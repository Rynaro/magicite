"""AC-001, AC-002: stdio MCP handshake + stdout purity.

These tests speak raw newline-delimited JSON-RPC (spec: MCP stdio
transport, ``mcp/server/stdio.py``) directly against a subprocess running
``magicite serve``, rather than going through the SDK's client
convenience wrapper -- that is the only way to prove "not a single
stray byte on stdout" (AC-002) instead of relying on a tolerant parser
to silently swallow one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

PROTOCOL_VERSION = "2025-11-25"


def _rpc(method: str, params: dict | None = None, *, id: int | None = None) -> bytes:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return (json.dumps(msg) + "\n").encode("utf-8")


async def _spawn(project_root: Path) -> asyncio.subprocess.Process:
    env = {**os.environ, "MAGICITE_EMBEDDING_PROVIDER": "hashing"}
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "magicite",
        "serve",
        "--project-root",
        str(project_root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )


async def _read_line(proc: asyncio.subprocess.Process, timeout: float = 10.0) -> bytes:
    assert proc.stdout is not None
    return await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)


async def _initialize(proc: asyncio.subprocess.Process) -> tuple[dict, bytes]:
    assert proc.stdin is not None
    proc.stdin.write(
        _rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "vivi-acceptance-test", "version": "0.0.1"},
            },
            id=1,
        )
    )
    await proc.stdin.drain()
    raw_line = await _read_line(proc)
    resp = json.loads(raw_line)
    proc.stdin.write(_rpc("notifications/initialized"))
    await proc.stdin.drain()
    return resp, raw_line


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None:
        proc.stdin.close()
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_initialize(project_root: Path) -> None:
    """AC-001: the server completes the initialize handshake and reports serverInfo.name == 'magicite'."""
    proc = await _spawn(project_root)
    try:
        resp, _raw = await _initialize(proc)
        assert "error" not in resp, resp
        assert resp["result"]["serverInfo"]["name"] == "magicite"
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
async def test_stdout_is_protocol_only(project_root: Path) -> None:
    """AC-002: every line written to stdout is a valid MCP JSON-RPC frame, never anything else."""
    proc = await _spawn(project_root)
    lines: list[bytes] = []
    try:
        _resp, raw_init_line = await _initialize(proc)
        lines.append(raw_init_line)

        assert proc.stdin is not None
        proc.stdin.write(_rpc("tools/list", {}, id=2))
        await proc.stdin.drain()
        lines.append(await _read_line(proc))

        proc.stdin.write(
            _rpc(
                "tools/call",
                {"name": "route", "arguments": {"query": "rollback proton for a steam game"}},
                id=3,
            )
        )
        await proc.stdin.drain()
        lines.append(await _read_line(proc))

        # A call that trips AC-005 (unknown field) is still an MCP-level success
        # frame carrying an isError=True CallToolResult -- also protocol-only.
        proc.stdin.write(
            _rpc("tools/call", {"name": "route", "arguments": {"query": "x", "bogus": 1}}, id=4)
        )
        await proc.stdin.drain()
        lines.append(await _read_line(proc))
    finally:
        await _terminate(proc)

    assert len(lines) == 4
    for raw in lines:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        obj = json.loads(text)  # raises ValueError on anything that is not exactly one JSON value
        assert obj.get("jsonrpc") == "2.0"
        assert isinstance(obj.get("id"), int) or "id" not in obj
