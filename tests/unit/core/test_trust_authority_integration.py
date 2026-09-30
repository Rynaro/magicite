"""Actual domain calls with an explicit simulated custody provider."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import trust, writer_guard
from magicite.core.trust_custodian import CustodianStore
from magicite.core.trust_journal import TrustJournal
from magicite.storage import db


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    store = CustodianStore.create(tmp_path / "independent-test-store")
    store.enroll("r", trust.default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    client = Adapter()
    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", client))
    TrustJournal(cfg.data_dir / "trust" / "authority", "r", client).initialize_reviewed_genesis()
    yield cfg, connection, store
    connection.close()
    store.close()


def decision(identity, action):
    policy = trust.default_policy()
    return trust.TrustDecision(
        decision_id=identity,
        engram_id="subject",
        content_digest="a" * 64,
        decision=action,
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
    )


def test_actual_domain_mirror_loss_cannot_resurrect_revoke(enrolled):
    cfg, connection, _ = enrolled
    trust.persist_decision(cfg, connection, decision("admit", "admit"))
    assert trust.admission_still_valid(cfg, engram_id="subject", content_digest="a" * 64)
    trust.persist_decision(cfg, connection, decision("revoke", "revoke"))
    legacy = trust.trust_decisions_dir(cfg)
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "admit.json").write_text(json.dumps(decision("admit", "admit").to_dict()))
    assert not trust.admission_still_valid(cfg, engram_id="subject", content_digest="a" * 64)
    assert trust.latest_decision_for(cfg, "subject").decision == "revoke"


def test_policy_projection_cannot_reset_authenticated_policy(enrolled):
    cfg, _, _ = enrolled
    policy = trust.TrustPolicy(policy_id="reviewed", revision=2, roots=())
    trust.save_policy(cfg, policy)
    trust.trust_policy_path(cfg).write_text(json.dumps(trust.default_policy().to_dict()))
    assert trust.load_policy(cfg) == policy
    trust.trust_policy_path(cfg).unlink()
    assert trust.load_policy(cfg) == policy


def test_unenrolled_mirror_never_becomes_authority(tmp_path):
    cfg = Config(project_root=tmp_path)
    legacy = trust.trust_decisions_dir(cfg)
    legacy.mkdir(parents=True)
    (legacy / "admit.json").write_text(json.dumps(decision("admit", "admit").to_dict()))
    assert not trust.admission_still_valid(cfg, engram_id="subject", content_digest="a" * 64)
    with pytest.raises(trust.TrustLedgerCorruptError):
        trust.load_policy(cfg)


def test_all_production_outer_leases_use_explicit_registry_factory():
    root = Path(__file__).resolve().parents[3] / "src" / "magicite"
    raw = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "CrossProcessLease":
                    raw.append(str(path.relative_to(root)))
    assert raw == ["core/writer_guard.py"]


def test_real_register_review_revoke_flow_uses_custody(enrolled):
    import shutil

    from magicite.core import registry
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, connection, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    shutil.copy(fixture, cfg.registry_dir / "sample.egr.md")
    outcome = registry.register(cfg, connection, get_embedder(dim=256), path=".magicite/engrams")
    assert not outcome.validation_errors
    row = connection.execute("SELECT id, content_sha256 FROM engram").fetchone()
    assert row is not None
    approval = registry.review_approve(
        cfg, connection, engram_id=row["id"], expected_digest=row["content_sha256"], actor="operator"
    )
    assert approval.decision == "admit"
    revoke = registry.review_revoke(
        cfg, connection, engram_id=row["id"], expected_digest=row["content_sha256"], actor="operator"
    )
    assert revoke.decision == "revoke"
    assert not registry.trust_view_for(cfg, connection, engram_id=row["id"]).admitted
