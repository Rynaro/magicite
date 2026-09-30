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


def test_source_root_restriction_survives_target_review_and_authored_edit(enrolled):
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from magicite.core import lifecycle, registry, router, trust_artifacts, writer_guard
    from magicite.core.bundles import public_key_fingerprint
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.errors import InvalidInputError
    from magicite.mcp import bind_retrieval
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput

    cfg, conn, _ = enrolled
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust.pin_trust_root(cfg, public_key_bytes=public)
    fingerprint = public_key_fingerprint(public)
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures/toy-registry/engrams/proton-ge-proton-downgrade.egr.md"
    )
    target = cfg.registry_dir / fixture.name
    with writer_guard.registry_writer_lease(cfg, conn).acquire():
        trust_artifacts.publish_new_artifact(
            cfg,
            target,
            fixture.read_bytes(),
            actor="verified-fixture",
            source_signature={"signature_valid": True, "signer_fingerprint": fingerprint},
        )
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    approved = registry.review_approve(
        cfg, conn, engram_id=artifact.id, expected_digest=artifact.content_sha256, actor="operator"
    )
    assert approved.signature_valid is not True
    lifecycle.execute_sharpen(
        cfg,
        conn,
        embedder,
        name=artifact.name,
        proposed_changes=SimpleNamespace(
            procedures=["Preserve reviewed source lineage."], triggers=[], pitfalls=[]
        ),
        actor="operator",
    )
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    approved = registry.review_approve(
        cfg, conn, engram_id=artifact.id, expected_digest=artifact.content_sha256, actor="operator"
    )
    assert approved.signature_valid is not True
    params = LoadSkillBodyInput(
        name=artifact.name,
        level="L2",
        expected_content_digest=artifact.content_sha256,
        expected_policy_digest=bind_retrieval._active_policy_digest(cfg),
    )
    ctx = ToolContext(cfg=cfg, conn=conn, embedder=embedder)
    assert bind_retrieval.load_skill_body(ctx, params).status == "ok"
    trust.revoke_trust_root(cfg, fingerprint=fingerprint)
    with pytest.raises(InvalidInputError):
        registry.review_approve(
            cfg, conn, engram_id=artifact.id, expected_digest=artifact.content_sha256, actor="operator"
        )
    assert bind_retrieval.load_skill_body(ctx, params).status != "ok"
    assert not router.route(cfg, conn, embedder, query="rollback proton for steam", k=5).candidates


def test_changed_digest_review_never_inherits_target_publisher_signature(enrolled):
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
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    prior = trust.approve(
        cfg,
        conn,
        engram_id=artifact.id,
        expected_digest=artifact.content_sha256,
        actor="fixture",
        signature_valid=True,
        signer_fingerprint="a" * 64,
    )
    assert prior.signature_valid is True
    lifecycle.execute_sharpen(
        cfg,
        conn,
        embedder,
        name=artifact.name,
        proposed_changes=SimpleNamespace(
            procedures=["Exact target requires separate review."], triggers=[], pitfalls=[]
        ),
        actor="operator",
    )
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    new = trust.approve(
        cfg, conn, engram_id=artifact.id, expected_digest=artifact.content_sha256, actor="operator"
    )
    assert new.signature_valid is None
    assert new.signer_fingerprint is None


def test_missing_source_decision_reference_rejected_before_preparation(enrolled):
    import hashlib

    from magicite.core import trust_artifacts, writer_guard
    from magicite.core.trust_custodian import _bytes

    cfg, conn, store = enrolled
    source = (
        Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    ).read_bytes()
    lineage = trust_artifacts.mark_artifact(
        source,
        registry_id="r",
        relpath="sample.egr.md",
        actor="operator",
        source_decision_ids=("missing-original",),
    ).lineage
    with writer_guard.registry_writer_lease(cfg, conn).acquire():
        journal, held, fence = writer_guard.bound_journal(cfg)
        with pytest.raises(CustodianError, match="source decision"):
            journal.append(
                record_id="transform-" + hashlib.sha256(_bytes(lineage)).hexdigest(),
                kind="artifact_transform",
                payload=lineage,
                fence=fence,
                assert_owned=held.assert_owned,
            )
    assert store.read_current("r")["head_sequence"] == 1
    assert store.prepared_record("r") is None


