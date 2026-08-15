"""VC-4 driver: AC-002 fd-diversion re-verified from inside a REAL tool handler.

Not a pytest module (no ``test_``/``_test`` naming -- excluded from
collection); spawned as a subprocess by
``test_stdio_handshake.py::test_stdout_is_protocol_only``.

Wires the exact same production pieces ``magicite.mcp.app.run_stdio`` uses --
``build_state``, ``dispatch_call``, ``SERVER_NAME``, ``build_mcp_tools``,
``mcp.server.lowlevel.Server``, ``mcp.server.stdio.stdio_server`` -- so the
stdio serving loop under test is Magicite's real one. The only addition is
one extra tool name, ``ESCAPE_TOOL_NAME``, whose handler performs the three
AC-002 escape levels (Python ``print``, a raw ``os.write(1, ...)``, and a
``subprocess`` child inheriting fd 1) from *inside* a live ``on_call_tool``
invocation -- every other tool name still goes through the real
``dispatch_call`` chokepoint, untouched. It also does a non-blocking
``os.read(0, ...)`` for VC-5 (fd 0 diverted to the null device -> immediate
EOF, never a block on a handler that mistakenly reads stdin).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent

from magicite.config import Config
from magicite.mcp.app import SERVER_NAME, build_mcp_tools, build_state, dispatch_call

ESCAPE_TOOL_NAME = "__stdio_escape_canary__"


async def _run(project_root: str) -> None:
    cfg = Config.load(project_root)
    state = build_state(cfg)

    async def list_tools_handler(ctx: Any, params: Any) -> ListToolsResult:
        return ListToolsResult(tools=build_mcp_tools())

    async def call_tool_handler(ctx: Any, params: Any) -> CallToolResult:
        if params.name == ESCAPE_TOOL_NAME:
            print("CANARY-print", flush=True)
            os.write(1, b"CANARY-raw-fd1-write\n")
            subprocess.run(["/bin/echo", "CANARY-subprocess-child"], check=True)
            # VC-5: fd 0 is diverted to the null device while serving -- this
            # must return immediately (EOF, len 0), never block.
            stdin_read_len = len(os.read(0, 4096))
            return CallToolResult(
                content=[TextContent(type="text", text="ok")],
                structured_content={"ok": True, "stdin_read_len": stdin_read_len},
                is_error=False,
            )
        return dispatch_call(state, params.name, params.arguments or {})

    server = Server(SERVER_NAME, on_list_tools=list_tools_handler, on_call_tool=call_tool_handler)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_run(sys.argv[1]))
