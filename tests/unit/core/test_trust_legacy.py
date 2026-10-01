"""Reviewed legacy migration inputs; preview never creates trust authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core.trust import default_policy
from magicite.storage import db


@pytest.fixture
def legacy(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    conn.execute("CREATE TABLE legacy_business (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO legacy_business VALUES (1, 'uncheckpointed business row')")
    (cfg.data_dir / "trust").mkdir()
    (cfg.data_dir / "trust/policy.json").write_text(json.dumps(default_policy().to_dict()))
    source = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    import hashlib

    asset = b"legacy retained resource"
    (cfg.registry_dir / "assets").mkdir()
    (cfg.registry_dir / "assets/helper.bin").write_bytes(asset)
    raw = source.read_text().replace(
        "assets: {}",
        "assets:\n  assets/helper.bin:\n    sha256: "
        + hashlib.sha256(asset).hexdigest()
        + f"\n    size: {len(asset)}\n    media_type: application/octet-stream",
    )
    (cfg.registry_dir / "sample.egr.md").write_text(raw)
    yield cfg, conn
    conn.close()


def tree(cfg):
    return {
        str(p.relative_to(cfg.data_dir)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in cfg.data_dir.rglob("*")
        if p.is_file()
    }


def test_legacy_preview_is_zero_write_and_discloses_unsigned_history(legacy):
    from magicite.core.trust_legacy import preview

    cfg, _ = legacy
    before = tree(cfg)
    plan = preview(cfg, registry_id="protected-r", actor="operator")
    assert tree(cfg) == before
    assert plan["historical_provenance"] == "unsigned-incomplete-operator-reconciliation-required"
    assert plan["artifacts"][0]["target_digest"] != plan["artifacts"][0]["source_digest"]
    assert not (cfg.data_dir / "trust/authority").exists()


def test_logical_db_digest_excludes_only_lease_rows_and_includes_business_wal(legacy):
    from magicite.core.trust_legacy import logical_db_digest

    _, conn = legacy
    initial = logical_db_digest(conn)
    conn.execute(
        "INSERT INTO writer_lease (id, holder, pid, acquired_at, heartbeat_at, expires_at, fencing_token) "
        "VALUES (1, 'test', 1, 1, 1, 2, 1)"
    )
    assert logical_db_digest(conn) == initial
    conn.execute("UPDATE legacy_business SET value='changed' WHERE id=1")
    assert logical_db_digest(conn) != initial


def test_legacy_preview_rejects_conflicting_decision_identity(legacy):
    from magicite.core.trust import TrustDecision
    from magicite.core.trust_custodian import CustodianError
    from magicite.core.trust_legacy import preview

    cfg, _ = legacy
    policy = default_policy()
    payload = TrustDecision(
        decision_id="same",
        engram_id="subject",
        content_digest="a" * 64,
        decision="revoke",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
    ).to_dict()
    root = cfg.data_dir / "trust/decisions"
    root.mkdir()
    (root / "one.json").write_text(json.dumps(payload))
    payload["decision"] = "admit"
    (root / "two.json").write_text(json.dumps(payload))
    with pytest.raises(CustodianError):
        preview(cfg, registry_id="r", actor="operator")


def test_reviewed_backup_includes_wal_and_all_managed_domains_before_migration(legacy, tmp_path, monkeypatch):
    from magicite.core import trust_legacy, writer_guard
    from magicite.core.trust_custodian import CustodianStore

    cfg, conn = legacy
    (cfg.data_dir / "evidence").mkdir()
    (cfg.data_dir / "evidence/private-history.json").write_text('{"preserve":true}')
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Adapter()))
    try:
        result = trust_legacy.backup_reviewed(
            cfg, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=tmp_path / "backup"
        )
        assert result["complete"] is True
        recovered_plan, recovered_manifest = trust_legacy.read_verified_backup(
            tmp_path / "backup", reviewed_sha256=trust_legacy.digest(plan)
        )
        assert recovered_plan == plan
        assert trust_legacy.digest(recovered_manifest) == result["manifest_digest"]
        assert (tmp_path / "backup/files/evidence/private-history.json").read_text() == '{"preserve":true}'
        restored = db.connect(tmp_path / "backup/files/engrams/skill-graph.db", migrate=False)
        try:
            assert (
                restored.execute("SELECT value FROM legacy_business").fetchone()[0]
                == "uncheckpointed business row"
            )
        finally:
            restored.close()
        assert not (cfg.data_dir / "trust/authority").exists()
        assert trust_legacy.logical_db_digest(conn) == plan["logical_db_digest"]
    finally:
        store.close()


def test_changed_business_row_or_manifest_denies_backup_before_migration(legacy, tmp_path, monkeypatch):
    from magicite.core import trust_legacy, writer_guard
    from magicite.core.trust_custodian import CustodianError, CustodianStore

    cfg, conn = legacy
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Adapter()))
    conn.execute("UPDATE legacy_business SET value='unreviewed' WHERE id=1")
    try:
        with pytest.raises(CustodianError):
            trust_legacy.backup_reviewed(
                cfg, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=tmp_path / "backup"
            )
        assert not (tmp_path / "backup").exists()
        assert not (cfg.data_dir / "trust/authority").exists()
    finally:
        store.close()


@pytest.fixture
def legacy_custody(legacy, tmp_path, monkeypatch):
    from magicite.core import writer_guard
    from magicite.core.trust_custodian import CustodianStore

    store = CustodianStore.create(tmp_path / "separate-custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Adapter()))
    yield legacy[0], legacy[1], store
    store.close()


def test_incomplete_backup_never_initializes_history_and_can_resume(legacy_custody, tmp_path):
    from magicite.core import trust_legacy

    cfg, _, _ = legacy_custody
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    destination = tmp_path / "interrupted-backup"

    def stop(boundary):
        if boundary == "before_backup_complete":
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        trust_legacy.backup_reviewed(
            cfg,
            plan=plan,
            reviewed_sha256=trust_legacy.digest(plan),
            destination=destination,
            fault_hook=stop,
        )
    assert not (destination / "complete.json").exists()
    assert not (cfg.data_dir / "trust/authority").exists()
    assert trust_legacy.backup_reviewed(
        cfg, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=destination
    )["complete"]


def test_existing_control_key_requires_encrypted_custody_and_is_not_archived(legacy_custody, tmp_path):
    import hashlib

    from magicite.core import trust_legacy
    from magicite.core.trust_custodian import CustodianError

    cfg, _, _ = legacy_custody
    secret = b"private-control-key-canary-32byte"
    (cfg.runtime_dir / "fingerprint.key").write_bytes(secret)
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    destination = tmp_path / "key-backup"
    with pytest.raises(CustodianError):
        trust_legacy.backup_reviewed(
            cfg, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=destination
        )
    assert not destination.exists()
    encrypted = tmp_path / "operator-encrypted-custody"
    encrypted.write_bytes(b"opaque operator-managed encrypted artifact")
    reference = {
        "schema": "OperatorEncryptedCustody/1",
        "path": str(encrypted),
        "sha256": hashlib.sha256(encrypted.read_bytes()).hexdigest(),
        "source_sha256": {"runtime/fingerprint.key": hashlib.sha256(secret).hexdigest()},
    }
    trust_legacy.backup_reviewed(
        cfg,
        plan=plan,
        reviewed_sha256=trust_legacy.digest(plan),
        destination=destination,
        encrypted_custody=reference,
    )
    assert not (destination / "files/runtime/fingerprint.key").exists()
    assert all(secret not in p.read_bytes() for p in destination.rglob("*") if p.is_file())


@pytest.mark.parametrize("interrupted", [False, True])
def test_actual_reviewed_apply_preserves_original_revoke_and_resumes_nonempty_targets(
    legacy_custody, tmp_path, interrupted
):
    import hashlib

    from magicite.core import trust, trust_legacy

    cfg, conn, _ = legacy_custody
    source = cfg.registry_dir / "sample.egr.md"
    raw = source.read_bytes()
    policy = default_policy()
    restriction = trust.TrustDecision(
        decision_id="original-revoke",
        engram_id="egr_a1b2c3d4",
        content_digest=hashlib.sha256(raw).hexdigest(),
        decision="revoke",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="original-reviewer",
        timestamp="2025-01-01T00:00:00Z",
    ).to_dict()
    (cfg.data_dir / "trust/decisions").mkdir()
    (cfg.data_dir / "trust/decisions/original.json").write_text(json.dumps(restriction))
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    destination = tmp_path / "complete-backup"
    trust_legacy.backup_reviewed(
        cfg, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=destination
    )

    def stop(boundary):
        if boundary == "target_published":
            raise RuntimeError("process interruption boundary")

    if interrupted:
        with pytest.raises(RuntimeError):
            trust_legacy.apply_reviewed(
                cfg, backup_path=destination, reviewed_sha256=trust_legacy.digest(plan), fault_hook=stop
            )
        with pytest.raises(trust.TrustLedgerCorruptError):
            trust.authenticated_snapshot(cfg)
        assert source.read_bytes() != raw
    result = trust_legacy.apply_reviewed(
        cfg, backup_path=destination, reviewed_sha256=trust_legacy.digest(plan)
    )
    assert result["status"] == "complete_requires_target_review"
    snapshot = trust.authenticated_snapshot(cfg)
    assert snapshot.latest_by_engram["egr_a1b2c3d4"] == restriction
    assert b"magicite.trust_journal" in source.read_bytes()
    assert (destination / "files/engrams/sample.egr.md").read_bytes() == raw
    assert (cfg.data_dir / "trust/sources" / hashlib.sha256(raw).hexdigest()).read_bytes() == raw
    assert trust_legacy.logical_db_digest(conn) == plan["logical_db_digest"]
    trust_legacy.apply_reviewed(cfg, backup_path=destination, reviewed_sha256=trust_legacy.digest(plan))
    assert trust.authenticated_snapshot(cfg).latest_by_engram["egr_a1b2c3d4"] == restriction


def test_unsigned_legacy_signer_claim_only_restricts_and_never_verifies_target(legacy, tmp_path, monkeypatch):
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from magicite.core import bundles, registry, trust, trust_legacy, writer_guard
    from magicite.core.trust_custodian import CustodianStore
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.errors import InvalidInputError

    cfg, conn = legacy
    public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    fingerprint = bundles.public_key_fingerprint(public)
    policy = trust.TrustPolicy(
        policy_id="reviewed", revision=1, roots=(bundles.TrustRoot(fingerprint, public, revoked=True),)
    )
    (cfg.data_dir / "trust/policy.json").write_text(json.dumps(policy.to_dict()))
    original = trust.TrustDecision(
        decision_id="unsigned-old-admit",
        engram_id="egr_a1b2c3d4",
        content_digest=hashlib.sha256((cfg.registry_dir / "sample.egr.md").read_bytes()).hexdigest(),
        decision="admit",
        source_channel="bundle_import",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="old-operator",
        timestamp="2025-01-01T00:00:00Z",
        signer_fingerprint=fingerprint,
        signature_valid=True,
    ).to_dict()
    (cfg.data_dir / "trust/decisions").mkdir()
    (cfg.data_dir / "trust/decisions/old-admit.json").write_text(json.dumps(original))
    store = CustodianStore.create(tmp_path / "legacy-signer-custody")
    store.enroll("r", policy.to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Adapter()))
    try:
        plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
        backup_path = tmp_path / "backup-signer"
        trust_legacy.backup_reviewed(
            cfg, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=backup_path
        )
        trust_legacy.apply_reviewed(cfg, backup_path=backup_path, reviewed_sha256=trust_legacy.digest(plan))
        snapshot = trust.authenticated_snapshot(cfg)
        assert not any(row["decision_id"] == original["decision_id"] for row in snapshot.decisions)
        lineage = next(row["payload"] for row in snapshot.records if row["kind"] == "artifact_transform")
        assert lineage["signature_provenance"]["source"]["signature_valid"] is False
        assert lineage["legacy_provenance"]["original_decisions"] == [original]
        registry.sync(cfg, conn, get_embedder(dim=256))
        with pytest.raises(InvalidInputError):
            registry.review_approve(
                cfg,
                conn,
                engram_id=original["engram_id"],
                expected_digest=lineage["target_digest"],
                actor="operator",
            )
    finally:
        store.close()


def test_real_child_exit_after_target_publish_leaves_gate_closed_then_resumes(legacy_custody, tmp_path):
    import subprocess
    import sys
    import time

    from magicite.core import trust, trust_legacy

    cfg, _, store = legacy_custody
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    destination = tmp_path / "child-backup"
    plan_digest = trust_legacy.digest(plan)
    trust_legacy.backup_reviewed(cfg, plan=plan, reviewed_sha256=plan_digest, destination=destination)
    program = """
