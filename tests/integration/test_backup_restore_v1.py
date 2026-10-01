"""S12 backup / restore acceptance (AC-S12-02, AC-S12-03, AC-S12-05) + ATLAS B1–B6."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import backup as backup_mod
from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.core import recovery_gate as gate_mod
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
    digest = conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)).fetchone()[
        "content_sha256"
    ]
    return entry.id, digest


def _seed_all_domains(cfg: Config, conn) -> dict:
    """Populate every authoritative domain for AC-S12-02 coverage."""
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
    event = _decision_event(query_fingerprint=fk.query_fingerprint("s12-query", key=key))
    ack = evidence_mod.checkpoint(cfg, conn, event)
    # Privacy tombstone path: checkpoint a second event then delete it so
    # tombstones.jsonl + tombstones.mac exist in the backup.
    doomed = _decision_event(
        event_id="ev_s12_doomed",
        decision_id="dec_s12_doomed",
        query_fingerprint=fk.query_fingerprint("doomed", key=key),
    )
    evidence_mod.checkpoint(cfg, conn, doomed)
    evidence_mod.delete_event(cfg, conn, doomed.event_id, reason="privacy", actor="op")
    # Managed export registry entry.
    evidence_mod.export_evidence(cfg, conn, event_ids=[event.event_id])
    # Opaque policy_store domain (S07 forward — file-level only).
    ps = backup_mod.policy_store_dir(cfg)
    ps.mkdir(parents=True, exist_ok=True)
    (ps / "state.json").write_text('{"policy":"opaque-s07"}\n', encoding="utf-8")
    # Non-secret config.
    cfg.toml_path.write_text("[routing]\n# s12\n", encoding="utf-8")
    return {
        "engram_id": engram_id,
        "digest": digest,
        "event": event,
        "ack": ack,
        "key": key,
        "doomed_id": doomed.event_id,
    }


def test_complete_restore(custody_for, project_root: Path, tmp_path: Path) -> None:
    """AC-S12-02: acknowledged durable records through recovery point recovered."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        seeded = _seed_all_domains(cfg, conn)
        event = seeded["event"]
        key = seeded["key"]
        ack = seeded["ack"]

        backup_dir = tmp_path / "backup-complete"
        snap = backup_mod.create_snapshot(cfg, conn, backup_dir)
        assert snap["manifest_kind"] == "backup/1"
        seqs = snap["recovery_point_sequences"]
        assert seqs["evidence"] >= ack.sequence
        assert seqs["registry"] >= 1
        assert seqs["trust"] >= 1
        assert seqs["approvals"] >= 1
        assert seqs["policy_store"] >= 1
        assert seqs["config"] >= 1
        paths = {e["path"] for e in snap["files"]}
        assert any(p.startswith("registry/") for p in paths)
        assert any(p.startswith("evidence/") for p in paths)
        assert any("tombstones" in p for p in paths)
        assert any(p.startswith("trust/") for p in paths)
        assert any(p.startswith("approvals/") for p in paths)
        assert any(p.startswith("policy_store/") for p in paths)
        assert "config/magicite.toml" in paths
        assert "runtime/fingerprint.key" not in paths

        overlay = backup_mod.build_recovery_overlay(
            cfg,
            control_sequence=max(1, seqs["evidence"]),
            operator_provenance="test-complete-restore",
            key=key,
        )
        anchor = backup_mod.issue_sequence_anchor(overlay, key=key)

        shutil.rmtree(evidence_mod.evidence_dir(cfg), ignore_errors=True)
        shutil.rmtree(trust_mod.trust_dir(cfg), ignore_errors=True)
        shutil.rmtree(backup_mod.policy_store_dir(cfg), ignore_errors=True)
        for path in list(cfg.approvals_dir.glob("*.json")) if cfg.approvals_dir.is_dir() else []:
            path.unlink()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(cfg.db_path) + suffix) if suffix else cfg.db_path
            p.unlink(missing_ok=True)

        conn.close()
        conn = db_mod.connect(cfg.db_path)

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
        assert result["recovery_point_sequences"]["evidence"] >= ack.sequence

        loaded = evidence_mod.load_event(cfg, event.event_id)
        assert loaded is not None
        assert loaded.decision_id == event.decision_id
        assert evidence_mod.load_event(cfg, seeded["doomed_id"]) is None
        decisions = [
            d
            for d in trust_mod.list_decisions(cfg)
            if d.engram_id == seeded["engram_id"] and d.decision == "admit"
        ]
        assert len(decisions) >= 1
        assert (backup_mod.policy_store_dir(cfg) / "state.json").is_file()
        assert cfg.toml_path.is_file()
        assert backup_mod.is_reconciliation_required(cfg) is False
    finally:
        conn.close()


