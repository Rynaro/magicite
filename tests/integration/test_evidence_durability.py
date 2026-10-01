"""ATLAS review fixes: crash-atomic checkpoint, rebuild, fencing, privacy."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
from typing import Any

import pytest

from magicite.config import Config
from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.errors import IdempotencyKeyConflictError
from magicite.storage import db as db_mod


def _decision_event(**overrides: Any) -> evidence_mod.EvidenceEvent:
    base: dict[str, Any] = dict(
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
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        context_fingerprint="d" * 64,
        source_tier=0,
        retention_class="operational",
        extra={"payload_sentinel": "PAYLOAD_SENTINEL_FOR_DELETE"},
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


def _segment_lines(cfg: Config) -> list[str]:
    path = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _count_event_id_in_segment(cfg: Config, event_id: str) -> int:
    n = 0
    for ln in _segment_lines(cfg):
        row = json.loads(ln)
        eid = row.get("event", {}).get("event_id") or row.get("event_id")
        if eid == event_id:
            n += 1
    return n


@pytest.fixture(autouse=True)
def _reset_hooks():
    evidence_mod.set_checkpoint_fault_hook(None)
    evidence_mod.clear_receipt_buffer()
    evidence_mod.reset_enqueue_counters()
    yield
    evidence_mod.set_checkpoint_fault_hook(None)
    evidence_mod.clear_receipt_buffer()
    evidence_mod.reset_enqueue_counters()


# ── 1. Crash-atomic checkpoint ─────────────────────────────────────────────


def test_checkpoint_retry_after_segment_fsync_zero_duplicates(cfg, db_conn) -> None:
    """Crash after segment fsync: wipe index+meta; retry must replay, not append."""
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_crash_seg")

    def fault(label: str) -> None:
        if label == "after_segment":
            raise RuntimeError("injected crash after_segment")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_segment"):
        evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.set_checkpoint_fault_hook(None)

    root = evidence_mod.evidence_dir(cfg)
    assert (root / "segments" / "open.events.jsonl").is_file()
    # Simulate index+meta never committed.
    (root / "event_index.json").unlink(missing_ok=True)
    (root / "meta.json").unlink(missing_ok=True)

    ack = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack.replayed is True
    assert ack.durable is True
    assert _count_event_id_in_segment(cfg, event.event_id) == 1
    assert evidence_mod.load_event(cfg, event.event_id) is not None


def test_checkpoint_retry_after_index_before_meta_contiguous(cfg, db_conn) -> None:
    """Crash after index write before meta: next event must not reuse sequence."""
    cfg.ensure_dirs()
    first = _decision_event(event_id="ev_crash_idx", decision_id="dec_idx")

    def fault(label: str) -> None:
        if label == "before_meta":
            raise RuntimeError("injected crash before_meta")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="before_meta"):
        evidence_mod.checkpoint(cfg, db_conn, first)
    evidence_mod.set_checkpoint_fault_hook(None)

    # Rewind meta as if the meta write never landed.
    root = evidence_mod.evidence_dir(cfg)
    evidence_mod._atomic_write_json(  # noqa: SLF001 — intentional crash-matrix setup
        root / "meta.json",
        {
            "kind": evidence_mod.LEDGER_KIND,
            "ledger_version": evidence_mod.LEDGER_KIND,
            "last_sequence": 0,
            "open_segment_id": "00000001",
            "redaction_version": 1,
        },
    )

    ack_replay = evidence_mod.checkpoint(cfg, db_conn, first)
    assert ack_replay.replayed is True
    assert _count_event_id_in_segment(cfg, first.event_id) == 1

    second = _decision_event(event_id="ev_crash_idx_2", decision_id="dec_idx_2")
    ack2 = evidence_mod.checkpoint(cfg, db_conn, second)
    assert ack2.replayed is False
    assert ack2.sequence == ack_replay.sequence + 1
    # Contiguous non-deleted event sequences.
    live_seqs = []
    for ln in _segment_lines(cfg):
        row = json.loads(ln)
        if row.get("deleted"):
            continue
        live_seqs.append(int(row["sequence"]))
    assert live_seqs == sorted(live_seqs)
    assert len(live_seqs) == len(set(live_seqs))


def test_checkpoint_conflict_duplicate_event_id_fails_closed(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_conflict")
    evidence_mod.checkpoint(cfg, db_conn, event)
    conflict = _decision_event(event_id="ev_conflict", chosen_action="other")
    with pytest.raises(IdempotencyKeyConflictError):
        evidence_mod.checkpoint(cfg, db_conn, conflict)
    assert _count_event_id_in_segment(cfg, "ev_conflict") == 1


# ── 2. Rebuild restores read authority ─────────────────────────────────────


def test_rebuild_restores_index_and_load_event(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_rebuild_idx")
    evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="test_hide")

    kept = _decision_event(event_id="ev_rebuild_keep", decision_id="dec_keep")
    evidence_mod.checkpoint(cfg, db_conn, kept)

    root = evidence_mod.evidence_dir(cfg)
    (root / "event_index.json").unlink()
    # load_event must fail or miss before rebuild if index is authority-cache
    # After rebuild, index+meta reconstructed from segment.
    evidence_mod.rebuild_projections(cfg, db_conn)

    assert (root / "event_index.json").is_file()
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert int(meta["last_sequence"]) >= 2
    assert evidence_mod.load_event(cfg, kept.event_id) is not None
    assert evidence_mod.load_event(cfg, event.event_id) is None  # tombstoned stays hidden


# ── 3. Cross-process fencing ────────────────────────────────────────────────


def _mp_checkpoint_worker(project_root, event_id, ready, start, results, custody_directory, registry_id):
    from tests.support.custody_adapter import attach_fixture

    with attach_fixture(Path(project_root), Path(custody_directory), registry_id):
        _mp_checkpoint_worker_attached(project_root, event_id, ready, start, results)


def _mp_checkpoint_worker_attached(
    project_root: str,
    event_id: str,
    ready: Any,
    start: Any,
    results: Any,
) -> None:
    cfg = Config.load(Path(project_root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    conn = db_mod.connect(cfg.db_path)
    try:
        ready.set()
        if not start.wait(timeout=10):
            results.put({"event_id": event_id, "status": "timeout"})
            return
        event = evidence_mod.EvidenceEvent(
            event_id=event_id,
            decision_id=f"dec_{event_id}",
            event_type="decision",
            recorded_at="2026-09-29T12:00:00+00:00",
            candidate_ids=("skill_a",),
            chosen_action="skill_a",
            behavior_policy_id="dense-v1",
            behavior_policy_digest="digest_a",
            propensity=1.0,
            query_fingerprint="e" * 64,
            fingerprint_scheme=fk.FINGERPRINT_SCHEME,
            source_tier=0,
            retention_class="operational",
        )
        from magicite.errors import BusyError

        # CrossProcessLease is fail-fast; contenders retry until both land.
        last_err: str | None = None
        for _ in range(80):
            try:
                ack = evidence_mod.checkpoint(cfg, conn, event)
                results.put(
                    {
                        "event_id": event_id,
                        "status": "ok",
                        "sequence": ack.sequence,
                        "replayed": ack.replayed,
                    }
                )
                return
            except BusyError as exc:
                last_err = str(exc)
                import time

                time.sleep(0.05)
                continue
            except Exception as exc:  # noqa: BLE001 — surface to parent
                results.put({"event_id": event_id, "status": "error", "error": str(exc)})
                return
        results.put(
            {
                "event_id": event_id,
                "status": "error",
                "error": last_err or "lease timeout",
            }
        )
    finally:
        conn.close()


def test_multiprocess_concurrent_checkpoint_unique_sequences(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    # Touch DB schema / lease table in parent before spawn.
    assert db_mod.schema_version(db_conn) >= 6

    from magicite.core import writer_guard

    provider = writer_guard.resolve_custody(cfg)[1]
    ctx = mp.get_context("spawn")
    ready1, ready2 = ctx.Event(), ctx.Event()
    start = ctx.Event()
    results: Any = ctx.Queue()
    workers = [
        ctx.Process(
            target=_mp_checkpoint_worker,
            args=(
                str(cfg.project_root),
                "ev_mp_a",
                ready1,
                start,
                results,
                str(provider.store.directory),
                provider.registry_id,
            ),
        ),
        ctx.Process(
            target=_mp_checkpoint_worker,
            args=(
                str(cfg.project_root),
                "ev_mp_b",
                ready2,
                start,
                results,
                str(provider.store.directory),
                provider.registry_id,
            ),
        ),
    ]
    for w in workers:
        w.start()
    assert ready1.wait(timeout=10) and ready2.wait(timeout=10)
    start.set()
    for w in workers:
        w.join(timeout=30)
        assert w.exitcode == 0

    rows = [results.get(timeout=5), results.get(timeout=5)]
    assert all(r["status"] == "ok" for r in rows), rows
    seqs = sorted(int(r["sequence"]) for r in rows)
    assert len(seqs) == 2
    assert seqs[0] != seqs[1]
    assert _count_event_id_in_segment(cfg, "ev_mp_a") == 1
    assert _count_event_id_in_segment(cfg, "ev_mp_b") == 1


# ── 4. Hot-path enqueue ─────────────────────────────────────────────────────


def test_enqueue_never_raises_with_unreadable_key_dir(cfg, monkeypatch) -> None:
    cfg.ensure_dirs()

    def boom(*_a: Any, **_k: Any) -> bytes:
        raise OSError("key dir unreadable")

    monkeypatch.setattr(
        evidence_mod.fingerprint_key_mod,
        "load_or_create_fingerprint_key",
        boom,
    )
    # Must not call key loader; missing fingerprint still must not raise.
    receipt = evidence_mod.enqueue_decision_receipt(
        cfg,
        candidate_ids=["skill_a"],
        policy_id="dense-v1",
        policy_digest="d1",
        source_tier=0,
    )
    assert receipt is not None
    assert receipt.query_fingerprint is None
    assert evidence_mod.enqueue_missing_fingerprint_count() >= 1


# ── 5. Physical deletion ────────────────────────────────────────────────────


def test_delete_physically_removes_payload_bytes(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "PAYLOAD_SENTINEL_FOR_DELETE"
    event = _decision_event(event_id="ev_phys_del", extra={"payload_sentinel": sentinel})
    evidence_mod.checkpoint(cfg, db_conn, event)
    root = evidence_mod.evidence_dir(cfg)
    assert any(sentinel.encode() in p.read_bytes() for p in root.rglob("*") if p.is_file())

    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="privacy_delete")

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "exports" in path.parts:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"payload remains in {path}"

    assert evidence_mod.load_event(cfg, event.event_id) is None
    export_dir = evidence_mod.export_evidence(cfg)
    export_blob = b"".join(p.read_bytes() for p in export_dir.rglob("*") if p.is_file())
    assert sentinel.encode() not in export_blob

    evidence_mod.rebuild_projections(cfg, db_conn)
    assert evidence_mod.load_event(cfg, event.event_id) is None

    # Sequences remain unique/valid for a subsequent append.
    nxt = _decision_event(event_id="ev_after_del", decision_id="dec_after", extra={})
    ack = evidence_mod.checkpoint(cfg, db_conn, nxt)
    assert ack.sequence >= 1


# ── 6. Export unlinkability ─────────────────────────────────────────────────


def test_two_exports_are_unlinkable(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _decision_event(
        event_id="ev_export_u",
        query_fingerprint="f" * 64,
        context_fingerprint="g" * 64,
    )
    evidence_mod.checkpoint(cfg, db_conn, event)
    a = evidence_mod.export_evidence(
        cfg, event_ids=["ev_export_u"], export_dir=evidence_mod.evidence_dir(cfg) / "exports" / "ex_a"
    )
    b = evidence_mod.export_evidence(
        cfg, event_ids=["ev_export_u"], export_dir=evidence_mod.evidence_dir(cfg) / "exports" / "ex_b"
    )
    row_a = json.loads((a / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    row_b = json.loads((b / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    correlators = (
        "query_fingerprint",
        "context_fingerprint",
        "registry_fingerprint",
        "model_fingerprint",
        "config_fingerprint",
        "event_id",
        "decision_id",
    )
    for key in correlators:
        va, vb = row_a.get(key), row_b.get(key)
        if va is None and vb is None:
            continue
        assert va != vb, f"correlator {key} shared across exports: {va!r}"
        assert va != "f" * 64
        assert va != "g" * 64


# ── 7. Torn-line repair ─────────────────────────────────────────────────────


def test_torn_line_keeps_complete_lines(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    e1 = _decision_event(event_id="ev_torn_1", decision_id="dec_torn_1", extra={})
    e2 = _decision_event(event_id="ev_torn_2", decision_id="dec_torn_2", extra={})
    evidence_mod.checkpoint(cfg, db_conn, e1)
    evidence_mod.checkpoint(cfg, db_conn, e2)
    open_seg = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    before = open_seg.read_bytes()
    assert before.endswith(b"\n")
    with open_seg.open("ab") as fh:
        fh.write(b'{"sequence":999,"torn":true')
        fh.flush()
        os.fsync(fh.fileno())

    evidence_mod._repair_torn_open_segment(  # noqa: SLF001
        evidence_mod.evidence_dir(cfg) / "segments"
    )
    after = open_seg.read_bytes()
    assert after.endswith(b"\n")
    assert b'"torn":true' not in after
    lines = [ln for ln in after.decode().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["event"]["event_id"] == "ev_torn_1"
    assert json.loads(lines[1])["event"]["event_id"] == "ev_torn_2"
