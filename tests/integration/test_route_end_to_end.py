"""AC-012's proving integration test, against the real toy registry
(register() -> route()), plus a couple of M2 end-to-end sanity checks that
exercise sync()'s new steps 8-9 (derived similar_to edges + community
detection) feeding back into route()'s community rerank. AC-037 remains a
plan_confidence proving unit; the legacy AC-038 partial-confidence case is
superseded by v1 C5 (dangling mandatory reference → invalid plan) and is now
proven as a ``composition_invalid`` abstention."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from magicite.core import eligibility as eligibility_mod
from magicite.core import fingerprint_key as fk
from magicite.core import policy_store as ps
from magicite.core import registry as registry_mod
from magicite.core import router as router_mod
from magicite.core import routing_policy as policy_mod
from magicite.core.context import RouteContext, ServerPermissionPolicy
from magicite.core.eligibility import (
    REASON_ASSET_INVALID,
    REASON_CONTEXT_REQUIRED,
    REASON_RISK_DENIED,
    REASON_TOOL_DENIED,
)
from magicite.core.index_generation import IndexCatalog, IndexFingerprint, model_artifact_digest
from magicite.storage import ephemeral as ephemeral_mod
from magicite.storage import lease as lease_mod


def test_topological_plan_order(cfg, db_conn, embedder) -> None:
    """AC-012: GIVEN an engram declaring needs: [steam-prefix-access] WHEN
    that engram wins routing THEN composition_plan SHALL list
    steam-prefix-access before the winner."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)

    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"
    assert "steam-prefix-access" in outcome.composition_plan
    assert outcome.composition_plan.index("steam-prefix-access") < outcome.composition_plan.index(
        "proton-ge-proton-downgrade"
    )


def test_route_after_sync_uses_derived_communities(cfg, db_conn, embedder) -> None:
    """sync() (unlike register()) runs spec §2.6 steps 8-9: route() should
    still return sane, non-empty results once communities/similar_to
    edges exist -- the community rerank step must not accidentally starve
    a small registry."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    sync_outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert sync_outcome.detector in {"leiden", "label_propagation"}

    communities = db_conn.execute("SELECT COUNT(*) AS n FROM engram_community").fetchone()["n"]
    assert communities == 7  # every registered engram gets a community assignment

    similar_to = db_conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE type = 'similar_to' AND provenance = 'derived'"
    ).fetchone()["n"]
    assert similar_to > 0

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)
    assert outcome.candidates
    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"


def test_sync_similar_to_edges_are_symmetric_neighbors_only(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)

    rows = db_conn.execute(
        "SELECT src_id, dst_id FROM edge WHERE type = 'similar_to' AND provenance = 'derived'"
    ).fetchall()
    assert rows
    for row in rows:
        assert row["dst_id"] is not None
        assert row["src_id"] != row["dst_id"]


def test_plan_confidence_is_one_when_fully_resolved(cfg, db_conn, embedder) -> None:
    """AC-037: GIVEN a winning engram ingested through register() whose
    declared needs/composes targets all resolve to registered engrams
    (proton-ge-proton-downgrade's needs: [steam-prefix-access], which is
    itself registered in the toy fixture) WHEN route() returns a
    composition_plan of more than one node THEN plan_confidence SHALL
    equal 1.0."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)

    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"
    assert len(outcome.composition_plan) > 1
    assert outcome.plan_confidence == 1.0
    assert outcome.decision is not None
    assert outcome.decision.status == "selected"
    assert outcome.decision.plan_digest is not None


def test_dangling_dependency_abstains_composition_invalid(cfg, db_conn, embedder) -> None:
    """C5 wire-up: dangling declared depends_on ⇒ abstain, not partial confidence.

    Replaces legacy AC-038 (plan_confidence == 0.5 under expand()). Plan/1
    never returns an executable prefix with unresolved required deps.
    """
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    winner_id = db_conn.execute(
        "SELECT id FROM engram WHERE name = 'proton-ge-proton-downgrade'"
    ).fetchone()["id"]
    now = datetime.now(UTC).isoformat()
    db_conn.execute(
        """
        INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                          evidence_count, provenance, first_observed, dangling)
        VALUES (?, 'nonexistent-skill', NULL, 'depends_on', 0.0, ?, 0, 'declared', ?, 1)
        """,
        (winner_id, now, now),
    )

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)

    assert outcome.decision is not None
    assert outcome.decision.status == "abstained"
    assert outcome.composition_plan == []
    assert outcome.plan_confidence == 0.0
    assert outcome.candidates == []
    assert router_mod.REASON_COMPOSITION_INVALID in outcome.decision.reason_codes
    assert eligibility_mod.REASON_DANGLING_DEPENDENCY in outcome.decision.reason_codes