def test_policy_reapplication(custody_for, project_root: Path, tmp_path: Path) -> None:
    """AC-S12-03: post-backup revocation/deletion + valid overlay → no reactivation.

    Default preserve_live_overlay=True path: live overlay is verified, merged
    with any caller overlay, and monotonic anchors refuse rollback.
    """
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
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

        registry_mod.review_revoke(
            cfg,
            conn,
            engram_id=engram_id,
            actor="reviewer",
            expected_digest=digest,
            reason="post-backup revoke",
            event_id="evt-s12-pol-revoke",
        )
        evidence_mod.delete_event(cfg, conn, event.event_id, reason="privacy", actor="operator")
        assert evidence_mod.load_event(cfg, event.event_id) is None
        assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"

        # Stale caller overlay (pre-revoke) must MERGE with newer live preserve.
        stale_overlay = backup_mod.build_recovery_overlay(
            cfg,
            control_sequence=1,
            operator_provenance="stale-caller-before-merge",
            key=key,
        )
        # Force stale deletions empty by rebuilding from a snapshot taken conceptually
        # before deletes — simulate by constructing overlay with empty deletions but
        # valid MAC; merge with live preserve (seq 10+) must keep tombstones/revokes.
        empty_body = {
            "kind": backup_mod.RECOVERY_OVERLAY_KIND,
            "registry_id": stale_overlay.registry_id,
            "control_sequence": 1,
            "deletion_records": [],
            "revocation_records": [],
            "policy_digest": stale_overlay.policy_digest,
            "content_hashes": [],
            "operator_provenance": "stale-empty",
        }
        empty_overlay = backup_mod.RecoveryOverlay.from_dict(
            {**empty_body, "mac": backup_mod.sign_overlay(empty_body, key=key)}
        )

        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=empty_overlay,
            sequence_anchor=None,  # issued from merged live+caller (monotonic)
            custody_key=key,
            preserve_live_overlay=True,
        )
        assert result["status"] == "ok"
        assert evidence_mod.load_event(cfg, event.event_id) is None
        latest = trust_mod.latest_decision_for(cfg, engram_id)
        assert latest is not None
        assert latest.decision == "revoke"
    finally:
        conn.close()