@pytest.mark.parametrize("admitted", [False, True])
def test_dream_checkpoint_preserves_v1_and_admitted_stable_bytes(enrolled, admitted):
    import shutil

    from magicite.core import dream, registry, router, trust_artifacts, writer_guard
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.storage import lease

    cfg, conn, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams/steam-prefix-access.egr.md"
    target = cfg.registry_dir / fixture.name
    shutil.copy(fixture, target)
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    before = trust_artifacts.require_bound_artifact(cfg, target)
    if admitted:
        registry.review_approve(
            cfg, conn, engram_id=before.id, expected_digest=before.content_sha256, actor="operator"
        )
    original = target.read_bytes()
    route_before = router.route(
        cfg,
        conn,
        embedder,
        query="Locate and prepare a Steam Proton compatdata prefix for a given appid",
        k=5,
    )
    if admitted:
        assert route_before.candidates
    with writer_guard.registry_writer_lease(cfg, conn).acquire(), lease.writer_lease():
        conn.execute("UPDATE engram SET storage_strength=0.9 WHERE id=?", (before.id,))
    result = dream.run_checkpoint_only(cfg, conn)
    after = trust_artifacts.require_bound_artifact(cfg, target)
    for field in ("compatibility", "capabilities", "relations", "risk", "assets", "extensions"):
        assert getattr(after.frontmatter, field) == getattr(before.frontmatter, field)
    if admitted:
        assert target.read_bytes() == original
        assert result.checkpointed == 0
        assert [
            x.id
            for x in router.route(
                cfg,
                conn,
                embedder,
                query="Locate and prepare a Steam Proton compatdata prefix for a given appid",
                k=5,
            ).candidates
        ] == [x.id for x in route_before.candidates]
    else:
        assert result.checkpointed == 1
        assert target.read_bytes() != original
        assert not registry.trust_view_for(cfg, conn, engram_id=before.id).admitted


def test_automatic_archive_preserves_admitted_bytes_but_explicit_archive_keeps_v1(enrolled):
    import shutil

    from magicite.core import decay, dream, registry, trust_artifacts, writer_guard
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.storage import lease

    cfg, conn, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams/steam-prefix-access.egr.md"
    target = cfg.registry_dir / fixture.name
    shutil.copy(fixture, target)
    registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    before = trust_artifacts.require_bound_artifact(cfg, target)
    registry.review_approve(
        cfg, conn, engram_id=before.id, expected_digest=before.content_sha256, actor="operator"
    )
    original = target.read_bytes()
    with (
        writer_guard.registry_writer_lease(cfg, conn).acquire(),
        lease.writer_lease(),
        dream.checkpoint_phase(),
    ):
        conn.execute(
            "UPDATE engram SET storage_strength=0,peak_storage_strength=1,success_count=3 WHERE id=?",
            (before.id,),
        )
        assert decay.archive_below_floor(cfg, conn, now="2026-09-30T00:00:00Z") == []
    assert target.read_bytes() == original
    dream.archive_engram(cfg, conn, name=before.name, reason="explicit operator archival", actor="operator")
    row = conn.execute("SELECT path,status,content_sha256 FROM engram WHERE id=?", (before.id,)).fetchone()
    assert row["status"] == "archived"
    after = trust_artifacts.require_bound_artifact(cfg, cfg.project_root / row["path"])
    assert after.content_sha256 == row["content_sha256"]
    assert after.frontmatter.extensions == before.frontmatter.extensions
    assert not target.exists()


