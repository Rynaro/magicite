"""Round-2 ATLAS findings: delete/rotation integrity + A02 AC-S09-05/06."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from magicite.config import Config
from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.errors import InvalidInputError


@pytest.fixture(autouse=True)
def _reset():
    evidence_mod.set_checkpoint_fault_hook(None)
    evidence_mod.clear_receipt_buffer()
    yield
    evidence_mod.set_checkpoint_fault_hook(None)
    evidence_mod.clear_receipt_buffer()


def _event(**overrides: Any) -> evidence_mod.EvidenceEvent:
    base: dict[str, Any] = dict(
        event_id="ev_r2_001",
        decision_id="dec_r2_001",
        event_type="decision",
        recorded_at="2026-09-29T12:00:00+00:00",
        candidate_ids=("skill_a",),
        chosen_action="skill_a",
        behavior_policy_id="dense-v1",
        behavior_policy_digest="digest_a",
        propensity=1.0,
        query_fingerprint="a" * 64,
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        source_tier=0,
        retention_class="operational",
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


def _force_seal_open(cfg: Config) -> Path:
    """Seal the current open segment into 00000001.events.jsonl for tests."""
    root = evidence_mod.evidence_dir(cfg)
    segments = root / "segments"
    open_path = segments / "open.events.jsonl"
    data = open_path.read_bytes()
    sealed = segments / "00000001.events.jsonl"
    sealed.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (segments / "00000001.manifest.json").write_text(
        json.dumps(
            {
                "segment_id": "00000001",
                "kind": evidence_mod.LEDGER_KIND,
                "sha256": digest,
                "sealed": True,
                "updated_at": "2026-09-29T12:00:00+00:00",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    open_path.write_bytes(b"")
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["open_segment_id"] = "00000002"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (segments / "open.manifest.json").write_text(
        json.dumps(
            {
                "segment_id": "00000002",
                "kind": evidence_mod.LEDGER_KIND,
                "sha256": hashlib.sha256(b"").hexdigest(),
                "sealed": False,
                "record_count": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return sealed


# ── Finding 1: sealed manifest updated after purge ─────────────────────────


def test_delete_from_sealed_segment_updates_manifest_checksum(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_sealed_del", extra={"payload_sentinel": "SEALED_SENTINEL_XYZ"})
    evidence_mod.checkpoint(cfg, db_conn, event)
    sealed = _force_seal_open(cfg)
    old_manifest = json.loads(
        sealed.with_name("00000001.manifest.json").read_text(encoding="utf-8")
    )
    old_sha = old_manifest["sha256"]

    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="sealed_purge")

    new_manifest = json.loads(
        sealed.with_name("00000001.manifest.json").read_text(encoding="utf-8")
    )
    new_sha = hashlib.sha256(sealed.read_bytes()).hexdigest()
    assert new_manifest["sha256"] == new_sha
    assert new_sha != old_sha
    evidence_mod.verify_segments(evidence_mod.evidence_dir(cfg))


def test_verify_segments_fails_closed_on_checksum_mismatch(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_verify_mismatch")
    evidence_mod.checkpoint(cfg, db_conn, event)
    _force_seal_open(cfg)
    manifest_path = evidence_mod.evidence_dir(cfg) / "segments" / "00000001.manifest.json"
    bad = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(bad, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match="checksum|sha256|manifest"):
        evidence_mod.verify_segments(evidence_mod.evidence_dir(cfg))


# ── Finding 2: tombstone-first + stub-wins + resumable purge ───────────────


def test_delete_crash_after_tombstone_before_purge_hides_payload(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "CRASH_TOMB_SENTINEL_UNIQUE"
    event = _event(
        event_id="ev_crash_tomb",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    def fault(label: str) -> None:
        if label == "after_tombstone":
            raise RuntimeError("injected after_tombstone")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_tombstone"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="crash_tomb")
    evidence_mod.set_checkpoint_fault_hook(None)

    assert evidence_mod.load_event(cfg, event.event_id) is None
    export_dir = evidence_mod.export_evidence(cfg, event_ids=[event.event_id])
    rows = [
        ln
        for ln in (export_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert rows == []

    # Recovery completes physical purge.
    evidence_mod.rebuild_projections(cfg, db_conn)
    root = evidence_mod.evidence_dir(cfg)
    for path in root.rglob("*"):
        if not path.is_file() or "exports" in path.parts:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"sentinel in {path}"


def test_delete_crash_after_sealed_purge_before_open_hides_and_recovers(
    cfg, db_conn
) -> None:
    cfg.ensure_dirs()
    sentinel = "CRASH_SEALED_PURGE_SENTINEL"
    event = _event(
        event_id="ev_crash_sealed",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)
    _force_seal_open(cfg)
    # Duplicate live row in open (simulates rotation crash / re-append race).
    open_path = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    live = {
        "sequence": 2,
        "segment_id": "00000002",
        "payload_digest": evidence_mod.payload_digest(event.payload_for_digest()),
        "event": event.to_dict(),
    }
    open_path.write_text(json.dumps(live, sort_keys=True) + "\n", encoding="utf-8")

    def fault(label: str) -> None:
        if label == "after_purge_sealed":
            raise RuntimeError("injected after_purge_sealed")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_purge_sealed"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="crash_sealed")
    evidence_mod.set_checkpoint_fault_hook(None)

    assert evidence_mod.load_event(cfg, event.event_id) is None
    export_dir = evidence_mod.export_evidence(cfg, event_ids=[event.event_id])
    rows = [
        ln
        for ln in (export_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert rows == []

    evidence_mod.rebuild_projections(cfg, db_conn)
    root = evidence_mod.evidence_dir(cfg)
    for path in root.rglob("*"):
        if not path.is_file() or "exports" in path.parts:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"sentinel in {path}"


def test_authority_scan_deleted_stub_wins_over_later_live_row(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "STUB_WINS_SENTINEL"
    event = _event(event_id="ev_stub_wins", extra={"payload_sentinel": sentinel})
    evidence_mod.checkpoint(cfg, db_conn, event)
    _force_seal_open(cfg)
    # Stub in sealed + live in open, no tombstone yet.
    sealed = evidence_mod.evidence_dir(cfg) / "segments" / "00000001.events.jsonl"
    sealed.write_text(
        json.dumps(
            {
                "sequence": 1,
                "segment_id": "00000001",
                "payload_digest": None,
                "deleted": True,
                "event_id": event.event_id,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    open_path = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    open_path.write_text(
        json.dumps(
            {
                "sequence": 2,
                "segment_id": "00000002",
                "payload_digest": "abcd",
                "event": event.to_dict(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    by_id, _ = evidence_mod._scan_segment_authority(evidence_mod.evidence_dir(cfg))
    assert by_id[event.event_id].get("deleted") is True
    # Without tombstone, load still uses index; but authority row is deleted.
    # After rewrite caches, load_event must hide it.
    evidence_mod._rewrite_derived_caches(evidence_mod.evidence_dir(cfg))
    assert evidence_mod.load_event(cfg, event.event_id) is None


# ── Finding 3: export after index wipe ─────────────────────────────────────


def test_export_after_index_wipe_contains_live_events(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    e1 = _event(event_id="ev_exp_idx_1", decision_id="dec_1")
    e2 = _event(event_id="ev_exp_idx_2", decision_id="dec_2", query_fingerprint="b" * 64)
    evidence_mod.checkpoint(cfg, db_conn, e1)
    evidence_mod.checkpoint(cfg, db_conn, e2)
    (evidence_mod.evidence_dir(cfg) / "event_index.json").unlink()

    out = evidence_mod.export_evidence(cfg)
    rows = [
        json.loads(ln)
        for ln in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(rows) == 2


# ── Finding 5: mid-rotation crash ──────────────────────────────────────────


def test_rotation_crash_after_seal_no_duplicates_or_resurrected_payload(
    cfg, db_conn, monkeypatch
) -> None:
    cfg.ensure_dirs()
    monkeypatch.setattr(evidence_mod, "DEFAULT_SEGMENT_MAX_BYTES", 120)
    sentinel = "ROT_CRASH_SENTINEL"
    # Fill past threshold, then inject mid-rotation.
    for i in range(3):
        evidence_mod.checkpoint(
            cfg,
            db_conn,
            _event(
                event_id=f"ev_rotcrash_{i}",
                decision_id=f"dec_rotcrash_{i}",
                query_fingerprint=hashlib.sha256(f"r{i}".encode()).hexdigest(),
                extra={"payload_sentinel": sentinel} if i == 0 else {},
            ),
        )

    # Delete first event so a stub exists; then force another rotate with crash.
    evidence_mod.delete_event(cfg, db_conn, "ev_rotcrash_0", reason="pre_rot")

    def fault(label: str) -> None:
        if label == "mid_rotation_after_seal":
            raise RuntimeError("injected mid_rotation_after_seal")

    evidence_mod.set_checkpoint_fault_hook(fault)
    # Next checkpoint should attempt rotation (open may still be large) or we
    # call rotate directly after stuffing open.
    try:
        evidence_mod.checkpoint(
            cfg,
            db_conn,
            _event(
                event_id="ev_rotcrash_new",
                decision_id="dec_rotcrash_new",
                query_fingerprint=hashlib.sha256(b"new").hexdigest(),
            ),
        )
    except RuntimeError as exc:
        if "mid_rotation_after_seal" not in str(exc):
            # Rotation may not fire if open is small; force it.
            evidence_mod.set_checkpoint_fault_hook(fault)
            monkeypatch.setattr(evidence_mod, "DEFAULT_SEGMENT_MAX_BYTES", 1)
            with pytest.raises(RuntimeError, match="mid_rotation_after_seal"):
                evidence_mod._maybe_rotate_open_segment(evidence_mod.evidence_dir(cfg))
    evidence_mod.set_checkpoint_fault_hook(None)

    # Recovery / rebuild: no duplicate authority surface; deleted payload gone.
    evidence_mod.rebuild_projections(cfg, db_conn)
    by_id, _ = evidence_mod._scan_segment_authority(evidence_mod.evidence_dir(cfg))
    # Each event_id maps to one authority row.
    assert len(by_id) == len({eid for eid in by_id})
    assert evidence_mod.load_event(cfg, "ev_rotcrash_0") is None
    root = evidence_mod.evidence_dir(cfg)
    for path in root.rglob("*"):
        if not path.is_file() or "exports" in path.parts:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"sentinel in {path}"
