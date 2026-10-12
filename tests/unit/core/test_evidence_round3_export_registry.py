"""Round-3: export-registry path allowlist, resumable purge, fail-closed registry."""

from __future__ import annotations

import hashlib
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
        event_id="ev_r3_001",
        decision_id="dec_r3_001",
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


# ── Finding 1: allowlisted, file-level, MAC'd registry ─────────────────────


def test_poisoned_registry_outside_roots_leaves_victim_untouched(cfg, db_conn, tmp_path) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_poison")
    evidence_mod.checkpoint(cfg, db_conn, event)

    victim = tmp_path / "VICTIM_DO_NOT_DELETE.txt"
    victim.write_text("precious\n", encoding="utf-8")

    # Plant a poisoned registry pointing at the victim (no valid MAC).
    root = evidence_mod.evidence_dir(cfg)
    (root / "registered_exports.json").write_text(
        json.dumps(
            {
                "kind": "EvidenceExportRegistry/1",
                "exports": [
                    {
                        "path": str(victim.resolve()),
                        "sha256": hashlib.sha256(b"precious\n").hexdigest(),
                        "size": victim.stat().st_size,
                        "st_dev": victim.stat().st_dev,
                        "st_ino": victim.stat().st_ino,
                        "exported_at": "2026-09-29T12:00:00+00:00",
                    }
                ],
                "dirs": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidInputError):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="poison")

    assert victim.is_file()
    assert victim.read_text(encoding="utf-8") == "precious\n"


def test_symlinked_export_dir_is_refused(cfg, db_conn, tmp_path) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_symlink_export")
    evidence_mod.checkpoint(cfg, db_conn, event)

    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    link_dir = tmp_path / "link_export"
    link_dir.symlink_to(real_dir)

    # Even if configured as an allowed root, the symlink itself is refused.
    object.__setattr__(cfg, "evidence_export_roots", (str(real_dir.resolve()),))

    with pytest.raises(InvalidInputError, match="symlink|refusing"):
        evidence_mod.export_evidence(
            cfg, db_conn, event_ids=[event.event_id], export_dir=link_dir
        )

    assert list(real_dir.iterdir()) == []


def test_registry_entry_with_changed_bytes_is_not_deleted(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_changed_bytes")
    evidence_mod.checkpoint(cfg, db_conn, event)
    out = evidence_mod.export_evidence(cfg, db_conn, event_ids=[event.event_id])
    target = out / "events.jsonl"
    assert target.is_file()
    original = target.read_bytes()

    # Tamper file after registration.
    target.write_bytes(original + b"\nTAMPERED")

    result = evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="tamper")
    assert target.is_file(), "tampered file must not be unlinked"
    skipped = result.get("export_purge_skipped") or result.get("purge_skipped") or []
    assert skipped, "mismatch must be reported in deletion result"


def test_valid_managed_export_files_are_deleted(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_managed_ok")
    evidence_mod.checkpoint(cfg, db_conn, event)
    out = evidence_mod.export_evidence(cfg, db_conn, event_ids=[event.event_id])
    events_path = out / "events.jsonl"
    manifest_path = out / "manifest.json"
    assert events_path.is_file() and manifest_path.is_file()

    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="managed_ok")
    assert not events_path.exists()
    assert not manifest_path.exists()


def test_registry_never_rmtrees_directory_entries(cfg, db_conn, tmp_path) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_no_rmtree")
    evidence_mod.checkpoint(cfg, db_conn, event)

    # Configure an allowed root and export into it (real dir, not symlink).
    allowed = tmp_path / "allowed_exports"
    allowed.mkdir()
    object.__setattr__(cfg, "evidence_export_roots", (str(allowed.resolve()),))
    out = evidence_mod.export_evidence(
        cfg, db_conn, event_ids=[event.event_id], export_dir=allowed / "exp1"
    )
    # Plant an extra nested dir that must not be rmtree'd if somehow registered as dir.
    nested = out / "nested_keep"
    nested.mkdir()
    keep = nested / "operator.txt"
    keep.write_text("keep\n", encoding="utf-8")

    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="no_rmtree")
    # Magicite-written files gone; operator nested content must survive (not rmtree).
    assert keep.is_file()
    assert keep.read_text(encoding="utf-8") == "keep\n"


def test_export_outside_allowed_roots_is_refused(cfg, db_conn, tmp_path) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_refuse_out")
    evidence_mod.checkpoint(cfg, db_conn, event)
    outside = tmp_path / "unconfigured_out"
    with pytest.raises(InvalidInputError, match="allowed|outside|refus"):
        evidence_mod.export_evidence(
            cfg, db_conn, event_ids=[event.event_id], export_dir=outside
        )


# ── Finding 2: repurge on every lease-held mutation ────────────────────────


def test_checkpoint_after_tombstone_crash_completes_physical_purge(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "R3_RESIDUAL_SENTINEL_UNIQUE"
    event = _event(
        event_id="ev_r3_residual",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    def fault(label: str) -> None:
        if label == "after_tombstone":
            raise RuntimeError("injected after_tombstone")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_tombstone"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r3_residual")
    evidence_mod.set_checkpoint_fault_hook(None)

    # Payload may still be on disk until next lease-held mutation.
    root = evidence_mod.evidence_dir(cfg)
    assert any(
        sentinel.encode() in p.read_bytes()
        for p in root.rglob("*.events.jsonl")
        if p.is_file()
    )

    # Next checkpoint (unrelated event) must resume physical purge.
    evidence_mod.checkpoint(
        cfg,
        db_conn,
        _event(
            event_id="ev_r3_trigger",
            decision_id="dec_r3_trigger",
            query_fingerprint="b" * 64,
        ),
    )
    for path in root.rglob("*"):
        if not path.is_file() or "exports" in path.parts:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"sentinel in {path}"


# ── Finding 3: corrupt registry fails closed ───────────────────────────────


def test_corrupt_registered_exports_fails_closed_on_delete(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_corrupt_reg")
    evidence_mod.checkpoint(cfg, db_conn, event)
    root = evidence_mod.evidence_dir(cfg)
    (root / "registered_exports.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(InvalidInputError, match="registry|export|JSON|corrupt|mac|HMAC"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="corrupt_reg")