import os, sys
from pathlib import Path
from magicite.config import Config
from magicite.core import trust_legacy, writer_guard
from magicite.core.trust_custodian import CustodianStore
store = CustodianStore.open(Path(sys.argv[2]))
class Adapter:
    def call(self, operation, **arguments):
        return getattr(store, operation)("r", **arguments)
writer_guard.resolve_custody = lambda cfg: ("r", Adapter())
original = writer_guard.registry_writer_lease
def brief_lease(cfg, conn, **kwargs):
    return original(cfg, conn, ttl_s=0.1, heartbeat_interval_s=0.02, **kwargs)
writer_guard.registry_writer_lease = brief_lease
def stop(boundary):
    if boundary == "target_published":
        os._exit(23)
trust_legacy.apply_reviewed(Config(project_root=Path(sys.argv[1])), backup_path=Path(sys.argv[3]),
                           reviewed_sha256=sys.argv[4], fault_hook=stop)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(cfg.project_root),
            str(store.directory),
            str(destination),
            plan_digest,
        ],
        capture_output=True,
    )
    assert completed.returncode == 23, completed.stderr.decode()
    with pytest.raises(trust.TrustLedgerCorruptError):
        trust.authenticated_snapshot(cfg)
    time.sleep(0.15)  # expired child lease; real OS lock was released by process death
    result = trust_legacy.apply_reviewed(cfg, backup_path=destination, reviewed_sha256=plan_digest)
    assert result["status"] == "complete_requires_target_review"
    assert trust.authenticated_snapshot(cfg).latest_by_engram["egr_a1b2c3d4"]["decision"] == "pending"


