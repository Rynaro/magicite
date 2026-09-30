"""Direct (non-stdio) unit coverage for ``mcp/bind_retrieval.py``.

S11: RouteDecision/1 projection + C10 body disclosure gate.
"""

from __future__ import annotations

from magicite.core import registry as registry_mod
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
    # Plan/1 not yet wired on router at c656fb8 — defensive null.
    assert out.plan is None or out.plan.status in {"valid", "invalid"}


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
