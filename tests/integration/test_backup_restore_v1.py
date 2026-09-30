"""S12 backup / restore acceptance (AC-S12-02, AC-S12-03, AC-S12-05)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import backup as backup_mod
from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.errors import InvalidInputError
from magicite.obs import doctor as doctor_mod
from magicite.storage import db as db_mod

pytestmark = pytest.mark.acceptance


def _decision_event(**overrides):
    base = dict(
        event_id="ev_s12_001",
        decision_id="dec_s12_001",
        event_type="decision",
        recorded_at="2026-09-29T12:00:00+00:00",
        candidate_ids=("skill_a",),
        chosen_action="skill_a",
        behavior_policy_id="dense-v1",
        behavior_policy_digest="digest_a",
        propensity=1.0,
        query_fingerprint="b" * 64,
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        source_tier=0,
        retention_class="operational",
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


def _seed_registry(cfg: Config, conn) -> tuple[str, str]:
    embedder = get_embedder(dim=256)
    outcome = registry_mod.register(cfg, conn, embedder, path=".magicite/engrams")
    assert outcome.ingested >= 1
    entry = outcome.registered[0]
    digest = conn.execute(
        "SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)
    ).fetchone()["content_sha256"]
    return entry.id, digest


def test_complete_restore(project_root: Path, tmp_path: Path) -> None:
    """AC-S12-02: acknowledged durable records through recovery point recovered."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        engram_id, digest = _seed_registry(cfg, conn)
        registry_mod.review_approve(
            cfg,
            conn,
            engram_id=engram_id,
            expected_digest=digest,
            actor="reviewer",
            event_id="evt-s12-admit",
        )
        key = fk.load_or_create_fingerprint_key(cfg)
        fp = fk.query_fingerprint("s12-query", key=key)
        event = _decision_event(query_fingerprint=fp)
        ack = evidence_mod.checkpoint(cfg, conn, event)
        assert ack.durable is True

        # Approvals mirror via review_approve already.
        backup_dir = tmp_path / "backup-complete"
        snap = backup_mod.create_snapshot(cfg, conn, backup_dir)
        assert snap["manifest_kind"] == "backup/1"
        assert snap["recovery_point_sequences"]["evidence"] >= ack.sequence
        assert "runtime/fingerprint.key" not in {
            e["path"] for e in snap["files"]
        }

        overlay = backup_mod.build_recovery_overlay(
            cfg,
            control_sequence=max(1, snap["recovery_point_sequences"]["evidence"]),
            operator_provenance="test-complete-restore",
            key=key,
        )
        anchor = backup_mod.issue_sequence_anchor(overlay, key=key)

        # Destroy live authoritative stores + projection DB.
        shutil.rmtree(evidence_mod.evidence_dir(cfg), ignore_errors=True)
        shutil.rmtree(trust_mod.trust_dir(cfg), ignore_errors=True)
        for path in list(cfg.approvals_dir.glob("*.json")) if cfg.approvals_dir.is_dir() else []:
            path.unlink()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(cfg.db_path) + suffix) if suffix else cfg.db_path
            p.unlink(missing_ok=True)

        # Fresh connection after DB wipe.
        conn.close()
        conn = db_mod.connect(cfg.db_path)
        # Restore fingerprint key so overlay + tombstone MAC verify (custody).
        fk_path = fk.fingerprint_key_path(cfg)
        fk_path.parent.mkdir(parents=True, exist_ok=True)
        fk_path.write_bytes(key)

        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=overlay,
            sequence_anchor=anchor,
            custody_key=key,
            preserve_live_overlay=False,
        )
        assert result["status"] == "ok"
        assert result["reconciliation_required"] is False

        loaded = evidence_mod.load_event(cfg, event.event_id)
        assert loaded is not None
        assert loaded.decision_id == event.decision_id
        decisions = [
            d
            for d in trust_mod.list_decisions(cfg)
            if d.engram_id == engram_id and d.decision == "admit"
        ]
        assert len(decisions) >= 1
        assert backup_mod.is_reconciliation_required(cfg) is False
    finally:
        conn.close()