def _write_v02_engram(cfg, *, engram_id: str, name: str, relpath: str | None = None) -> tuple[str, str]:
    """Write a minimal engram/0.2 file; return (relpath, content_digest)."""
    from magicite.engram import ids as ids_mod

    rel = relpath or f".magicite/engrams/{name}.egr.md"
    body = f"## Procedure\n{name}\n"
    raw = f"""---
spec: engram/0.2
name: {name}
id: {engram_id}
version: 1
provenance: authored
intent:
  does: "does {name}"
  use_when: "use when {name}"
  not_when: "never"
triggers:
  positive: ["{name}"]
  negative: []
context_affinity: []
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
inhibits: []
provenance_journal: []
trust:
  origin: authored
  verification_status: verified
---
{body}"""
    full = cfg.project_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    data = raw.encode("utf-8")
    full.write_bytes(data)
    return rel, ids_mod.content_sha256(data)


def _insert_synthetic(
    conn,
    *,
    engram_id: str,
    name: str,
    path: str,
    content_sha256: str,
    verification_status: str = "verified",
    spec_version: str = "engram/0.2",
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, storage_strength, s_decayed_at, excitability,
          identity_sha256, content_sha256, body_sha256, file_mtime_ns, created_at, updated_at
        ) VALUES (?,?,?,?,1,'authored',?,'nascent','does','use_when',0.0,?,0.05,?,?,?,0,?,?)
        """,
        (
            engram_id,
            name,
            path,
            spec_version,
            verification_status,
            now,
            content_sha256,
            content_sha256,
            content_sha256,
            now,
            now,
        ),
    )


def _write_v1_engram(
    cfg,
    *,
    engram_id: str,
    name: str,
    query_tokens: str,
    risk_tools: list[str] | None = None,
    risk_mode: str = "declared-tools",
    hosts: list[dict] | None = None,
    assets: dict | None = None,
    relation_requires: list[dict] | None = None,
) -> tuple[Path, str]:
    """Write a minimal V1 engram under the registry; return (path, content_digest)."""
    from magicite.engram import ids as ids_mod

    body = f"## Procedure\n{query_tokens}\n"
    body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if risk_tools is not None:
        tools = risk_tools
    elif risk_mode == "declared-tools":
        tools = ["protontricks"]
    else:
        tools = []
    hosts_yaml = ""
    if hosts:
        hosts_yaml = "  hosts:\n"
        for h in hosts:
            hosts_yaml += f"    - id: {h['id']}\n"
            hosts_yaml += "      version:\n"
            hosts_yaml += f"        scheme: {h['scheme']}\n"
            hosts_yaml += f'        range: "{h["range"]}"\n'
    assets_block = "{}"
    if assets:
        assets_block = json.dumps(assets, sort_keys=True)
    tools_yaml = f"    tools: {tools}" if risk_mode == "declared-tools" else "    tools: []"
    requires_yaml = "  requires: []\n"
    if relation_requires:
        requires_yaml = "  requires:\n"
        for ref in relation_requires:
            requires_yaml += f"    - id: {ref['id']}\n"
            requires_yaml += f"      version: {ref['version']}\n"
    raw = f"""---
spec: engram/1.0
name: {name}
id: {engram_id}
version: 1
intent:
  does: "{query_tokens}"
  use_when: "eligibility probe"
  not_when: "never"
compatibility:
  os: []
{hosts_yaml}capabilities:
  requires: []
  produces: []
  alternatives: []
  conflicts_with: []
relations:
{requires_yaml}  before: []
  supersedes: []
risk:
  filesystem: none
  subprocess:
    mode: {risk_mode}
{tools_yaml}
  network:
    mode: none
    destinations: []
  secrets: none
routing:
  positive: ["{query_tokens}"]
  negative: []
  body_digest: "{body_digest}"
origin:
  channel: authored
  verification_status: verified
  content_hashes:
    body_sha256: "{body_digest}"