def test_legacy_operator_cli_preview_backup_apply(legacy_custody, tmp_path):
    from click.testing import CliRunner

    from magicite.core.custody_admin import custody_cli

    cfg, _, _ = legacy_custody
    runner = CliRunner()
    before = tree(cfg)
    previewed = runner.invoke(
        custody_cli,
        [
            "legacy-preview",
            "--project-root",
            str(cfg.project_root),
            "--registry-id",
            "r",
            "--actor",
            "operator",
        ],
    )
    assert previewed.exit_code == 0, previewed.output
    assert tree(cfg) == before
    value = json.loads(previewed.output)
    plan_path = tmp_path / "reviewed.json"
    plan_path.write_text(json.dumps(value["plan"]))
    backup = tmp_path / "cli-backup"
    backed = runner.invoke(
        custody_cli,
        [
            "legacy-backup",
            "--project-root",
            str(cfg.project_root),
            "--plan",
            str(plan_path),
            "--reviewed-sha256",
            value["reviewed_sha256"],
            "--destination",
            str(backup),
        ],
    )
    assert backed.exit_code == 0, backed.output
    applied = runner.invoke(
        custody_cli,
        [
            "legacy-apply",
            "--project-root",
            str(cfg.project_root),
            "--backup",
            str(backup),
            "--reviewed-sha256",
            value["reviewed_sha256"],
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["status"] == "complete_requires_target_review"


@pytest.mark.parametrize(
    "boundary",
    [
        "begin_committed",
        "record_committed:artifact_transform",
        "record_committed:trust_decision",
        "before_complete",
        "complete_committed",
    ],
)
def test_apply_boundary_resume_never_implicitly_admits(legacy_custody, tmp_path, boundary):
    from magicite.core import trust, trust_legacy

    cfg, _, _ = legacy_custody
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    committed = trust_legacy.digest(plan)
    backup = tmp_path / "boundary-backup"
    trust_legacy.backup_reviewed(cfg, plan=plan, reviewed_sha256=committed, destination=backup)

    def interrupt(at):
        if at == boundary:
            raise RuntimeError("interrupted migration")

    with pytest.raises(RuntimeError):
        trust_legacy.apply_reviewed(cfg, backup_path=backup, reviewed_sha256=committed, fault_hook=interrupt)
    if boundary != "complete_committed":
        with pytest.raises(trust.TrustLedgerCorruptError):
            trust.authenticated_snapshot(cfg)
    trust_legacy.apply_reviewed(cfg, backup_path=backup, reviewed_sha256=committed)
    snapshot = trust.authenticated_snapshot(cfg)
    assert snapshot.latest_by_engram["egr_a1b2c3d4"]["decision"] == "pending"
    assert len({row["record_id"] for row in snapshot.records}) == len(snapshot.records)


def test_apply_resource_drift_keeps_authenticated_gate_closed(legacy_custody, tmp_path):
    from magicite.core import trust, trust_legacy
    from magicite.core.trust_custodian import CustodianError

    cfg, _, _ = legacy_custody
    plan = trust_legacy.preview(cfg, registry_id="r", actor="operator")
    committed = trust_legacy.digest(plan)
    backup = tmp_path / "drift-backup"
    trust_legacy.backup_reviewed(cfg, plan=plan, reviewed_sha256=committed, destination=backup)

    def change_asset(at):
        if at == "target_published":
            (cfg.registry_dir / "assets/helper.bin").write_bytes(b"unreviewed resource")

    with pytest.raises(CustodianError, match="source change"):
        trust_legacy.apply_reviewed(
            cfg, backup_path=backup, reviewed_sha256=committed, fault_hook=change_asset
        )
    with pytest.raises(trust.TrustLedgerCorruptError):
        trust.authenticated_snapshot(cfg)
    with pytest.raises(CustodianError, match="source change"):
        trust_legacy.apply_reviewed(cfg, backup_path=backup, reviewed_sha256=committed)