def test_policy_reapplication(project_root: Path, tmp_path: Path) -> None:
    """AC-S12-03: post-backup revocation/deletion + valid overlay → no reactivation."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        engram_id, digest = _seed_registry(cfg, conn)
        registry_mod.review_approve(
            cfg,
            conn,
            engram_id=engram_id,
            expected_digest=digest,
            actor="reviewer",
            event_id="evt-s12-pol-admit",
        )
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(
            event_id="ev_s12_pol",
            decision_id="dec_s12_pol",
            query_fingerprint=fk.query_fingerprint("pol", key=key),
        )
        evidence_mod.checkpoint(cfg, conn, event)

        backup_dir = tmp_path / "backup-policy"
        backup_mod.create_snapshot(cfg, conn, backup_dir)

        # After backup: revoke artifact + privacy-delete the event.
        registry_mod.review_revoke(
            cfg,
            conn,
            engram_id=engram_id,
            actor="reviewer",
            expected_digest=digest,
            reason="post-backup revoke",
            event_id="evt-s12-pol-revoke",
        )
        evidence_mod.delete_event(
            cfg, conn, event.event_id, reason="privacy", actor="operator"
        )
        assert evidence_mod.load_event(cfg, event.event_id) is None
        assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"

        # Live overlay+anchor capture current policy.
        overlay = backup_mod.build_recovery_overlay(
            cfg,
            control_sequence=10,
            operator_provenance="test-policy-reapplication",
            key=key,
        )
        anchor = backup_mod.issue_sequence_anchor(overlay, key=key)
        assert any(r.get("target_event_id") == event.event_id for r in overlay.deletion_records)
        assert any(r.get("engram_id") == engram_id for r in overlay.revocation_records)

        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=overlay,
            sequence_anchor=anchor,
            custody_key=key,
            preserve_live_overlay=False,
        )
        assert result["status"] == "ok"

        # Backup had the live event + admit; overlay must prevent reactivation.
        assert evidence_mod.load_event(cfg, event.event_id) is None
        latest = trust_mod.latest_decision_for(cfg, engram_id)
        assert latest is not None
        assert latest.decision == "revoke"
    finally:
        conn.close()


def test_missing_stale_overlay_closed(project_root: Path, tmp_path: Path) -> None:
    """AC-S12-05: clean-machine restore without overlay/anchor stays offline."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(
            event_id="ev_s12_clean",
            decision_id="dec_s12_clean",
            query_fingerprint=fk.query_fingerprint("clean", key=key),
        )
        evidence_mod.checkpoint(cfg, conn, event)
        backup_dir = tmp_path / "backup-clean"
        backup_mod.create_snapshot(cfg, conn, backup_dir)

        # Simulate clean machine: wipe data_dir contents except we keep project.
        data = cfg.data_dir
        for child in list(data.iterdir()):
            if child.name == "recovery":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        conn.close()
        # Recreate empty dirs + DB for lease.
        cfg.ensure_dirs()
        conn = db_mod.connect(cfg.db_path)

        # Missing overlay/anchor → staging + reconciliation_required.
        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=None,
            sequence_anchor=None,
            preserve_live_overlay=False,
        )
        assert result["reconciliation_required"] is True
        assert result["activated"] is False
        assert backup_mod.is_reconciliation_required(cfg) is True

        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            backup_mod.assert_routing_allowed(cfg)
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            backup_mod.assert_evidence_access_allowed(cfg)

        report = doctor_mod.run_doctor(cfg)
        assert report["reconciliation_required"] is True
        assert any(c["id"] == "recovery.reconciliation" and c["status"] == "fail" for c in report["checks"])

        # Stale overlay (sequence behind declared control) also fails closed.
        # Restore a key so we can authenticate a *stale* anchor attempt.
        # Use a fresh key — without custody matching backup tombstones, still closed.
        stale_key = b"\x11" * 32
        # Build overlay claiming registry but with wrong MAC key vs seal requirements.
        body = {
            "kind": backup_mod.RECOVERY_OVERLAY_KIND,
            "registry_id": "reg_stale",
            "control_sequence": 1,
            "deletion_records": [],
            "revocation_records": [],
            "policy_digest": "0" * 64,
            "content_hashes": [],
            "operator_provenance": "stale",
        }
        mac = backup_mod.sign_overlay(body, key=stale_key)
        overlay = backup_mod.RecoveryOverlay.from_dict({**body, "mac": mac})
        # Anchor with lower sequence than overlay → stale.
        bad_anchor_body = {
            "kind": backup_mod.SEQUENCE_ANCHOR_KIND,
            "registry_id": "reg_stale",
            "control_sequence": 0,
            "overlay_digest": overlay.content_digest(),
            "issued_at": "2020-01-01T00:00:00+00:00",
        }
        bad_anchor = backup_mod.SequenceAnchor.from_dict(
            {**bad_anchor_body, "mac": backup_mod.sign_anchor(bad_anchor_body, key=stale_key)}
        )
        result2 = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=overlay,
            sequence_anchor=bad_anchor,
            custody_key=stale_key,
            preserve_live_overlay=False,
        )
        assert result2["reconciliation_required"] is True
        assert backup_mod.is_reconciliation_required(cfg) is True
    finally:
        conn.close()