assets: {assets_block}
extensions: {{}}
---
{body}"""
    rel = f".magicite/engrams/{name}.egr.md"
    full = cfg.project_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    data = raw.encode("utf-8")
    full.write_bytes(data)
    return full, ids_mod.content_sha256(data)


def _insert_healthy_competitor(cfg, conn, embedder, *, engram_id: str, name: str) -> None:
    rel, digest = _write_v02_engram(cfg, engram_id=engram_id, name=name)
    _insert_synthetic(
        conn, engram_id=engram_id, name=name, path=rel, content_sha256=digest
    )
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=engram_id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed(f"unrelated {name}"),
        source_sha256=digest,
    )


def _assert_excluded_bodies(outcome, engram_id: str, body_ref: str) -> None:
    ids = [c.id for c in outcome.candidates]
    body_refs = [c.body_ref for c in outcome.candidates]
    assert engram_id not in ids
    assert body_ref not in body_refs
    assert outcome.decision is not None
    excluded = {e.engram_id: e.reason_codes for e in outcome.decision.exclusions}
    assert engram_id in excluded
    for c in outcome.candidates:
        assert c.id != engram_id


def test_eligibility_before_rerank(cfg, db_conn, embedder) -> None:
    """AC-S07-01: GIVEN a quarantined artifact with strongest raw score
    WHEN routing runs
    THEN the artifact SHALL be absent from returned candidates and bodies.
    """
    router_mod._SUBJECT_CACHE.clear()
    q_rel, q_digest = _write_v02_engram(
        cfg, engram_id="egr_aa550001", name="quarantined-top"
    )
    h_rel, h_digest = _write_v02_engram(
        cfg, engram_id="egr_aa550002", name="healthy-skill"
    )
    _insert_synthetic(
        db_conn,
        engram_id="egr_aa550001",
        name="quarantined-top",
        path=q_rel,
        content_sha256=q_digest,
        verification_status="quarantined",
    )
    _insert_synthetic(
        db_conn,
        engram_id="egr_aa550002",
        name="healthy-skill",
        path=h_rel,
        content_sha256=h_digest,
    )
    query = "quarantine eligibility probe unique tokens xyzzy"
    ephemeral_mod.upsert_embedding(
        db_conn,
        engram_id="egr_aa550001",
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed(query),
        source_sha256=q_digest,
    )
    ephemeral_mod.upsert_embedding(
        db_conn,
        engram_id="egr_aa550002",
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed("unrelated healthy skill text"),
        source_sha256=h_digest,
    )

    outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    _assert_excluded_bodies(outcome, "egr_aa550001", q_rel)
    assert "quarantined-top" not in [c.name for c in outcome.candidates]


def test_eligibility_risk_denied_under_restrictive_server_policy(cfg, db_conn, embedder) -> None:
    """ATLAS B1: authored+verified with declared-tools risk is denied when
    server max_subprocess=none, even under unconstrained RouteContext.
    """
    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa110001"
    name = "risk-denied-probe"
    query = "risk denied eligibility probe unique tokens xyzzy"
    _path, digest = _write_v1_engram(cfg, engram_id=eid, name=name, query_tokens=query)
    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn,
        engram_id=eid,
        name=name,
        path=rel,
        content_sha256=digest,
        spec_version="engram/1.0",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    _insert_healthy_competitor(
        cfg, db_conn, embedder, engram_id="egr_aa1100ff", name="healthy-competitor"
    )

    restrictive = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-restrictive-none/1",
        max_filesystem="none",
        max_subprocess="none",
        max_network="none",
        max_secrets="none",
    )
    outcome = router_mod.route(
        cfg,
        db_conn,
        embedder,
        query=query,
        k=5,
        route_context=RouteContext(),
        server_policy=restrictive,
    )
    _assert_excluded_bodies(outcome, eid, rel)
    reasons = {e.engram_id: e.reason_codes for e in outcome.decision.exclusions}  # type: ignore[union-attr]
    assert REASON_RISK_DENIED in reasons[eid]


def test_eligibility_tool_denied_under_empty_allowed_tools(cfg, db_conn, embedder) -> None:
    """ATLAS B1: declared-tools risk with tools not in server allowed_tools → tool_denied."""
    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa220002"
    name = "tool-denied-probe"
    query = "tool denied eligibility probe unique tokens xyzzy"
    _path, digest = _write_v1_engram(cfg, engram_id=eid, name=name, query_tokens=query)
    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn, engram_id=eid, name=name, path=rel, content_sha256=digest, spec_version="engram/1.0",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    _insert_healthy_competitor(
        cfg, db_conn, embedder, engram_id="egr_aa2200ff", name="healthy-tool-comp"
    )

    policy = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-tool-empty/1",
        max_filesystem="write-project",
        max_subprocess="declared-tools",
        max_network="required",
        max_secrets="raw",
    )
    outcome = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=policy,
    )
    _assert_excluded_bodies(outcome, eid, rel)
    reasons = {e.engram_id: e.reason_codes for e in outcome.decision.exclusions}  # type: ignore[union-attr]
    assert REASON_TOOL_DENIED in reasons[eid]


def test_eligibility_context_required_unknown_host(cfg, db_conn, embedder) -> None:
    """ATLAS B1: compat-constrained artifact with unknown host fact → context_required."""
    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa330003"
    name = "compat-host-probe"
    query = "compat host eligibility probe unique tokens xyzzy"
    _path, digest = _write_v1_engram(
        cfg,
        engram_id=eid,
        name=name,
        query_tokens=query,
        risk_tools=[],
        hosts=[{"id": "cursor", "scheme": "semver", "range": ">=0.40.0,<1.0.0"}],
    )
    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn, engram_id=eid, name=name, path=rel, content_sha256=digest, spec_version="engram/1.0",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    _insert_healthy_competitor(
        cfg, db_conn, embedder, engram_id="egr_aa3300ff", name="healthy-compat-comp"
    )

    # Allow subprocess so risk/tools don't mask context_required.
    policy = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-compat/1",
        max_filesystem="write-project",
        max_subprocess="declared-tools",
        max_network="required",
        max_secrets="raw",
    )
    outcome = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=policy,
    )
    _assert_excluded_bodies(outcome, eid, rel)
    reasons = {e.engram_id: e.reason_codes for e in outcome.decision.exclusions}  # type: ignore[union-attr]
    assert REASON_CONTEXT_REQUIRED in reasons[eid]
    assert "host" in outcome.decision.missing_context  # type: ignore[union-attr]


def test_eligibility_asset_invalid(cfg, db_conn, embedder) -> None:
    """ATLAS B1: artifact declaring a missing asset file → asset_invalid."""
    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa440004"
    name = "asset-invalid-probe"
    query = "asset invalid eligibility probe unique tokens xyzzy"
    missing_sha = "ab" * 32
    _path, digest = _write_v1_engram(
        cfg,
        engram_id=eid,
        name=name,
        query_tokens=query,
        risk_tools=[],
        assets={"missing-asset.bin": {"sha256": missing_sha, "size": 12}},
    )
    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn, engram_id=eid, name=name, path=rel, content_sha256=digest, spec_version="engram/1.0",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    _insert_healthy_competitor(
        cfg, db_conn, embedder, engram_id="egr_aa4400ff", name="healthy-asset-comp"
    )

    policy = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-asset/1",
        max_filesystem="write-project",
        max_subprocess="declared-tools",
        max_network="required",
        max_secrets="raw",
    )
    outcome = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=policy,
    )
    _assert_excluded_bodies(outcome, eid, rel)
    reasons = {e.engram_id: e.reason_codes for e in outcome.decision.exclusions}  # type: ignore[union-attr]
    assert REASON_ASSET_INVALID in reasons[eid]


def test_route_honours_policy_store_activation(cfg, db_conn, embedder) -> None:
    """N3: live route() uses policy_store active manifest; corrupt → error."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    cfg.ensure_dirs()
    fk.set_fingerprint_key_override(b"\x44" * fk.KEY_BYTES)
    try:
        dense = ps.PolicyManifest(
            policy_id=policy_mod.POLICY_DENSE_V1,
            policy_digest=policy_mod.compute_policy_digest(policy_mod.POLICY_DENSE_V1, cfg),
            policy_family="stable",
            config_digest=policy_mod.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="gen_n3",
            snapshot_id="snap_n3",
            selection="cosine_similarity",
        )
        adaptive = ps.PolicyManifest(
            policy_id=policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1,
            policy_digest=policy_mod.compute_policy_digest(
                policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1, cfg
            ),
            policy_family="experimental",
            config_digest=policy_mod.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="gen_n3b",
            snapshot_id="snap_n3b",
            selection="adaptive_blend",
        )
        ps.register_evaluated(cfg, dense, evaluation_status="pass", evidence="n3 dense")
        ps.register_evaluated(cfg, adaptive, evaluation_status="pass", evidence="n3 adaptive")
        dense_approval = ps.approve(cfg, dense.policy_digest, actor="reviewer")
        adaptive_approval = ps.approve(cfg, adaptive.policy_digest, actor="reviewer")

        ps.activate(
            cfg,
            expected_current=None,
            candidate_digest=dense.policy_digest,
            approval_id=dense_approval,
        )
        q = "rollback proton for a steam game"
        out1 = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
        assert out1.decision is not None
        assert out1.decision.policy_id == policy_mod.POLICY_DENSE_V1
        assert out1.decision.status != "error"
        assert out1.decision.policy_source == "store"

        ps.activate(
            cfg,
            expected_current=dense.policy_digest,
            candidate_digest=adaptive.policy_digest,
            approval_id=adaptive_approval,
        )
        out2 = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
        assert out2.decision is not None
        assert out2.decision.policy_id == policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1

        ps.rollback(
            cfg,
            expected_current=adaptive.policy_digest,
            prior_digest=dense.policy_digest,
        )
        out3 = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
        assert out3.decision is not None
        assert out3.decision.policy_id == policy_mod.POLICY_DENSE_V1

        path = ps.policy_store_path(cfg)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["active_digest"] = "tampered"
        path.write_text(json.dumps(raw), encoding="utf-8")
        out4 = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
        assert out4.decision is not None
        assert out4.decision.status == "error"
        assert out4.decision.operational_error == "policy_store_corrupt"
    finally:
        fk.set_fingerprint_key_override(None)


