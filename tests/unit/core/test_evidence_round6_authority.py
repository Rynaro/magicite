"""Round-6: no heal-once, pending-aware marker, sealed corrupt fail-closed, config retention."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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


def _event(**overrides):
    base = dict(
        event_id="ev_r6_001",
        decision_id="dec_r6_001",
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


def _force_seal_open(cfg) -> Path:
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
                "kind": "EvidenceLedger/1",
                "sha256": digest,
                "sealed": True,
                "record_count": sum(1 for ln in data.splitlines() if ln.strip()),
                "tombstone_mac_initialized": True,
                "tombstone_digest": evidence_mod._tombstone_set_digest(root),  # noqa: SLF001
            }
        ),
        encoding="utf-8",
    )
    open_path.write_bytes(b"")
    (segments / "open.manifest.json").write_text(
        json.dumps(
            {
                "segment_id": "00000002",
                "kind": "EvidenceLedger/1",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "sealed": False,
                "record_count": 0,
                "tombstone_mac_initialized": True,
                "tombstone_digest": evidence_mod._tombstone_set_digest(root),  # noqa: SLF001
            }
        ),
        encoding="utf-8",
    )
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["open_segment_id"] = "00000002"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return sealed


# ── Finding 1: no heal-once on missing tombstones.mac ──────────────────────


def test_missing_mac_after_truncate_fails_closed_not_healed(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "R6_HEAL_ONCE_SENTINEL"
    event = _event(
        event_id="ev_r6_heal",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    def fault(label: str) -> None:
        if label == "after_tombstone":
            raise RuntimeError("injected after_tombstone")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_tombstone"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r6_heal")
    evidence_mod.set_checkpoint_fault_hook(None)

    root = evidence_mod.evidence_dir(cfg)
    (root / "tombstones.mac").unlink(missing_ok=True)
    (root / "tombstones.jsonl").write_text("", encoding="utf-8")

    # Lease-held mutation must NOT heal and re-auth the empty journal.
    with pytest.raises(InvalidInputError, match="tombstones\\.mac|MAC|initialized|fail"):
        evidence_mod.checkpoint(
            cfg,
            db_conn,
            _event(
                event_id="ev_r6_heal_trig",
                decision_id="dec_r6_heal_trig",
                query_fingerprint="b" * 64,
            ),
        )

    with pytest.raises(InvalidInputError, match="tombstones\\.mac|MAC|initialized|fail"):
        evidence_mod.load_event(cfg, event.event_id)

    with pytest.raises(InvalidInputError, match="tombstones\\.mac|MAC|initialized|fail"):
        evidence_mod.export_evidence(cfg, db_conn, event_ids=[event.event_id])


def test_migrate_tombstone_mac_refused_when_already_initialized(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_r6_mig")
    evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r6_mig")
    root = evidence_mod.evidence_dir(cfg)
    (root / "tombstones.mac").unlink(missing_ok=True)
    with pytest.raises(InvalidInputError, match="initialized|refus|migrate"):
        evidence_mod.migrate_tombstone_mac(cfg, confirm=True)


# ── Finding 2: valid marker cannot hide pending payloads ───────────────────


def test_mac_valid_marker_with_pending_payload_still_purges(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "R6_PENDING_MARKER_SENTINEL"
    event = _event(
        event_id="ev_r6_pending",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    def fault(label: str) -> None:
        if label == "after_tombstone":
            raise RuntimeError("injected after_tombstone")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_tombstone"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r6_pending")
    evidence_mod.set_checkpoint_fault_hook(None)

    root = evidence_mod.evidence_dir(cfg)
    # Write a *valid* MAC'd marker claiming purge_complete while payload is pending.
    body = {
        "purge_complete": True,
        "pending_count": 0,
        "generation": evidence_mod._tombstone_generation(root),  # noqa: SLF001
        "tombstone_digest": evidence_mod._tombstone_set_digest(root),  # noqa: SLF001
        "manifest_digest": evidence_mod._segment_manifest_digest(root),  # noqa: SLF001
        "mac_scheme": evidence_mod._PURGE_MARKER_MAC_SCHEME,  # noqa: SLF001
        "updated_at": "2026-09-29T12:00:00+00:00",
    }
    body["mac"] = evidence_mod._purge_marker_mac(cfg, body)  # noqa: SLF001
    (root / "purge_complete.marker").write_text(json.dumps(body), encoding="utf-8")

    evidence_mod.checkpoint(
        cfg,
        db_conn,
        _event(
            event_id="ev_r6_pending_trig",
            decision_id="dec_r6_pending_trig",
            query_fingerprint="c" * 64,
        ),
    )
    for path in root.rglob("*.events.jsonl"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes(), f"sentinel in {path}"


# ── Finding 3: corrupt stub must not let live twin win ─────────────────────


def test_corrupt_stub_in_sealed_segment_never_returns_live(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(
        event_id="ev_r6_corrupt_stub",
        extra={"payload_sentinel": "LIVE_SHOULD_NOT_SURFACE"},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)
    sealed = _force_seal_open(cfg)

    live_line = sealed.read_text(encoding="utf-8").strip().splitlines()[0]
    # Plant a corrupt line inside the sealed segment. Permissive parsers that
    # skip it would still surface LIVE; sealed segments must fail closed.
    sealed.write_text(live_line + "\n{not-json-deleted-stub\n", encoding="utf-8")
    new_bytes = sealed.read_bytes()
    (sealed.parent / "00000001.manifest.json").write_text(
        json.dumps(
            {
                "segment_id": "00000001",
                "kind": "EvidenceLedger/1",
                "sha256": hashlib.sha256(new_bytes).hexdigest(),
                "sealed": True,
                "record_count": sum(1 for ln in new_bytes.splitlines() if ln.strip()),
                "tombstone_mac_initialized": True,
                "tombstone_digest": evidence_mod._tombstone_set_digest(  # noqa: SLF001
                    evidence_mod.evidence_dir(cfg)
                ),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidInputError, match="corrupt|sealed|JSON|fail"):
        evidence_mod.load_event(cfg, event.event_id)


# ── Finding 4: retention from Config, not meta.json ────────────────────────


def test_forged_meta_retention_ignored_uses_config(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    object.__setattr__(cfg, "evidence_retention_operational_days", 30)
    object.__setattr__(cfg, "evidence_retention_audit_days", 90)

    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    event = _event(
        event_id="ev_r6_ret_audit",
        retention_class="audit",
        recorded_at=old,
        query_fingerprint="d" * 64,
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    root = evidence_mod.evidence_dir(cfg)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    meta["retention_audit_days"] = 0
    meta["retention_operational_days"] = 0
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    deleted = evidence_mod.apply_retention(cfg, db_conn, now=datetime.now(UTC))
    assert event.event_id not in deleted
    assert evidence_mod.load_event(cfg, event.event_id) is not None
