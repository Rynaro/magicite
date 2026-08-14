"""AC-003: ``tools/list`` returns exactly the 16 tool names from spec §3.2."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance

EXPECTED_TOOL_NAMES = {
    "route",
    "load_skill_body",
    "introspect",
    "flag_dead",
    "signal_use",
    "signal_outcome",
    "session_end",
    "register",
    "sync",
    "checkpoint",
    "export",
    "consolidate",
    "nucleate",
    "sharpen",
    "promote",
    "archive",
}


def test_sixteen_tools() -> None:
    from magicite.mcp import (
        app as _app,  # noqa: F401  (import side effect: populates TOOL_REGISTRY)
    )
    from magicite.mcp.registry import registered_names

    names = registered_names()
    assert len(names) == 16
    assert set(names) == EXPECTED_TOOL_NAMES


@pytest.mark.asyncio
async def test_tools_list_over_stdio(project_root) -> None:
    """The same 16 names, proven over the real MCP wire (belt-and-suspenders on AC-003)."""
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
            result = await session.list_tools()
            names = {t.name for t in result.tools}
            assert len(result.tools) == 16
            assert names == EXPECTED_TOOL_NAMES
