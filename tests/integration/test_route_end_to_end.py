"""AC-012's proving integration test, against the real toy registry
(register() -> route()), plus a couple of M2 end-to-end sanity checks that
exercise sync()'s new steps 8-9 (derived similar_to edges + community
detection) feeding back into route()'s community rerank. AC-037/AC-038
(DECLARED-EDGES-AMENDED, 2026-08-15) are the amended plan_confidence's
proving units."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

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


def test_plan_confidence_reports_the_unresolved_share(cfg, db_conn, embedder) -> None:
    """AC-038: GIVEN a winning engram declaring exactly two needs targets
    of which exactly one is registered WHEN route() returns its
    composition_plan THEN plan_confidence SHALL equal 0.5.

    proton-ge-proton-downgrade already declares needs: [steam-prefix-
    access] (registered, spec §2.6-ingested via register()); one
    additional depends_on edge naming a target no .egr.md declares is
    inserted directly so the winner has exactly two needs targets, one
    resolved."""
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

    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"
    assert outcome.plan_confidence == 0.5


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
    hosts: list[dict] | None = None,
    assets: dict | None = None,
) -> tuple[Path, str]:
    """Write a minimal V1 engram under the registry; return (path, content_digest)."""
    body = f"## Procedure\n{query_tokens}\n"
    body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    tools = risk_tools if risk_tools is not None else ["protontricks"]
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
  requires: []
  before: []
  supersedes: []
risk:
  filesystem: none
  subprocess:
    mode: declared-tools
    tools: {tools}
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
    full.write_text(raw, encoding="utf-8")
    content_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return full, content_digest


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
    _insert_synthetic(
        db_conn,
        engram_id="egr_quarantined",
        name="quarantined-top",
        path="quarantined-top.egr.md",
        content_sha256="egr_quarantined",
        verification_status="quarantined",
    )
    _insert_synthetic(
        db_conn,
        engram_id="egr_healthy",
        name="healthy-skill",
        path="healthy-skill.egr.md",
        content_sha256="egr_healthy",
    )
    query = "quarantine eligibility probe unique tokens xyzzy"
    ephemeral_mod.upsert_embedding(
        db_conn,
        engram_id="egr_quarantined",
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed(query),
        source_sha256="egr_quarantined",
    )
    ephemeral_mod.upsert_embedding(
        db_conn,
        engram_id="egr_healthy",
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=embedder.embed("unrelated healthy skill text"),
        source_sha256="egr_healthy",
    )

    outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    _assert_excluded_bodies(outcome, "egr_quarantined", "quarantined-top.egr.md")
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
    _insert_synthetic(
        db_conn,
        engram_id="egr_aa1100ff",
        name="healthy-competitor",
        path="healthy-competitor.egr.md",
        content_sha256="egr_aa1100ff",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id="egr_aa1100ff", model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed("unrelated healthy"), source_sha256="egr_aa1100ff",
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
    _insert_synthetic(
        db_conn,
        engram_id="egr_aa2200ff",
        name="healthy-tool-comp",
        path="healthy-tool-comp.egr.md",
        content_sha256="egr_aa2200ff",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id="egr_aa2200ff", model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed("unrelated"), source_sha256="egr_aa2200ff",
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
    _insert_synthetic(
        db_conn,
        engram_id="egr_aa3300ff",
        name="healthy-compat-comp",
        path="healthy-compat-comp.egr.md",
        content_sha256="egr_aa3300ff",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id="egr_aa3300ff", model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed("unrelated"), source_sha256="egr_aa3300ff",
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
    _insert_synthetic(
        db_conn,
        engram_id="egr_aa4400ff",
        name="healthy-asset-comp",
        path="healthy-asset-comp.egr.md",
        content_sha256="egr_aa4400ff",
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id=eid, model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed(query), source_sha256=digest,
    )
    ephemeral_mod.upsert_embedding(
        db_conn, engram_id="egr_aa4400ff", model_name=embedder.model_name, dim=embedder.dim,
        vec=embedder.embed("unrelated"), source_sha256="egr_aa4400ff",
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