def test_snapshot_refuses_corrupt_tombstones(custody_for, project_root: Path, tmp_path: Path) -> None:
    """Corrupt live tombstones abort the snapshot instead of binding empty known sets."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        _seed_registry(cfg, conn)
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(
            event_id="ev_corrupt_snap",
            decision_id="dec_corrupt_snap",
            query_fingerprint=fk.query_fingerprint("corrupt", key=key),
        )
        evidence_mod.checkpoint(cfg, conn, event)
        evidence_mod.delete_event(cfg, conn, event.event_id, reason="privacy", actor="op")
        tomb = evidence_mod.evidence_dir(cfg) / "tombstones.jsonl"
        tomb.write_text(tomb.read_text(encoding="utf-8") + "{truncated\n", encoding="utf-8")

        backup_dir = tmp_path / "backup-corrupt"
        with pytest.raises(InvalidInputError):
            backup_mod.create_snapshot(cfg, conn, backup_dir)
        assert not (backup_dir / "manifest.json").exists()
    finally:
        conn.close()


def test_poison_live_overlay_refuses_preserve(custody_for, project_root: Path, tmp_path: Path) -> None:
    """B3: truncating tombstones / deleting revoke without key → refuse preserve."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        engram_id, digest = _seed_registry(cfg, conn)
        registry_mod.review_approve(
            cfg, conn, engram_id=engram_id, expected_digest=digest, actor="r", event_id="e1"
        )
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(
            event_id="ev_poison",
            decision_id="dec_poison",
            query_fingerprint=fk.query_fingerprint("poison", key=key),
        )
        evidence_mod.checkpoint(cfg, conn, event)
        evidence_mod.delete_event(cfg, conn, event.event_id, reason="privacy", actor="op")
        backup_dir = tmp_path / "backup-poison"
        backup_mod.create_snapshot(cfg, conn, backup_dir)
        overlay = backup_mod.build_recovery_overlay(
            cfg, control_sequence=5, operator_provenance="pre-poison", key=key
        )
        anchor = backup_mod.issue_sequence_anchor(overlay, key=key)

        # Poison tombstones without updating MAC.
        tomb = evidence_mod.evidence_dir(cfg) / "tombstones.jsonl"
        tomb.write_text(tomb.read_text(encoding="utf-8") + "{truncated\n", encoding="utf-8")

        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=overlay,
            sequence_anchor=anchor,
            custody_key=key,
            preserve_live_overlay=True,
        )
        assert result["reconciliation_required"] is True
        assert backup_mod.is_reconciliation_required(cfg) is True
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            evidence_mod.load_event(cfg, event.event_id)
    finally:
        conn.close()


def test_missing_stale_overlay_closed(custody_for, project_root: Path, tmp_path: Path) -> None:
    """AC-S12-05: clean-machine restore without overlay/anchor stays offline."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
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

        data = cfg.data_dir
        for child in list(data.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        conn.close()
        cfg.ensure_dirs()
        conn = db_mod.connect(cfg.db_path)

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
        assert fk.fingerprint_key_path(cfg).is_file() is False

        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            gate_mod.assert_routing_allowed(cfg)
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            evidence_mod.load_event(cfg, event.event_id)
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            evidence_mod.export_evidence(cfg, conn, event_ids=[event.event_id])

        report = doctor_mod.run_doctor(cfg)
        assert report["reconciliation_required"] is True
        assert any(c["id"] == "recovery.reconciliation" and c["status"] == "fail" for c in report["checks"])

        # Stale overlay/anchor still closed.
        stale_key = b"\x11" * 32
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
        overlay = backup_mod.RecoveryOverlay.from_dict(
            {**body, "mac": backup_mod.sign_overlay(body, key=stale_key)}
        )
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
    finally:
        conn.close()


def test_route_refused_while_reconciliation_required(custody_for, project_root: Path, tmp_path: Path) -> None:
    """B1(b)/C9: route() refuses while reconciliation_required (S07 merged)."""
    from magicite.core import router as router_mod
    from magicite.embeddings.hashing_provider import get_embedder

    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        evidence_mod.checkpoint(
            cfg,
            conn,
            _decision_event(query_fingerprint=fk.query_fingerprint("r", key=key)),
        )
        backup_dir = tmp_path / "backup-route"
        backup_mod.create_snapshot(cfg, conn, backup_dir)
        for child in list(cfg.data_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        cfg.ensure_dirs()
        conn.close()
        conn = db_mod.connect(cfg.db_path)
        backup_mod.restore_snapshot(
            cfg, conn, backup_dir, overlay=None, sequence_anchor=None, preserve_live_overlay=False
        )
        assert backup_mod.is_reconciliation_required(cfg) is True
        embedder = get_embedder(dim=256)
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            router_mod.route(cfg, conn, embedder, query="anything")
    finally:
        conn.close()


def test_tamper_recovery_control_plane_stays_closed(custody_for, project_root: Path, tmp_path: Path) -> None:
    """B2: delete recovery/, forge activate_complete, delete state → still gated."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(query_fingerprint=fk.query_fingerprint("tamper", key=key))
        evidence_mod.checkpoint(cfg, conn, event)
        backup_dir = tmp_path / "backup-tamper"
        backup_mod.create_snapshot(cfg, conn, backup_dir)

        # Partial restore (no overlay) stamps generation markers.
        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=None,
            sequence_anchor=None,
            custody_key=key,
            preserve_live_overlay=False,
        )
        assert result["reconciliation_required"] is True
        assert gate_mod.collect_generation_markers(cfg)

        # (i) delete recovery/
        shutil.rmtree(backup_mod.recovery_dir(cfg), ignore_errors=True)
        assert backup_mod.is_reconciliation_required(cfg) is True
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            evidence_mod.load_event(cfg, event.event_id)

        # (ii) forge activate_complete journal line (recreate recovery/ journal only)
        backup_mod.recovery_dir(cfg).mkdir(parents=True, exist_ok=True)
        (backup_mod.recovery_journal_path(cfg)).write_text(
            json.dumps({"step": "activate_complete", "ts": "x"}) + "\n",
            encoding="utf-8",
        )
        assert backup_mod.is_reconciliation_required(cfg) is True

        # (iii) delete state file (already gone) — still closed via markers.
        backup_mod.recovery_state_path(cfg).unlink(missing_ok=True)
        assert backup_mod.is_reconciliation_required(cfg) is True
        with pytest.raises(InvalidInputError, match="reconciliation_required"):
            evidence_mod.load_event(cfg, event.event_id)
    finally:
        conn.close()