def test_unmarked_source_cannot_route_or_disclose_even_with_old_admission(enrolled):
    import hashlib
    from dataclasses import replace

    from magicite.core import registry, router
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.errors import InvalidInputError
    from magicite.mcp import bind_retrieval
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput

    cfg, conn, _ = enrolled
    source = (
        Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams/steam-prefix-access.egr.md"
    ).read_bytes()
    target = cfg.registry_dir / "source.egr.md"
    target.write_bytes(source)
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    row = conn.execute("SELECT * FROM engram").fetchone()
    original_digest = hashlib.sha256(source).hexdigest()
    target.write_bytes(source)
    conn.execute("UPDATE engram SET content_sha256=? WHERE id=?", (original_digest, row["id"]))
    old = replace(decision("original-admit", "admit"), engram_id=row["id"], content_digest=original_digest)
    trust.persist_decision(cfg, conn, old)
    params = LoadSkillBodyInput(
        name=row["name"],
        level="L2",
        expected_content_digest=original_digest,
        expected_policy_digest=bind_retrieval._active_policy_digest(cfg),
    )
    assert (
        bind_retrieval.load_skill_body(ToolContext(cfg=cfg, conn=conn, embedder=embedder), params).status
        != "ok"
    )
    assert not router.route(
        cfg,
        conn,
        embedder,
        query="Locate and prepare a Steam Proton compatdata prefix for a given appid",
        k=5,
    ).candidates
    with pytest.raises(InvalidInputError):
        registry.review_approve(
            cfg, conn, engram_id=row["id"], expected_digest=original_digest, actor="operator"
        )


def test_archive_closes_without_unlinking_source_changed_during_publication(enrolled, monkeypatch):
    import shutil

    from magicite.core import dream, registry, trust_artifacts
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, _ = enrolled
    fixture = Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams/steam-prefix-access.egr.md"
    target = cfg.registry_dir / fixture.name
    shutil.copy(fixture, target)
    registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    original = trust_artifacts.publish_authored_edit
    changed = target.read_bytes() + b"\nchanged during archive publication\n"

    def interleave(*args, **kwargs):
        original(*args, **kwargs)
        target.write_bytes(changed)

    monkeypatch.setattr(trust_artifacts, "publish_authored_edit", interleave)
    with pytest.raises(CustodianError, match="source changed"):
        dream.archive_engram(cfg, conn, name=artifact.name, reason="test", actor="operator")
    assert target.read_bytes() == changed
    assert conn.execute("SELECT status FROM engram WHERE id=?", (artifact.id,)).fetchone()[0] != "archived"


