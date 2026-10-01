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
    (cfg.registry_dir / "sample.egr.md").write_bytes(source.read_bytes())
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
