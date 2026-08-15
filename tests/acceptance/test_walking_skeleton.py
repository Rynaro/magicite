"""AC-006: register() -> route() -> introspect() end-to-end against the toy registry."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance


def test_register_route_introspect(cfg, db_conn, embedder) -> None:
    from magicite.core import registry as registry_mod
    from magicite.core import router as router_mod
    from magicite.storage import queries as queries_mod

    register_outcome = registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    assert register_outcome.ingested == 7
    assert register_outcome.validation_errors == []

    route_outcome = router_mod.route(
        cfg, db_conn, embedder, query="rollback proton for a steam game", k=5
    )
    assert route_outcome.candidates, "expected at least one routable candidate"
    assert route_outcome.candidates[0].name == "proton-ge-proton-downgrade"

    summary = queries_mod.registry_summary(
        db_conn, embedding_model=embedder.model_name, autonomous=cfg.autonomous
    )
    assert summary["registry_size"] == 7

    detail = queries_mod.skill_detail(db_conn, "proton-ge-proton-downgrade")
    assert detail is not None
    assert detail["skill"]["name"] == "proton-ge-proton-downgrade"
    assert any(e["target"] == "steam-prefix-access" for e in detail["outbound_edges"])


@pytest.mark.asyncio
async def test_register_route_introspect_over_mcp(project_root) -> None:
    """The same story proven over the real MCP wire (the AC's literal GIVEN/WHEN)."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "magicite", "serve", "--project-root", str(project_root)],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            reg = await session.call_tool("register", {"path": ".spectra/engrams"})
            assert reg.is_error is False, reg.structured_content
            assert reg.structured_content["ingested"] == 7

            rt = await session.call_tool(
                "route", {"query": "rollback proton for a steam game", "k": 5}
            )
            assert rt.is_error is False, rt.structured_content
            assert rt.structured_content["candidates"][0]["name"] == "proton-ge-proton-downgrade"

            intro = await session.call_tool(
                "introspect", {"skill_id": "proton-ge-proton-downgrade"}
            )
            assert intro.is_error is False, intro.structured_content
            assert intro.structured_content["skill"]["name"] == "proton-ge-proton-downgrade"
