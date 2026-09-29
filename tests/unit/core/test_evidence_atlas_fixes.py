"""ATLAS REQUEST-CHANGES regression suite for S09 (findings not covered elsewhere)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from magicite.config import Config
from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.errors import IdempotencyKeyConflictError
from magicite.obs import events as events_mod


@pytest.fixture(autouse=True)
def _clear_receipts():
    evidence_mod.clear_receipt_buffer()
    evidence_mod.reset_enqueue_counters()
    evidence_mod.set_checkpoint_fault_hook(None)
    yield
    evidence_mod.clear_receipt_buffer()
    evidence_mod.reset_enqueue_counters()
    evidence_mod.set_checkpoint_fault_hook(None)


def _decision_event(**overrides):
    base = dict(
        event_id="ev_atlas_001",
        decision_id="dec_atlas_001",
        event_type="decision",
        recorded_at="2026-09-29T12:00:00+00:00",
        candidate_ids=("skill_a",),
        chosen_action="skill_a",
        behavior_policy_id="dense-v1",
        behavior_policy_digest="digest_a",
        propensity=1.0,
        query_fingerprint="c" * 64,
        context_fingerprint="d" * 64,
        registry_fingerprint="e" * 64,
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        source_tier=0,
        retention_class="operational",
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


def _open_segment_lines(cfg: Config) -> list[str]:
    path = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _all_segment_records(cfg: Config) -> list[dict[str, Any]]:
    root = evidence_mod.evidence_dir(cfg) / "segments"
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_checkpoint_replayed_after_index_meta_wipe(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event()
    ack1 = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack1.replayed is False

    root = evidence_mod.evidence_dir(cfg)
    (root / "event_index.json").unlink(missing_ok=True)
    (root / "meta.json").unlink(missing_ok=True)

    ack2 = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack2.replayed is True
    assert ack2.sequence == ack1.sequence
    assert len(_open_segment_lines(cfg)) == 1


def test_checkpoint_no_duplicate_sequence_after_meta_rewind(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    e1 = _decision_event(event_id="ev_seq_a", decision_id="dec_a")
    e2 = _decision_event(event_id="ev_seq_b", decision_id="dec_b")
    ack1 = evidence_mod.checkpoint(cfg, db_conn, e1)
    evidence_mod.checkpoint(cfg, db_conn, e2)

    root = evidence_mod.evidence_dir(cfg)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    meta["last_sequence"] = ack1.sequence
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    e3 = _decision_event(event_id="ev_seq_c", decision_id="dec_c")
    evidence_mod.checkpoint(cfg, db_conn, e3)

    records = [r for r in _all_segment_records(cfg) if not r.get("deleted")]
    event_ids = [r["event"]["event_id"] for r in records]
    assert event_ids.count("ev_seq_a") == 1
    assert event_ids.count("ev_seq_b") == 1
    assert event_ids.count("ev_seq_c") == 1
    sequences = [int(r["sequence"]) for r in records]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))

    db_conn.execute("DELETE FROM evidence_event_projection")
    assert evidence_mod.rebuild_projections(cfg, db_conn) == 3


def test_checkpoint_crash_between_segment_and_index(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_crash_seg")

    def fault(label: str) -> None:
        if label == "after_segment":
            raise OSError("injected crash after segment, before index")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(OSError, match="injected crash"):
        evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.set_checkpoint_fault_hook(None)

    ack = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack.replayed is True
    assert len(_open_segment_lines(cfg)) == 1


def test_checkpoint_crash_between_index_and_meta(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_crash_meta")

    def fault(label: str) -> None:
        if label == "before_meta":
            raise OSError("injected crash after index, before meta")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(OSError, match="injected crash"):
        evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.set_checkpoint_fault_hook(None)

    ack = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack.replayed is True
    assert len(_open_segment_lines(cfg)) == 1


def test_rebuild_restores_index_so_load_event_works(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_rebuild_idx")
    evidence_mod.checkpoint(cfg, db_conn, event)

    root = evidence_mod.evidence_dir(cfg)
    (root / "event_index.json").unlink()
    (root / "meta.json").unlink(missing_ok=True)

    # load_event is segment-authoritative; rebuild must still restore caches.
    evidence_mod.rebuild_projections(cfg, db_conn)
    loaded = evidence_mod.load_event(cfg, event.event_id)
    assert loaded is not None
    assert loaded.event_id == event.event_id
    assert (root / "event_index.json").is_file()
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert int(meta["last_sequence"]) >= 1


def test_rebuild_fails_closed_on_conflicting_duplicate_digest(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_conflict_dup")
    evidence_mod.checkpoint(cfg, db_conn, event)

    seg = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    bad = {
        "sequence": 99,
        "segment_id": "00000001",
        "payload_digest": "0" * 64,
        "event": event.to_dict() | {"chosen_action": "skill_other"},
    }
    with seg.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(bad, sort_keys=True) + "\n")
    # Keep sibling manifest aligned so verify_segments does not mask the conflict.
    evidence_mod._rewrite_segment_manifest(seg, sealed=False)

    with pytest.raises(IdempotencyKeyConflictError):
        evidence_mod.rebuild_projections(cfg, db_conn)


def test_enqueue_with_failing_file_io_does_not_raise(cfg, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("disk unavailable")

    monkeypatch.setattr(fk, "load_or_create_fingerprint_key", boom)
    monkeypatch.setattr(Path, "open", boom)
    monkeypatch.setattr(os, "open", boom)

    receipt = evidence_mod.enqueue_decision_receipt(
        cfg,
        query_fingerprint="a" * 64,
        candidate_ids=["skill_a"],
        policy_id="dense-v1",
        policy_digest="d1",
    )
    assert receipt is None or isinstance(receipt, evidence_mod.DecisionReceipt)


def test_enqueue_never_calls_fingerprint_key_disk(cfg, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("enqueue must not touch fingerprint_key disk")

    monkeypatch.setattr(fk, "load_or_create_fingerprint_key", boom)
    monkeypatch.setattr(fk, "query_fingerprint", boom)

    receipt = evidence_mod.enqueue_decision_receipt(
        cfg,
        query_fingerprint="b" * 64,
        candidate_ids=["skill_a"],
        policy_id="dense-v1",
        policy_digest="d1",
    )
    assert receipt is not None
    assert receipt.query_fingerprint == "b" * 64


def test_delete_physically_erases_payload_sentinel(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "DELETE_SENTINEL_payload_xyz_UNIQUE"
    event = _decision_event(
        event_id="ev_erase_001",
        decision_id="dec_erase",
        behavior_policy_digest=sentinel,
        query_fingerprint=hashlib.sha256(b"fp").hexdigest(),
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    root = evidence_mod.evidence_dir(cfg)
    assert any(sentinel.encode() in p.read_bytes() for p in root.rglob("*") if p.is_file())

    tomb = evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="operator_erase")
    assert tomb["target_event_id"] == event.event_id
    assert "event" not in tomb

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "exports" in path.parts:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"sentinel survived in {path}"

    assert evidence_mod.load_event(cfg, event.event_id) is None
    export_dir = evidence_mod.export_evidence(cfg, event_ids=[event.event_id])
    export_blob = b"".join(p.read_bytes() for p in export_dir.rglob("*") if p.is_file())
    assert sentinel.encode() not in export_blob

    db_conn.execute("DELETE FROM evidence_event_projection")
    evidence_mod.rebuild_projections(cfg, db_conn)
    assert evidence_mod.load_event(cfg, event.event_id) is None

    tomb2 = evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="operator_erase")
    assert tomb2["target_event_id"] == event.event_id


def test_export_rehmac_consistent_within_unlinkable_across(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    fp = "f" * 64
    ctx = "1" * 64
    e1 = _decision_event(
        event_id="ev_exp_a",
        decision_id="dec_exp_a",
        query_fingerprint=fp,
        context_fingerprint=ctx,
    )
    e2 = _decision_event(
        event_id="ev_exp_b",
        decision_id="dec_exp_b",
        query_fingerprint=fp,
        context_fingerprint=ctx,
    )
    evidence_mod.checkpoint(cfg, db_conn, e1)
    evidence_mod.checkpoint(cfg, db_conn, e2)

    out1 = evidence_mod.export_evidence(cfg, event_ids=["ev_exp_a", "ev_exp_b"])
    rows1 = [
        json.loads(ln)
        for ln in (out1 / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(rows1) == 2
    assert rows1[0]["query_fingerprint"] == rows1[1]["query_fingerprint"]
    assert rows1[0]["context_fingerprint"] == rows1[1]["context_fingerprint"]
    assert rows1[0]["query_fingerprint"] != fp
    assert rows1[0]["context_fingerprint"] != ctx

    out2 = evidence_mod.export_evidence(cfg, event_ids=["ev_exp_a", "ev_exp_b"])
    rows2 = [
        json.loads(ln)
        for ln in (out2 / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert rows2[0]["query_fingerprint"] != rows1[0]["query_fingerprint"]
    assert rows2[0]["context_fingerprint"] != rows1[0]["context_fingerprint"]


def test_torn_tail_preserves_complete_lines(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    e1 = _decision_event(event_id="ev_torn_1", decision_id="dec_t1")
    e2 = _decision_event(event_id="ev_torn_2", decision_id="dec_t2")
    evidence_mod.checkpoint(cfg, db_conn, e1)
    evidence_mod.checkpoint(cfg, db_conn, e2)

    open_seg = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    with open_seg.open("ab") as fh:
        fh.write(b'{"sequence":999,"torn":')
        fh.flush()
        os.fsync(fh.fileno())

    evidence_mod._repair_torn_open_segment(evidence_mod.evidence_dir(cfg) / "segments")
    lines = _open_segment_lines(cfg)
    assert len(lines) == 2
    ids = {json.loads(ln)["event"]["event_id"] for ln in lines}
    assert ids == {"ev_torn_1", "ev_torn_2"}


def test_segment_rotation_seals_and_opens_new(cfg, db_conn, monkeypatch) -> None:
    cfg.ensure_dirs()
    monkeypatch.setattr(evidence_mod, "DEFAULT_SEGMENT_MAX_BYTES", 200)

    for i in range(6):
        evidence_mod.checkpoint(
            cfg,
            db_conn,
            _decision_event(
                event_id=f"ev_rot_{i}",
                decision_id=f"dec_rot_{i}",
                query_fingerprint=hashlib.sha256(f"rot{i}".encode()).hexdigest(),
            ),
        )

    segments = evidence_mod.evidence_dir(cfg) / "segments"
    sealed = list(segments.glob("[0-9]*.events.jsonl"))
    assert sealed, "expected at least one sealed segment after size rotation"
    for path in sealed:
        manifest = path.with_name(path.name.replace(".events.jsonl", ".manifest.json"))
        assert manifest.is_file()
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        assert meta.get("sealed") is True
        assert meta.get("sha256")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert meta["sha256"] == digest
    assert (segments / "open.events.jsonl").is_file()


def test_obs_events_redacts_raw_query_field() -> None:
    raw = {"query": "SECRET_USER_PROMPT", "k": 3}
    redacted = events_mod.redact_arguments(raw)
    assert redacted["query"] == events_mod.REDACTED_ARGUMENT
    assert redacted["k"] == 3
    assert "SECRET_USER_PROMPT" not in json.dumps(redacted)
    assert events_mod.args_digest(raw) == events_mod.args_digest(
        {"query": "OTHER_SECRET", "k": 3}
    )