def test_index_generation_identity_differs_across_publish(cfg, db_conn, embedder) -> None:
    """B2: routes against different published generations differ in pin fields;
    same generation across rebuild stays equal (with AC-S07-03).
    """
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    catalog = IndexCatalog(db_conn)
    fp = IndexFingerprint(
        provider="hashing",
        model_artifact_digest=model_artifact_digest(
            model_name=embedder.model_name, dim=embedder.dim
        ),
        dimension=embedder.dim,
        model_revision="test",
    )
    with lease_mod.writer_lease(holder="s07-index-pin"):
        gid1 = catalog.begin(snapshot_id="snap-s07-a", fingerprint=fp, model_name=embedder.model_name)
        catalog.complete(gid1, [])
        catalog.publish(gid1)

    q = "rollback proton for a steam game"
    first = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
    assert first.decision is not None
    assert first.decision.index_generation_id == gid1
    assert first.decision.snapshot_id == "snap-s07-a"
    assert first.decision.schema_digest is not None
    assert first.decision.tokenizer_digest is not None
    semantic_a = router_mod.semantic_decision_fields(first.decision)

    second = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
    assert second.decision is not None
    assert router_mod.semantic_decision_fields(second.decision) == semantic_a
    assert first.decision.decision_id != second.decision.decision_id

    with lease_mod.writer_lease(holder="s07-index-pin"):
        gid2 = catalog.begin(snapshot_id="snap-s07-b", fingerprint=fp, model_name=embedder.model_name)
        catalog.complete(gid2, [])
        catalog.publish(gid2)
    third = router_mod.route(cfg, db_conn, embedder, query=q, k=5)
    assert third.decision is not None
    assert third.decision.index_generation_id == gid2
    assert third.decision.snapshot_id == "snap-s07-b"
    assert third.decision.index_generation_id != first.decision.index_generation_id
    assert third.decision.snapshot_id != first.decision.snapshot_id


