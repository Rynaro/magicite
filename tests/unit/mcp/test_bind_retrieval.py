"""Direct (non-stdio) unit coverage for ``mcp/bind_retrieval.py``: proves
the adapter layer -> ``core.router``/``core.composition`` wiring survived
M2's algorithm rewrite unchanged (the "engine is a library; MCP is an
adapter" commitment, spec Approach commitment 1) without needing a real
stdio round-trip (that belt-and-suspenders coverage already exists in
tests/acceptance/test_walking_skeleton.py)."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.mcp import bind_retrieval
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import LoadSkillBodyInput, RouteContext, RouteInput


def test_route_tool_returns_composition_plan_via_adapter(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_retrieval.route(
        ctx, RouteInput(query="rollback proton for a steam game", k=5)
    )

    assert out.candidates[0].name == "proton-ge-proton-downgrade"
    assert "steam-prefix-access" in out.composition_plan
    assert out.registry_size == 7


def test_route_tool_hard_excludes_via_context(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
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


def test_load_skill_body_l2_via_adapter(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_retrieval.load_skill_body(
        ctx, LoadSkillBodyInput(name="proton-ge-proton-downgrade", level="L2")
    )
    assert out.procedure
    assert out.examples is None  # L2 excludes Examples/Provenance