def test_real_signed_bundle_binds_transformed_publication_and_source_only_signature(
    enrolled, tmp_path, monkeypatch
):
    import copy
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from magicite.core import bundles, registry, trust_artifacts
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.errors import InvalidInputError

    cfg, conn, store = enrolled
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust.pin_trust_root(cfg, public_key_bytes=public)
    source = tmp_path / "bundle-source"
    source.mkdir()
    raw = (
        Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    ).read_bytes()
    (source / "sample.egr.md").write_bytes(raw)
    archive = tmp_path / "signed.zip"
    bundles.write_signed_bundle(source_dir=source, out_path=archive, private_key=key)
    captured = []
    original = registry._write_publish_journal

    def capture(cfg, path, body):
        captured.append(copy.deepcopy(body))
        return original(cfg, path, body)

    monkeypatch.setattr(registry, "_write_publish_journal", capture)
    embedder = get_embedder(dim=256)
    outcome = registry.import_bundle(cfg, conn, embedder, archive_path=archive)
    assert outcome.ingested == 1, outcome.validation_errors
    target = cfg.registry_dir / "sample.egr.md"
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    assert target.read_bytes() != raw
    member = next(item for item in captured[0]["members"] if item["path"] == "sample.egr.md")
    assert member["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert member["size"] == len(target.read_bytes())
    lineage = next(r["payload"] for r in store.committed_records("r") if r["kind"] == "artifact_transform")
    assert lineage["signature_provenance"]["source"]["signature_valid"] is True
    assert lineage["signature_provenance"]["target_signature_valid"] is False
    pending = trust.latest_decision_for(cfg, artifact.id)
    assert pending.decision == "pending" and pending.signature_valid is not True
    approved = registry.review_approve(
        cfg, conn, engram_id=artifact.id, expected_digest=artifact.content_sha256, actor="operator"
    )
    assert approved.signature_valid is not True
    repeated = registry.import_bundle(cfg, conn, embedder, archive_path=archive)
    assert not repeated.validation_errors
    assert hashlib.sha256(target.read_bytes()).hexdigest() == artifact.content_sha256
    trust.revoke_trust_root(cfg, fingerprint=bundles.public_key_fingerprint(public))
    with pytest.raises(InvalidInputError):
        registry.review_approve(
            cfg, conn, engram_id=artifact.id, expected_digest=artifact.content_sha256, actor="operator"
        )


def test_late_verified_source_provenance_restricts_existing_descendant(enrolled):
    from types import SimpleNamespace

    from magicite.core import lifecycle, registry, trust_artifacts, writer_guard
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, _ = enrolled
    raw = (
        Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    ).read_bytes()
    target = cfg.registry_dir / "sample.egr.md"
    target.write_bytes(raw)
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    original = trust_artifacts.require_bound_artifact(cfg, target)
    lifecycle.execute_sharpen(
        cfg,
        conn,
        embedder,
        name=original.name,
        proposed_changes=SimpleNamespace(procedures=["New authored descendant."], triggers=[], pitfalls=[]),
        actor="operator",
    )
    descendant = trust_artifacts.require_bound_artifact(cfg, target)
    later = trust_artifacts.mark_artifact(
        raw,
        registry_id="r",
        relpath=str(target),
        actor="verified-later",
        source_signature={"signature_valid": True, "signer_fingerprint": "a" * 64},
    )
    with writer_guard.registry_writer_lease(cfg, conn).acquire():
        trust_artifacts.bind_prepared_transform(cfg, later)
    snapshot = trust.authenticated_snapshot(cfg)
    assert "a" * 64 in snapshot.source_signers[(descendant.id, descendant.content_sha256)]


def test_bundle_failure_after_target_publication_compensates_exact_marked_bytes(
    enrolled, tmp_path, monkeypatch
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from magicite.core import bundles, registry, trust_artifacts, trust_journal
    from magicite.embeddings.hashing_provider import get_embedder

    cfg, conn, _ = enrolled
    key = Ed25519PrivateKey.generate()
    trust.pin_trust_root(cfg, public_key_bytes=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    source = tmp_path / "source"
    source.mkdir()
    raw = (
        Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    ).read_bytes()
    (source / "sample.egr.md").write_bytes(raw)
    archive = tmp_path / "signed.zip"
    bundles.write_signed_bundle(source_dir=source, out_path=archive, private_key=key)
    original = trust_journal._replace_file

    def fail_after(directory, name, content, *args, **kwargs):
        original(directory, name, content, *args, **kwargs)
        if name == "sample.egr.md":
            raise RuntimeError("injected publication stop")

    monkeypatch.setattr(trust_journal, "_replace_file", fail_after)
    with pytest.raises(RuntimeError, match="publication stop"):
        registry.import_bundle(cfg, conn, get_embedder(dim=256), archive_path=archive)
    assert not (cfg.registry_dir / "sample.egr.md").exists()
    assert conn.execute("SELECT count(*) FROM engram").fetchone()[0] == 0
    assert list((cfg.data_dir / "quarantine").rglob("sample.egr.md"))
    assert (source / "sample.egr.md").read_bytes() == raw
    monkeypatch.setattr(trust_journal, "_replace_file", original)
    outcome = registry.import_bundle(cfg, conn, get_embedder(dim=256), archive_path=archive)
    assert outcome.ingested == 1
    artifact = trust_artifacts.require_bound_artifact(cfg, cfg.registry_dir / "sample.egr.md")
    assert not registry.trust_view_for(cfg, conn, engram_id=artifact.id).admitted