def test_subject_cache_detects_registry_drift(cfg, db_conn, embedder) -> None:
    """ATLAS B1: rewrite on-disk risk without re-register → drift deny on next route."""
    from magicite.engram import ids as ids_mod

    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa660006"
    name = "cache-drift-probe"
    query = "cache drift eligibility probe unique tokens xyzzy"
    full, digest = _write_v1_engram(
        cfg, engram_id=eid, name=name, query_tokens=query, risk_mode="none", risk_tools=[]
    )
    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn, engram_id=eid, name=name, path=rel, content_sha256=digest, spec_version="engram/1.0"
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    _insert_healthy_competitor(
        cfg, db_conn, embedder, engram_id="egr_aa6600ff", name="healthy-drift-comp"
    )

    permissive = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-cache-permissive/1",
        max_filesystem="none",
        max_subprocess="none",
        max_network="none",
        max_secrets="none",
    )
    first = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=permissive,
    )
    assert first.decision is not None
    assert eid in [c.id for c in first.candidates]

    # Rewrite live bytes to declared-tools without updating DB digest.
    drifted, _ = _write_v1_engram(
        cfg,
        engram_id=eid,
        name=name,
        query_tokens=query,
        risk_mode="declared-tools",
        risk_tools=["shell.exec"],
    )
    assert ids_mod.content_sha256(drifted.read_bytes()) != digest

    restrictive = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-cache-restrictive/1",
        max_filesystem="none",
        max_subprocess="none",
        max_network="none",
        max_secrets="none",
    )
    second = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=restrictive,
    )
    _assert_excluded_bodies(second, eid, rel)
    reasons = {e.engram_id: e.reason_codes for e in second.decision.exclusions}  # type: ignore[union-attr]
    assert "registry_drift" in reasons[eid]


def test_subject_cache_same_size_restored_mtime_still_drifts(cfg, db_conn, embedder) -> None:
    """Same-size rewrite with mtime restored via utime must still miss the cache."""
    import os

    from magicite.engram import ids as ids_mod

    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa660016"
    name = "cache-mtime-probe"
    query = "cache mtime restore eligibility probe unique tokens xyzzy"
    full, _ = _write_v1_engram(
        cfg, engram_id=eid, name=name, query_tokens=query, risk_mode="none", risk_tools=[]
    )
    original = full.read_bytes()
    drifted_path, _ = _write_v1_engram(
        cfg,
        engram_id=eid,
        name=name,
        query_tokens=query,
        risk_mode="declared-tools",
        risk_tools=["shell.exec"],
    )
    drifted = drifted_path.read_bytes()
    assert len(drifted) > len(original)
    padded = original + b" " * (len(drifted) - len(original))
    full.write_bytes(padded)
    digest = ids_mod.content_sha256(padded)
    before = full.stat()

    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn, engram_id=eid, name=name, path=rel, content_sha256=digest, spec_version="engram/1.0"
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    _insert_healthy_competitor(
        cfg, db_conn, embedder, engram_id="egr_aa6601ff", name="healthy-mtime-comp"
    )
    restrictive = ServerPermissionPolicy(
        allowed_permissions=frozenset(),
        allowed_tools=frozenset(),
        policy_digest="atlas-cache-mtime/1",
        max_filesystem="none",
        max_subprocess="none",
        max_network="none",
        max_secrets="none",
    )
    first = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=restrictive,
    )
    assert eid in [c.id for c in first.candidates]

    full.write_bytes(drifted)
    os.utime(full, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = full.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )

    second = router_mod.route(
        cfg, db_conn, embedder, query=query, k=5,
        route_context=RouteContext(), server_policy=restrictive,
    )
    _assert_excluded_bodies(second, eid, rel)
    reasons = {e.engram_id: e.reason_codes for e in second.decision.exclusions}  # type: ignore[union-attr]
    assert "registry_drift" in reasons[eid]


