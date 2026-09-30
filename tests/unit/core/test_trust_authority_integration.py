"""Actual domain calls with an explicit simulated custody provider."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import trust, writer_guard
from magicite.core.trust_custodian import CustodianError, CustodianStore
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


@pytest.mark.parametrize("drift", ["policy", "content"])
def test_router_never_restores_invalid_authenticated_admission(enrolled, drift):
    from magicite.core import router

    cfg, connection, _ = enrolled
    trust.persist_decision(cfg, connection, decision("admit", "admit"))
    # Minimal SQLite row is sufficient for the actual route/body trust helper.
    digest = "b" * 64 if drift == "content" else "a" * 64
    row = connection.execute(
        "SELECT 'subject' AS id, ? AS content_sha256, 'verified' AS verification_status, "
        "'active' AS status, 'authored' AS origin",
        (digest,),
    ).fetchone()
    policy = (
        trust.default_policy()
        if drift == "content"
        else trust.TrustPolicy(policy_id=trust.default_policy().policy_id, revision=2, roots=())
    )
    view = router._route_trust_view(
        cfg, row, cached_decision=decision("admit", "admit"), cached_policy=policy
    )
    assert not view.admitted


@pytest.mark.parametrize("restriction", ["policy", "root_revocation"])
def test_actual_route_and_body_deny_after_authenticated_policy_change(enrolled, restriction):
    import shutil

    from magicite.core import registry, router
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.mcp import bind_retrieval
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput

    cfg, connection, _ = enrolled
    root_fingerprint = None
    if restriction == "root_revocation":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        from magicite.core.bundles import public_key_fingerprint

        public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        trust.pin_trust_root(cfg, public_key_bytes=public)
        root_fingerprint = public_key_fingerprint(public)
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams"
    for source in fixtures.glob("*.egr.md"):
        shutil.copy(source, cfg.registry_dir / source.name)
    embedder = get_embedder(dim=256)
    registry.register(cfg, connection, embedder, path=".magicite/engrams")
    for row in connection.execute("SELECT id,content_sha256 FROM engram").fetchall():
        registry.review_approve(
            cfg, connection, engram_id=row["id"], expected_digest=row["content_sha256"], actor="operator"
        )
    name = "proton-ge-proton-downgrade"
    row = connection.execute("SELECT content_sha256 FROM engram WHERE name=?", (name,)).fetchone()
    params = LoadSkillBodyInput(
        name=name,
        level="L2",
        expected_content_digest=row["content_sha256"],
        expected_policy_digest=bind_retrieval._active_policy_digest(cfg),
    )
    ctx = ToolContext(cfg=cfg, conn=connection, embedder=embedder)
    assert bind_retrieval.load_skill_body(ctx, params).status == "ok"
    if root_fingerprint is not None:
        trust.revoke_trust_root(cfg, fingerprint=root_fingerprint)
    else:
        trust.save_policy(cfg, trust.TrustPolicy(policy_id="restricted", revision=2, roots=()))
    body = bind_retrieval.load_skill_body(ctx, params)
    assert body.status != "ok"
    assert not body.procedure
    routed = router.route(cfg, connection, embedder, query="rollback proton for a steam game", k=5)
    assert not routed.candidates


def test_stale_root_pin_cannot_undo_interleaved_acknowledged_revocation(enrolled, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from magicite.core.bundles import public_key_fingerprint

    cfg, _, _ = enrolled
    key_a = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_b = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust.pin_trust_root(cfg, public_key_bytes=key_a)
    original = trust.save_policy
    inside = False

    def revoke_between_read_and_save(cfg, policy):
        nonlocal inside
        if not inside:
            inside = True
            trust.revoke_trust_root(cfg, fingerprint=public_key_fingerprint(key_a))
        return original(cfg, policy)

    monkeypatch.setattr(trust, "save_policy", revoke_between_read_and_save)
    try:
        trust.pin_trust_root(cfg, public_key_bytes=key_b)
    except (CustodianError, ValueError):
        pass  # A stale pin is allowed to abort; acknowledged revocation is not.
    assert next(
        root for root in trust.load_policy(cfg).roots if root.fingerprint == public_key_fingerprint(key_a)
    ).revoked


@pytest.mark.parametrize("domain", ["backup", "evidence", "policy", "trust"])
def test_nested_domain_rejects_unenrolled_or_foreign_outer_lease(tmp_path, domain):
    from magicite.core import backup, evidence, policy_store
    from magicite.errors import BusyError
    from magicite.storage.lease import CrossProcessLease

    cfg = Config(project_root=tmp_path / "a")
    other = Config(project_root=tmp_path / "b")
    cfg.ensure_dirs()
    other.ensure_dirs()
    first, second = db.connect(cfg.db_path), db.connect(other.db_path)
    guards = {
        "backup": lambda: backup._backup_lease(other, second, "test"),
        "evidence": lambda: evidence._evidence_write_guard(other, second, "test"),
        "policy": lambda: policy_store._policy_write_leases(other, holder="test"),
        "trust": lambda: trust._trust_write_leases(other, None, holder="test"),
    }
    try:
        with CrossProcessLease(lock_path=cfg.dream_lock_path, conn=first).acquire():
            with pytest.raises((BusyError, CustodianError)):
                with guards[domain]():
                    pytest.fail("foreign bare lease reached protected mutation scope")
    finally:
        first.close()
        second.close()


def test_revoked_signer_is_denied_with_matching_current_policy_before_commit(enrolled):
    import shutil

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from magicite.core import registry, router
    from magicite.core.bundles import public_key_fingerprint
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.errors import InvalidInputError

    cfg, connection, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    shutil.copy(fixture, cfg.registry_dir / "sample.egr.md")
    registry.register(cfg, connection, get_embedder(dim=256), path=".magicite/engrams")
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    policy = trust.pin_trust_root(cfg, public_key_bytes=public, revoke=True)
    row = connection.execute("SELECT * FROM engram").fetchone()
    value = trust.TrustDecision(
        decision_id="invalid-signer-admit",
        engram_id=row["id"],
        content_digest=row["content_sha256"],
        decision="admit",
        source_channel="bundle_import",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
        signer_fingerprint=public_key_fingerprint(public),
        signature_valid=True,
    )
    assert not router._route_trust_view(cfg, row, cached_decision=value, cached_policy=policy).admitted
    before = trust.authenticated_snapshot(cfg).head
    with pytest.raises(InvalidInputError):
        trust.approve(
            cfg,
            connection,
            engram_id=row["id"],
            expected_digest=row["content_sha256"],
            actor="operator",
            source_channel="bundle_import",
            signature_valid=True,
            signer_fingerprint=public_key_fingerprint(public),
        )
    after = trust.authenticated_snapshot(cfg).head
    assert after["head_sequence"] == before["head_sequence"]
    assert after["head_mac"] == before["head_mac"]


def test_register_publishes_bound_marker_and_preserves_source(enrolled):
    import shutil

    from magicite.core import registry, trust_artifacts
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.engram import parser

    cfg, conn, store = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    target = cfg.registry_dir / "sample.egr.md"
    shutil.copy(fixture, target)
    source = target.read_bytes()
    outcome = registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    assert outcome.ingested == 1
    artifact, _ = parser.load_artifact_file(target, registry_root=cfg.project_root)
    trust_artifacts.require_enrollment_marker(artifact, "r")
    import hashlib

    archive = cfg.data_dir / "trust/sources" / hashlib.sha256(source).hexdigest()
    assert archive.read_bytes() == source
    lineage = [r for r in store.committed_records("r") if r["kind"] == "artifact_transform"]
    assert len(lineage) == 1
    assert lineage[0]["payload"]["target_digest"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert lineage[0]["payload"]["grants_admission"] is False
    assert not registry.trust_view_for(cfg, conn, engram_id=artifact.id).admitted


def test_sync_never_transforms_or_admits_unmarked_artifact(enrolled):
    import shutil

    from magicite.core import registry
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, store = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    target = cfg.registry_dir / "sample.egr.md"
    shutil.copy(fixture, target)
    source = target.read_bytes()
    outcome = registry.sync(cfg, conn, get_embedder(dim=256))
    assert outcome.validation_errors
    assert target.read_bytes() == source
    assert conn.execute("SELECT count(*) FROM engram").fetchone()[0] == 0
    assert len(store.committed_records("r")) == 1


def test_skill_import_marks_target_before_indexing(enrolled):
    from magicite.core import registry, trust_artifacts
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, store = enrolled
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/toy-registry/skills/wine-dxvk-cache-clear/SKILL.md"
    )
    intake = cfg.project_root / "SKILL.md"
    intake.write_bytes(fixture.read_bytes())
    outcome = registry.register(cfg, conn, get_embedder(dim=256), path=str(intake))
    assert outcome.ingested == 1, outcome.validation_errors
    row = conn.execute("SELECT path,content_sha256 FROM engram").fetchone()
    artifact = trust_artifacts.require_bound_artifact(cfg, cfg.project_root / row["path"])
    assert artifact.content_sha256 == row["content_sha256"]
    assert store.committed_records("r")[1]["kind"] == "artifact_transform"


def test_ingest_rejects_object_different_from_bound_file(enrolled, monkeypatch):
    import shutil

    from magicite.core import registry, trust_artifacts
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    target = cfg.registry_dir / "sample.egr.md"
    shutil.copy(fixture, target)
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    stale = registry._artifact_to_engram(artifact, intake_channel="local_register")
    stale.content_sha256 = "0" * 64

    def unexpected(*args, **kwargs):
        raise AssertionError("stale object reached DB upsert")

    monkeypatch.setattr(registry.durable_mod, "upsert_engram", unexpected)
    entry, error, _, _ = registry._ingest_one(
        conn, embedder, stale, profile="strict", registry_dir=cfg.registry_dir, cfg=cfg
    )
    assert entry is None and error is not None


def test_skill_raw_source_archive_and_conversion_provenance(enrolled):
    import hashlib

    from magicite.core import registry
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, store = enrolled
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/toy-registry/skills/wine-dxvk-cache-clear/SKILL.md"
    )
    source = fixture.read_bytes()
    intake = cfg.project_root / "SKILL.md"
    intake.write_bytes(source)
    registry.register(cfg, conn, get_embedder(dim=256), path=str(intake))
    digest = hashlib.sha256(source).hexdigest()
    assert (cfg.data_dir / "trust/sources" / digest).read_bytes() == source
    lineage = next(r["payload"] for r in store.committed_records("r") if r["kind"] == "artifact_transform")
    assert lineage["conversion_provenance"] == {
        "format": "SKILL.md",
        "source_digest": digest,
        "intermediate_digest": lineage["source_digest"],
    }


def test_sharpen_preserves_v1_contract_and_invalidates_prior_admission(enrolled):
    import shutil
    from types import SimpleNamespace

    from magicite.core import lifecycle, registry, trust_artifacts
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    target = cfg.registry_dir / "sample.egr.md"
    shutil.copy(fixture, target)
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    before = trust_artifacts.require_bound_artifact(cfg, target)
    registry.review_approve(
        cfg, conn, engram_id=before.id, expected_digest=before.content_sha256, actor="operator"
    )
    assert registry.trust_view_for(cfg, conn, engram_id=before.id).admitted
    result = lifecycle.execute_sharpen(
        cfg,
        conn,
        embedder,
        name=before.name,
        proposed_changes=SimpleNamespace(
            procedures=["Keep the source archive."], triggers=["explicit marker regression"], pitfalls=[]
        ),
        actor="operator",
    )
    after = trust_artifacts.require_bound_artifact(cfg, target)
    assert result.new_version == before.frontmatter.version + 1
    for field in ("compatibility", "capabilities", "relations", "risk", "assets", "extensions"):
        assert getattr(after.frontmatter, field) == getattr(before.frontmatter, field)
    assert "explicit marker regression" in after.frontmatter.routing.positive
    assert not registry.trust_view_for(cfg, conn, engram_id=after.id).admitted


def test_authored_edit_rejects_stale_source_before_journal_advance(enrolled):
    import shutil

    from magicite.core import registry, trust_artifacts, writer_guard
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, store = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    target = cfg.registry_dir / "sample.egr.md"
    shutil.copy(fixture, target)
    registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    head = store.read_current("r")
    with writer_guard.registry_writer_lease(cfg, conn).acquire():
        with pytest.raises(CustodianError, match="source changed"):
            trust_artifacts.publish_authored_edit(
                cfg, target, target.read_bytes(), expected_source_digest="0" * 64, actor="operator"
            )
    assert store.read_current("r")["head_mac"] == head["head_mac"]
