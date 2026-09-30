"""Direct (non-stdio) unit coverage for ``mcp/bind_retrieval.py``.

S11: RouteDecision/1 + Plan/1 projection + C10 body disclosure gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from magicite.core import eligibility as eligibility_mod
from magicite.core import registry as registry_mod
from magicite.core import router as router_mod
from magicite.core import routing_policy as policy_mod
from magicite.mcp import bind_retrieval
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import LoadSkillBodyInput, RouteContext, RouteInput


def _digest_for(conn, name: str) -> str:
    row = conn.execute(
        "SELECT id, content_sha256 FROM engram WHERE name = ?", (name,)
    ).fetchone()
    assert row is not None
    return str(row["content_sha256"])


def test_route_tool_returns_composition_plan_via_adapter(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_retrieval.route(ctx, RouteInput(query="rollback proton for a steam game", k=5))

    assert out.candidates[0].name == "proton-ge-proton-downgrade"
    assert "steam-prefix-access" in out.composition_plan
    assert out.registry_size == 7
    assert out.policy_id
    assert out.policy_digest
    assert out.decision_id
    assert out.status in {"selected", "abstained", "error"}
    assert out.selected_content_digests
    assert out.plan is not None
    assert out.plan.status == "valid"
    assert out.host_verification_report is None  # not produced on route path


def test_public_plan_digest_matches_decision(cfg, db_conn, embedder) -> None:
    """FIX 1: public plan_digest is exactly decision.plan_digest."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    outcome = router_mod.route(
        cfg, db_conn, embedder, query="rollback proton for a steam game", k=5
    )
    assert outcome.decision is not None
    assert outcome.decision.plan_digest is not None
    assert outcome.plan is not None

    out = bind_retrieval.project_route_output(outcome)
    assert out.plan_digest == outcome.decision.plan_digest
    assert out.plan is not None
    assert out.plan.plan_digest == outcome.decision.plan_digest
    assert router_mod._plan_identity_digest(outcome.plan) == outcome.decision.plan_digest


def test_valid_multi_node_plan_projected_in_topo_order(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    out = bind_retrieval.route(ctx, RouteInput(query="rollback proton for a steam game", k=5))

    assert out.status == "selected"
    assert out.plan is not None
    assert out.plan.status == "valid"
    assert out.plan.executable is True
    assert len(out.plan.nodes) >= 2
    assert out.plan.topological_order == [n.engram_id for n in out.plan.nodes]
    assert all(n.content_digest for n in out.plan.nodes)
    assert out.composition_plan.index("steam-prefix-access") < out.composition_plan.index(
        "proton-ge-proton-downgrade"
    )


def test_composition_invalid_abstain_projects_no_nodes(cfg, db_conn, embedder) -> None:
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
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    out = bind_retrieval.route(ctx, RouteInput(query="rollback proton for a steam game", k=5))

    assert out.status == "abstained"
    assert router_mod.REASON_COMPOSITION_INVALID in out.reason_codes
    assert eligibility_mod.REASON_DANGLING_DEPENDENCY in out.reason_codes
    assert out.candidates == []
    assert out.composition_plan == []
    # No plan nodes/bodies of ineligible deps on the public surface.
    if out.plan is not None:
        assert out.plan.nodes == []
        assert out.plan.edges == []
        assert out.plan.topological_order == []


def test_route_tool_hard_excludes_via_context(cfg, db_conn, embedder) -> None:
    cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_retrieval.route(
        ctx,
        RouteInput(
            query="rollback proton for a steam game",
            k=5,
            context=RouteContext(user_prefs=["-proton-ge-proton-downgrade"]),
        ),
    )
    names = {c.name for c in out.candidates}
    assert "proton-ge-proton-downgrade" not in names
    # Exclusions never carry bodies or absolute paths.
    for ex in out.exclusions:
        assert "/" not in ex.engram_id or not ex.engram_id.startswith("/")
        blob = " ".join(ex.reason_codes)
        assert "procedure" not in blob.lower()


def test_load_skill_body_l2_via_adapter(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    digest = _digest_for(db_conn, "proton-ge-proton-downgrade")

    out = bind_retrieval.load_skill_body(
        ctx,
        LoadSkillBodyInput(
            name="proton-ge-proton-downgrade",
            level="L2",
            expected_content_digest=digest,
        ),
    )
    assert out.status == "ok"
    assert out.procedure
    assert out.examples is None  # L2 excludes Examples/Provenance


def test_load_skill_body_cursor_round_trip(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    digest = _digest_for(db_conn, "proton-ge-proton-downgrade")
    full = bind_retrieval.load_skill_body(
        ctx,
        LoadSkillBodyInput(
            name="proton-ge-proton-downgrade",
            level="L2",
            max_bytes=100000,
            expected_content_digest=digest,
        ),
    )
    expected = full.procedure + full.pitfalls
    chunks: list[str] = []
    cursor = 0
    while True:
        page = bind_retrieval.load_skill_body(
            ctx,
            LoadSkillBodyInput(
                name="proton-ge-proton-downgrade",
                level="L2",
                max_bytes=23,
                cursor=cursor,
                expected_content_digest=digest,
            ),
        )
        chunks.append(page.procedure + page.pitfalls)
        if page.next_offset is None:
            break
        assert page.next_offset > cursor
        cursor = page.next_offset
    assert "".join(chunks) == expected


def test_load_skill_body_missing_digest_is_missing_context(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    out = bind_retrieval.load_skill_body(
        ctx, LoadSkillBodyInput(name="proton-ge-proton-downgrade", level="L2")
    )
    assert out.status == "missing_context"
    assert out.procedure == ""
    assert "expected_content_digest" in out.missing_context