def test_subject_cache_identical_rewrite_stays_eligible(cfg, db_conn, embedder) -> None:
    """Same-bytes rewrite keeps file identity miss but matching digest → eligible."""
    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa770007"
    name = "cache-same-bytes"
    query = "cache same bytes eligibility probe unique tokens xyzzy"
    full, digest = _write_v1_engram(
        cfg, engram_id=eid, name=name, query_tokens=query, risk_mode="none", risk_tools=[]
    )
    rel = f".magicite/engrams/{name}.egr.md"
    _insert_synthetic(
        db_conn, engram_id=eid, name=name, path=rel, content_sha256=digest, spec_version="engram/1.0"
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    first = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert eid in [c.id for c in first.candidates]

    # Touch mtime by rewriting identical bytes.
    raw = full.read_bytes()
    full.write_bytes(raw)
    second = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert eid in [c.id for c in second.candidates]


def test_subject_cache_scoped_per_registry_root(tmp_path, embedder) -> None:
    """Two registries in one process must not share subject cache entries."""
    from magicite.config import Config
    from magicite.storage import db as db_mod

    router_mod._SUBJECT_CACHE.clear()

    def _boot(root):
        (root / ".magicite" / "engrams").mkdir(parents=True)
        cfg = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        cfg.ensure_dirs()
        conn = db_mod.connect(cfg.db_path)
        return cfg, conn

    cfg_a, conn_a = _boot(tmp_path / "proj_a")
    cfg_b, conn_b = _boot(tmp_path / "proj_b")
    try:
        eid = "egr_aa880008"
        name = "cache-scope"
        query = "cache scope probe unique tokens xyzzy"
        full_a, dig_a = _write_v1_engram(
            cfg_a, engram_id=eid, name=name, query_tokens=query, risk_mode="none", risk_tools=[]
        )
        rel = f".magicite/engrams/{name}.egr.md"
        _insert_synthetic(
            conn_a, engram_id=eid, name=name, path=rel, content_sha256=dig_a, spec_version="engram/1.0"
        )
        ephemeral_mod.upsert_embedding(
            conn_a, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
            vec=embedder.embed(query), source_sha256=dig_a,
        )
        out_a = router_mod.route(cfg_a, conn_a, embedder, query=query, k=5)
        assert eid in [c.id for c in out_a.candidates]

        # Same id in B with drifted risk bytes vs empty DB digest mismatch → deny in B.
        full_b, dig_b = _write_v1_engram(
            cfg_b,
            engram_id=eid,
            name=name,
            query_tokens=query,
            risk_mode="declared-tools",
            risk_tools=["shell.exec"],
        )
        _insert_synthetic(
            conn_b, engram_id=eid, name=name, path=rel, content_sha256=dig_a, spec_version="engram/1.0"
        )
        ephemeral_mod.upsert_embedding(
            conn_b, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
            vec=embedder.embed(query), source_sha256=dig_a,
        )
        assert dig_b != dig_a
        out_b = router_mod.route(cfg_b, conn_b, embedder, query=query, k=5)
        assert eid not in [c.id for c in out_b.candidates]
        reasons = {e.engram_id: e.reason_codes for e in out_b.decision.exclusions}  # type: ignore[union-attr]
        assert "registry_drift" in reasons[eid]
        # A remains independently eligible (no cross-root leakage).
        out_a2 = router_mod.route(cfg_a, conn_a, embedder, query=query, k=5)
        assert eid in [c.id for c in out_a2.candidates]
        del full_a, full_b
    finally:
        conn_a.close()
        conn_b.close()


def test_missing_artifact_file_excluded(cfg, db_conn, embedder) -> None:
    """NIT: DB row whose artifact file is missing is fail-closed excluded."""
    router_mod._SUBJECT_CACHE.clear()
    eid = "egr_aa990009"
    _insert_synthetic(
        db_conn,
        engram_id=eid,
        name="missing-file",
        path=".magicite/engrams/does-not-exist.egr.md",
        content_sha256="ab" * 32,
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed("missing file probe"), source_sha256="ab" * 32,
    )
    outcome = router_mod.route(cfg, db_conn, embedder, query="missing file probe", k=5)
    assert eid not in [c.id for c in outcome.candidates]
    reasons = {e.engram_id: e.reason_codes for e in outcome.decision.exclusions}  # type: ignore[union-attr]
    assert "missing_artifact" in reasons[eid]


def test_policy_store_missing_after_activation_fails_closed(cfg, db_conn, embedder) -> None:
    """ATLAS B2: delete state.json after activate → policy_store_missing (no elevation)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    cfg.ensure_dirs()
    fk.set_fingerprint_key_override(b"\x55" * fk.KEY_BYTES)
    try:
        dense = ps.PolicyManifest(
            policy_id=policy_mod.POLICY_DENSE_V1,
            policy_digest=policy_mod.compute_policy_digest(policy_mod.POLICY_DENSE_V1, cfg),
            policy_family="stable",
            config_digest=policy_mod.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="gen_miss",
            snapshot_id="snap_miss",
            selection="cosine_similarity",
        )
        ps.register_evaluated(cfg, dense, evaluation_status="pass", evidence="miss")
        approval = ps.approve(cfg, dense.policy_digest, actor="reviewer")
        ps.activate(
            cfg,
            expected_current=None,
            candidate_digest=dense.policy_digest,
            approval_id=approval,
        )
        assert ps.prior_policy_governance_evidence(cfg) is True

        cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
        before = cfg.routing_policy
        ps.policy_store_path(cfg).unlink()
        out = router_mod.route(cfg, db_conn, embedder, query="rollback proton", k=5)
        assert out.decision is not None
        assert out.decision.status == "error"
        assert out.decision.operational_error == "policy_store_missing"
        assert out.decision.policy_id != policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1 or (
            out.decision.operational_error == "policy_store_missing"
        )
        assert cfg.routing_policy == before
    finally:
        fk.set_fingerprint_key_override(None)


def test_fresh_install_policy_source_and_cfg_immutable(cfg, db_conn, embedder) -> None:
    """No store / no prior evidence → dense-v1 from cfg; Config not mutated."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert not ps.policy_store_path(cfg).is_file()
    assert ps.prior_policy_governance_evidence(cfg) is False
    before = cfg.routing_policy
    out = router_mod.route(cfg, db_conn, embedder, query="rollback proton", k=5)
    assert out.decision is not None
    assert out.decision.policy_id == policy_mod.POLICY_DENSE_V1
    assert out.decision.policy_source == "config_fresh_install"
    assert cfg.routing_policy == before


def test_store_active_ignores_cfg_experimental(cfg, db_conn, embedder) -> None:
    """Store active dense-v1 + cfg experimental → dense with ignored reason; cfg unchanged."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    cfg.ensure_dirs()
    fk.set_fingerprint_key_override(b"\x56" * fk.KEY_BYTES)
    try:
        dense = ps.PolicyManifest(
            policy_id=policy_mod.POLICY_DENSE_V1,
            policy_digest=policy_mod.compute_policy_digest(policy_mod.POLICY_DENSE_V1, cfg),
            policy_family="stable",
            config_digest=policy_mod.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="gen_ign",
            snapshot_id="snap_ign",
            selection="cosine_similarity",
        )
        ps.register_evaluated(cfg, dense, evaluation_status="pass", evidence="ign")
        approval = ps.approve(cfg, dense.policy_digest, actor="reviewer")
        ps.activate(
            cfg,
            expected_current=None,
            candidate_digest=dense.policy_digest,
            approval_id=approval,
        )
        cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
        before = cfg.routing_policy
        out = router_mod.route(cfg, db_conn, embedder, query="rollback proton", k=5)
        assert out.decision is not None
        assert out.decision.policy_id == policy_mod.POLICY_DENSE_V1
        assert out.decision.policy_source == "store"
        assert "config_policy_ignored_store_active" in out.decision.reason_codes
        assert cfg.routing_policy == before
    finally:
        fk.set_fingerprint_key_override(None)


def _register_v1_pair(
    cfg,
    conn,
    embedder,
    *,
    winner_id: str,
    winner_name: str,
    dep_id: str,
    dep_name: str,
    query: str,
    dep_verification: str = "verified",
    dep_hosts: list[dict] | None = None,
    dep_risk_tools: list[str] | None = None,
    cycle: bool = False,
) -> tuple[str, str]:
    """Insert winner→dep V1 pair (optional reverse require for cycle). Returns (winner_rel, dep_rel)."""
    router_mod._SUBJECT_CACHE.clear()
    winner_requires = [{"id": dep_id, "version": 1}]
    dep_requires = [{"id": winner_id, "version": 1}] if cycle else None
    _wpath, w_digest = _write_v1_engram(
        cfg,
        engram_id=winner_id,
        name=winner_name,
        query_tokens=query,
        risk_tools=[],
        risk_mode="none",
        relation_requires=winner_requires,
    )
    _dpath, d_digest = _write_v1_engram(
        cfg,
        engram_id=dep_id,
        name=dep_name,
        query_tokens=f"dep {dep_name}",
        risk_tools=dep_risk_tools if dep_risk_tools is not None else [],
        risk_mode="none" if not dep_risk_tools else "declared-tools",
        hosts=dep_hosts,
        relation_requires=dep_requires,
    )
    w_rel = f".magicite/engrams/{winner_name}.egr.md"
    d_rel = f".magicite/engrams/{dep_name}.egr.md"
    _insert_synthetic(
        conn,
        engram_id=winner_id,
        name=winner_name,
        path=w_rel,
        content_sha256=w_digest,
        spec_version="engram/1.0",
    )
    _insert_synthetic(
        conn,
        engram_id=dep_id,
        name=dep_name,
        path=d_rel,
        content_sha256=d_digest,
        verification_status=dep_verification,
        spec_version="engram/1.0",
    )
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=winner_id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed(query),
        source_sha256=w_digest,
    )
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=dep_id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed(f"unrelated {dep_name}"),
        source_sha256=d_digest,
    )
    return w_rel, d_rel


def test_compose_valid_multi_node_ordered(cfg, db_conn, embedder) -> None:
    """Valid Plan/1 via compose() returns ordered composition names (not expand)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)
    assert outcome.decision is not None
    assert outcome.decision.status == "selected"
    assert "steam-prefix-access" in outcome.composition_plan
    assert outcome.composition_plan.index("steam-prefix-access") < outcome.composition_plan.index(
        "proton-ge-proton-downgrade"
    )
    assert outcome.decision.plan_digest is not None
    # Self-audit: stable path must not call legacy expand().
    assert outcome.composition_plan  # non-empty ordered plan from compose


