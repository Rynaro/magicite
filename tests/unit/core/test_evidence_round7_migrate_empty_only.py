"""Round-7: migrate must not re-auth truncated journals; backup expiry from Config."""

from __future__ import annotations

import json

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
        event_id="ev_r7_001",
        decision_id="dec_r7_001",
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


def test_migrate_refuses_after_incomplete_delete_truncate_attack(cfg, db_conn) -> None:
    """ATLAS: clear unsigned initialized flags + migrate must not resurrect."""
    cfg.ensure_dirs()
    sentinel = "R7_MIGRATE_RESURRECT_SENTINEL"
    event = _event(
        event_id="ev_r7_migrate_attack",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    def fault(label: str) -> None:
        if label == "after_tombstone":
            raise RuntimeError("injected after_tombstone")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_tombstone"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r7_attack")
    evidence_mod.set_checkpoint_fault_hook(None)

    root = evidence_mod.evidence_dir(cfg)
    # Incomplete delete left a live payload. Attacker truncates journal, drops MAC,
    # and clears unsigned tombstone_mac_initialized flags in every manifest.
    (root / "tombstones.jsonl").write_text("", encoding="utf-8")
    (root / "tombstones.mac").unlink(missing_ok=True)
    for mpath in (root / "segments").glob("*.manifest.json"):
        body = json.loads(mpath.read_text(encoding="utf-8"))
        body["tombstone_mac_initialized"] = False
        body.pop("tombstone_digest", None)
        body.pop("tombstone_count", None)
        mpath.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(InvalidInputError, match="segment|refus|empty|migrate"):
        evidence_mod.migrate_tombstone_mac(cfg, confirm=True)

    # Deleted event must never surface via load or export.
    with pytest.raises(InvalidInputError):
        evidence_mod.load_event(cfg, event.event_id)

    with pytest.raises(InvalidInputError):
        evidence_mod.export_evidence(cfg, db_conn, event_ids=[event.event_id])

    # And migrate must not have written a MAC that would authorize the empty journal.
    assert not (root / "tombstones.mac").is_file()


def test_migrate_allowed_only_on_completely_empty_ledger(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    root = evidence_mod._ensure_ledger_dirs(cfg)  # noqa: SLF001
    # No segment files yet — migrate may initialize.
    # Ensure no leftover mac from prior ensure under write guards in other tests.
    (root / "tombstones.mac").unlink(missing_ok=True)
    for p in (root / "segments").glob("*.events.jsonl"):
        p.unlink(missing_ok=True)
    for p in (root / "segments").glob("*.manifest.json"):
        p.unlink(missing_ok=True)
    (root / "event_index.json").unlink(missing_ok=True)
    (root / "tombstones.jsonl").unlink(missing_ok=True)

    result = evidence_mod.migrate_tombstone_mac(cfg, confirm=True)
    assert result["status"] == "ok"
    assert (root / "tombstones.mac").is_file()


def test_forged_meta_backup_expiry_ignored_uses_config(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    object.__setattr__(cfg, "evidence_backup_expiry_days", 42)
    evidence_mod.checkpoint(cfg, db_conn, _event(event_id="ev_r7_backup"))
    root = evidence_mod.evidence_dir(cfg)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    meta["backup_expiry_days"] = 1
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert evidence_mod.backup_expiry_days(cfg) == 42


def test_forged_open_segment_id_cannot_overwrite_sealed(cfg, db_conn) -> None:
    """meta.open_segment_id is unsigned — rotation must not clobber sealed files."""
    cfg.ensure_dirs()
    event1 = _event(event_id="ev_r7_rot_1", query_fingerprint="e" * 64)
    evidence_mod.checkpoint(cfg, db_conn, event1)
    root = evidence_mod.evidence_dir(cfg)
    open_path = root / "segments" / "open.events.jsonl"
    data = open_path.read_bytes()
    sealed = root / "segments" / "00000001.events.jsonl"
    sealed.write_bytes(data)
    evidence_mod._rewrite_segment_manifest(sealed, sealed=True)  # noqa: SLF001
    open_path.write_bytes(b"")
    evidence_mod._rewrite_segment_manifest(open_path, sealed=False)  # noqa: SLF001
    sealed_bytes = sealed.read_bytes()

    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    meta["open_segment_id"] = "00000001"
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    big = _event(
        event_id="ev_r7_rot_2",
        decision_id="dec_r7_rot_2",
        query_fingerprint="f" * 64,
        extra={"pad": "x" * 200},
    )
    evidence_mod.checkpoint(cfg, db_conn, big)
    evidence_mod._maybe_rotate_open_segment(root, max_bytes=1)  # noqa: SLF001

    assert sealed.read_bytes() == sealed_bytes
    assert (root / "segments" / "00000002.events.jsonl").is_file()