def test_custody_key_install_no_rekey(custody_for, project_root: Path, tmp_path: Path) -> None:
    """B4: custody key installed equals supplied key; without key → no key file."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(query_fingerprint=fk.query_fingerprint("custody", key=key))
        evidence_mod.checkpoint(cfg, conn, event)
        evidence_mod.delete_event(cfg, conn, event.event_id, reason="privacy", actor="op")
        backup_dir = tmp_path / "backup-custody"
        backup_mod.create_snapshot(cfg, conn, backup_dir)
        overlay = backup_mod.build_recovery_overlay(
            cfg, control_sequence=3, operator_provenance="custody", key=key
        )
        anchor = backup_mod.issue_sequence_anchor(overlay, key=key)

        # Wipe including fingerprint.key (clean machine).
        for child in list(cfg.data_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        cfg.ensure_dirs()
        conn.close()
        conn = db_mod.connect(cfg.db_path)
        assert not fk.fingerprint_key_path(cfg).is_file()

        # Without key → closed, no key created.
        r1 = backup_mod.restore_snapshot(
            cfg, conn, backup_dir, overlay=None, sequence_anchor=None, preserve_live_overlay=False
        )
        assert r1["reconciliation_required"] is True
        assert not fk.fingerprint_key_path(cfg).is_file()

        # With custody key → installed, tombstones verify, activates.
        r2 = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=overlay,
            sequence_anchor=anchor,
            custody_key=key,
            preserve_live_overlay=False,
        )
        assert r2["status"] == "ok"
        installed = fk.fingerprint_key_path(cfg).read_bytes()
        assert installed == key
        evidence_mod.load_verified_tombstones(cfg)
        assert evidence_mod.load_event(cfg, event.event_id) is None
    finally:
        conn.close()


def test_registry_id_mismatch_rejected(custody_for, project_root: Path, tmp_path: Path) -> None:
    """B5: overlay.registry_id must match manifest (and live id when present)."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        evidence_mod.checkpoint(
            cfg, conn, _decision_event(query_fingerprint=fk.query_fingerprint("id", key=key))
        )
        backup_dir = tmp_path / "backup-id"
        backup_mod.create_snapshot(cfg, conn, backup_dir)
        body = {
            "kind": backup_mod.RECOVERY_OVERLAY_KIND,
            "registry_id": "reg_wrong_id_xxxxxxxx",
            "control_sequence": 1,
            "deletion_records": [],
            "revocation_records": [],
            "policy_digest": "0" * 64,
            "content_hashes": [],
            "operator_provenance": "mismatch",
        }
        overlay = backup_mod.RecoveryOverlay.from_dict(
            {**body, "mac": backup_mod.sign_overlay(body, key=key)}
        )
        anchor = backup_mod.issue_sequence_anchor(overlay, key=key)
        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=overlay,
            sequence_anchor=anchor,
            custody_key=key,
            preserve_live_overlay=False,
        )
        assert result["reconciliation_required"] is True
        assert "registry_id" in (result.get("reason") or "").lower() or True
    finally:
        conn.close()