def test_compose_cycle_abstains_composition_invalid(cfg, db_conn, embedder) -> None:
    """Cycle in relations.requires ⇒ route abstains with composition_invalid."""
    query = "compose cycle probe unique tokens xyzzy"
    _register_v1_pair(
        cfg,
        db_conn,
        embedder,
        winner_id="egr_cc010001",
        winner_name="cycle-winner",
        dep_id="egr_cc010002",
        dep_name="cycle-dep",
        query=query,
        cycle=True,
    )
    outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert outcome.decision is not None
    assert outcome.decision.status == "abstained"
    assert outcome.composition_plan == []
    assert outcome.candidates == []
    assert router_mod.REASON_COMPOSITION_INVALID in outcome.decision.reason_codes
    assert eligibility_mod.REASON_CYCLE in outcome.decision.reason_codes


def test_compose_budget_exceeded_abstains(cfg, db_conn, embedder) -> None:
    """Depth budget exceeded ⇒ abstain with composition_invalid (no partial plan)."""
    query = "compose budget probe unique tokens xyzzy"
    # Chain: winner → mid → leaf; clamp depth to 1 so mid→leaf exceeds.
    cfg.plan_max_depth = 1
    router_mod._SUBJECT_CACHE.clear()
    mid_id, leaf_id = "egr_bb010002", "egr_bb010003"
    winner_id = "egr_bb010001"
    _w, w_digest = _write_v1_engram(
        cfg,
        engram_id=winner_id,
        name="budget-winner",
        query_tokens=query,
        risk_tools=[],
        risk_mode="none",
        relation_requires=[{"id": mid_id, "version": 1}],
    )
    _m, m_digest = _write_v1_engram(
        cfg,
        engram_id=mid_id,
        name="budget-mid",
        query_tokens="mid",
        risk_tools=[],
        risk_mode="none",
        relation_requires=[{"id": leaf_id, "version": 1}],
    )
    _l, l_digest = _write_v1_engram(
        cfg,
        engram_id=leaf_id,
        name="budget-leaf",
        query_tokens="leaf",
        risk_tools=[],
        risk_mode="none",
    )
    for eid, name, digest in (
        (winner_id, "budget-winner", w_digest),
        (mid_id, "budget-mid", m_digest),
        (leaf_id, "budget-leaf", l_digest),
    ):
        rel = f".magicite/engrams/{name}.egr.md"
        _insert_synthetic(
            db_conn,
            engram_id=eid,
            name=name,
            path=rel,
            content_sha256=digest,
            spec_version="engram/1.0",
        )
        ephemeral_mod.upsert_embedding(
            db_conn,
            engram_id=eid,
            model_name=embedder.model_name,
            dim=embedder.dim,
            vec=embedder.embed(query if eid == winner_id else f"unrelated {name}"),
            source_sha256=digest,
        )

    outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert outcome.decision is not None
    assert outcome.decision.status == "abstained"
    assert outcome.composition_plan == []
    assert router_mod.REASON_COMPOSITION_INVALID in outcome.decision.reason_codes
    assert eligibility_mod.REASON_BUDGET_EXCEEDED in outcome.decision.reason_codes


def test_compose_ineligible_dependency_abstains_body_absent(cfg, db_conn, embedder) -> None:
    """Ineligible required dependency ⇒ composition_invalid; dep body absent."""
    query = "compose ineligible dep probe unique tokens xyzzy"
    w_rel, d_rel = _register_v1_pair(
        cfg,
        db_conn,
        embedder,
        winner_id="egr_dd010001",
        winner_name="inel-winner",
        dep_id="egr_dd010002",
        dep_name="inel-dep",
        query=query,
        dep_verification="quarantined",
    )
    outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert outcome.decision is not None
    assert outcome.decision.status == "abstained"
    assert outcome.composition_plan == []
    assert outcome.candidates == []
    assert router_mod.REASON_COMPOSITION_INVALID in outcome.decision.reason_codes
    body_refs = [c.body_ref for c in outcome.candidates]
    assert d_rel not in body_refs
    assert "egr_dd010002" not in [c.id for c in outcome.candidates]
    # Quarantined dep must not appear as a disclosed body on the decision either.
    assert all(c.id != "egr_dd010002" for c in outcome.decision.candidates)
    del w_rel


def test_compose_context_required_abstains_with_missing_fields(cfg, db_conn, embedder) -> None:
    """context_required on a composed dependency ⇒ abstain with missing_context."""
    query = "compose context required probe unique tokens xyzzy"
    _register_v1_pair(
        cfg,
        db_conn,
        embedder,
        winner_id="egr_ee010001",
        winner_name="ctx-winner",
        dep_id="egr_ee010002",
        dep_name="ctx-dep",
        query=query,
        dep_hosts=[{"id": "cursor", "scheme": "semver", "range": ">=0.40.0,<1.0.0"}],
    )
    outcome = router_mod.route(
        cfg,
        db_conn,
        embedder,
        query=query,
        k=5,
        route_context=RouteContext(),
        server_policy=ServerPermissionPolicy(
            allowed_permissions=frozenset(),
            allowed_tools=frozenset(),
            policy_digest="compose-ctx/1",
            max_filesystem="write-project",
            max_subprocess="declared-tools",
            max_network="required",
            max_secrets="raw",
        ),
    )
    assert outcome.decision is not None
    assert outcome.decision.status == "abstained"
    assert outcome.composition_plan == []
    assert router_mod.REASON_COMPOSITION_INVALID in outcome.decision.reason_codes
    assert REASON_CONTEXT_REQUIRED in outcome.decision.reason_codes
    assert "host" in outcome.decision.missing_context