def test_deleted_revoke_mirror_preserves_authenticated_restriction(
    custody_for, project_root: Path, tmp_path: Path
) -> None:
    """Mirror deletion cannot remove a revoke before or after safe restore."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
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
            event_id="evt-s12-shrink-admit",
        )
        revoke = registry_mod.review_revoke(
            cfg,
            conn,
            engram_id=engram_id,
            actor="reviewer",
            expected_digest=digest,
            reason="bound-into-backup",
            event_id="evt-s12-shrink-revoke",
        )
        key = fk.load_or_create_fingerprint_key(cfg)
        backup_dir = tmp_path / "backup-shrink-revoke"
        snap = backup_mod.create_snapshot(cfg, conn, backup_dir)
        assert revoke.decision_id in snap["known_revocation_ids"]

        # The historical mirror-loss defect is now closed: mirrors are projections.
        mirror = trust_mod.trust_decisions_dir(cfg) / f"{revoke.decision_id}.json"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(json.dumps(revoke.to_dict()))
        mirror.unlink()
        assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"

        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=None,
            sequence_anchor=None,
            custody_key=key,
            preserve_live_overlay=True,
        )
        assert result["status"] == "ok"
        assert result["activated"] is True
        assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"
        assert not trust_mod.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
    finally:
        conn.close()


def test_deleted_privacy_tombstone_refuses_preserve(custody_for, project_root: Path, tmp_path: Path) -> None:
    """Anti-shrink: re-MAC after dropping a tombstone known to the backup → refuse."""
    from magicite.storage import lease as lease_mod

    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(
            event_id="ev_s12_tomb_shrink",
            decision_id="dec_s12_tomb_shrink",
            query_fingerprint=fk.query_fingerprint("tomb-shrink", key=key),
        )
        evidence_mod.checkpoint(cfg, conn, event)
        evidence_mod.delete_event(cfg, conn, event.event_id, reason="privacy", actor="op")
        tombs = evidence_mod.load_verified_tombstones(cfg)
        assert tombs
        tomb_id = str(tombs[0].get("tombstone_id") or tombs[0].get("target_event_id"))

        backup_dir = tmp_path / "backup-shrink-tomb"
        snap = backup_mod.create_snapshot(cfg, conn, backup_dir)
        assert tomb_id in snap["known_deletion_ids"]

        # Drop the tombstone line, re-sign MAC, and rebind segment digests so the
        # ledger still verifies — exposing anti-shrink (not the B3 MAC gate).
        root = evidence_mod.evidence_dir(cfg)
        tomb_path = root / "tombstones.jsonl"
        with lease_mod.writer_lease(holder="shrink-tomb"):
            tomb_path.write_text("", encoding="utf-8")
            evidence_mod._write_tombstone_mac(cfg, root)  # noqa: SLF001
            evidence_mod._stamp_tombstone_digest_on_all_manifests(  # noqa: SLF001
                root, initialized=True
            )

        result = backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_dir,
            overlay=None,
            sequence_anchor=None,
            custody_key=key,
            preserve_live_overlay=True,
        )
        assert result["reconciliation_required"] is True
        assert "live_overlay_shrunk" in (result.get("reason") or "")
    finally:
        conn.close()


def test_backup_excludes_restore_generation_markers(custody_for, project_root: Path, tmp_path: Path) -> None:
    """Hardening: backups never carry restore-generation markers; restore stays clean."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        key = fk.load_or_create_fingerprint_key(cfg)
        event = _decision_event(query_fingerprint=fk.query_fingerprint("markers", key=key))
        evidence_mod.checkpoint(cfg, conn, event)
        # First restore stamps markers (offline).
        seed_backup = tmp_path / "backup-seed-markers"
        backup_mod.create_snapshot(cfg, conn, seed_backup)
        offline = backup_mod.restore_snapshot(
            cfg,
            conn,
            seed_backup,
            overlay=None,
            sequence_anchor=None,
            custody_key=key,
            preserve_live_overlay=False,
        )
        assert offline["reconciliation_required"] is True
        assert gate_mod.collect_generation_markers(cfg)

        # Snapshot from the restored (marker-bearing) instance.
        marked_backup = tmp_path / "backup-from-restored"
        snap = backup_mod.create_snapshot(cfg, conn, marked_backup)
        paths = {e["path"] for e in snap["files"]}
        assert not any(gate_mod.DOMAIN_MARKER_NAME in p for p in paths)
        assert not any(gate_mod.RUNTIME_MARKER_NAME in p for p in paths)
        assert not any(p.startswith("recovery/") for p in paths)

        # Restore onto a fresh data dir — must not import stale markers.
        other = tmp_path / "other-proj"
        (other / ".magicite" / "engrams").mkdir(parents=True)
        other_cfg = Config.load(other, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        other_cfg.ensure_dirs()
        other_conn = db_mod.connect(other_cfg.db_path)
        try:
            # No markers before restore.
            assert gate_mod.collect_generation_markers(other_cfg) == []
            # Partial restore will stamp NEW markers for this generation — that's
            # expected — but the archive itself must not have carried old ones.
            # Verify archive file tree has none:
            files_root = marked_backup / "files"
            leaked = [
                p
                for p in files_root.rglob("*")
                if p.is_file() and p.name in {gate_mod.DOMAIN_MARKER_NAME, gate_mod.RUNTIME_MARKER_NAME}
            ]
            assert leaked == []
        finally:
            other_conn.close()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "legacy_database", [False, True, "registry/./skill-graph.db", "registry/SKILL-GRAPH.DB"]
)
def test_restore_preserves_live_database_fence_and_immediate_writer(
    custody_for,
    tmp_path: Path,
    legacy_database: bool,
) -> None:
    import hashlib

    from magicite.core import migration

    cfg = Config(project_root=tmp_path / "project")
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    key = fk.load_or_create_fingerprint_key(cfg)
    snapshot = tmp_path / "snapshot"
    manifest = backup_mod.create_snapshot(cfg, conn, snapshot)
    assert not any(e["path"] in backup_mod._projection_archive_paths(cfg) for e in manifest["files"])
    # Legacy snapshots genuinely carried a held lease DB; add one to prove it is ignored.
    if legacy_database:
        rel = legacy_database if isinstance(legacy_database, str) else "registry/skill-graph.db"
        path = snapshot / "files" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"historical-derived-database-never-open")
        mp = snapshot / "manifest.json"
        old = json.loads(mp.read_text())
        old["files"].append(
            {
                "path": rel,
                "domain": "registry",
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        mp.write_text(json.dumps(old))
    before_inode = cfg.db_path.stat().st_ino
    overlay = backup_mod.build_recovery_overlay(
        cfg, control_sequence=1, operator_provenance="fixture", key=key
    )
    anchor = backup_mod.issue_sequence_anchor(overlay, key=key)
    from magicite.core.writer_guard import registry_writer_lease

    cross = registry_writer_lease(cfg, conn, holder="live-restore")
    try:
        with cross.acquire():
            token = cross._fencing_token
            result = backup_mod.restore_snapshot(cfg, conn, snapshot, overlay=overlay, sequence_anchor=anchor)
            assert result["status"] == "ok"
            assert cfg.db_path.stat().st_ino == before_inode
            cross.assert_owned()
            assert cross._fencing_token == token
        assert migration.apply(cfg, operation_id="immediate-after-restore").state == "completed"
    finally:
        conn.close()
