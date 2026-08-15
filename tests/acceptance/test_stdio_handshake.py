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
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

pytestmark = pytest.mark.acceptance

PROTOCOL_VERSION = "2025-11-25"

#: The revision-string mcp==1.29.0's SUPPORTED_PROTOCOL_VERSIONS could serve.
#: A1-REVISED (framework-verdict.md): mcp>=2.0's HANDSHAKE_PROTOCOL_VERSIONS
#: is exactly this same four-tuple; MODERN_PROTOCOL_VERSIONS ("2026-07-28")
#: is additive and client-selected, never sent by an `initialize` request.
assert HANDSHAKE_PROTOCOL_VERSIONS == (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)

ESCAPE_DRIVER = Path(__file__).parent / "_stdio_escape_driver.py"
ESCAPE_TOOL_NAME = "__stdio_escape_canary__"


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


async def _spawn_escape_driver(project_root: Path) -> asyncio.subprocess.Process:
    """VC-4/VC-5: the same stdio wiring as ``_spawn``, driving
    ``_stdio_escape_driver.py`` instead of ``magicite serve``."""
    env = {**os.environ, "MAGICITE_EMBEDDING_PROVIDER": "hashing"}
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(ESCAPE_DRIVER),
        str(project_root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )


async def _read_line(proc: asyncio.subprocess.Process, timeout: float = 10.0) -> bytes:
    assert proc.stdout is not None
    return await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)


async def _initialize(
    proc: asyncio.subprocess.Process, protocol_version: str = PROTOCOL_VERSION
) -> tuple[dict, bytes]:
    assert proc.stdin is not None
    proc.stdin.write(
        _rpc(
            "initialize",
            {
                "protocolVersion": protocol_version,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_version", HANDSHAKE_PROTOCOL_VERSIONS)
async def test_initialize_across_handshake_versions(project_root: Path, protocol_version: str) -> None:
    """VC-1: every revision mcp==1.29.0 could serve still negotiates cleanly on mcp>=2.0,
    and a following tools/list still returns all 16 tools on that connection.

    VC-2 (first half): the negotiated ``protocolVersion`` is never the modern-era
    ``2026-07-28`` -- it is always the handshake-era value the client offered.
    """
    proc = await _spawn(project_root)
    try:
        resp, _raw = await _initialize(proc, protocol_version=protocol_version)
        assert "error" not in resp, resp
        negotiated = resp["result"]["protocolVersion"]
        assert negotiated in HANDSHAKE_PROTOCOL_VERSIONS
        assert negotiated == protocol_version
        assert negotiated != "2026-07-28"
        assert negotiated not in MODERN_PROTOCOL_VERSIONS

        assert proc.stdin is not None
        proc.stdin.write(_rpc("tools/list", {}, id=2))
        await proc.stdin.drain()
        list_resp = json.loads(await _read_line(proc))
        assert "error" not in list_resp, list_resp
        assert len(list_resp["result"]["tools"]) == 16
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol_version",
    ["2026-07-28", "2099-01-01", "not-a-real-protocol-version"],
)
async def test_no_modern_era_leakage_on_unrecognized_version(
    project_root: Path, protocol_version: str
) -> None:
    """VC-2 (second half): an `initialize` carrying an unrecognised or future
    (including the modern-era `2026-07-28`) `protocolVersion` is NEVER answered
    with a `MODERN_PROTOCOL_VERSIONS` member -- guards the `LATEST_PROTOCOL_VERSION`
    semantic trap (dossier §7.3) at the wire, where it would actually bite a host.
    """
    proc = await _spawn(project_root)
    try:
        resp, _raw = await _initialize(proc, protocol_version=protocol_version)
        assert "error" not in resp, resp
        negotiated = resp["result"]["protocolVersion"]
        assert negotiated not in MODERN_PROTOCOL_VERSIONS
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
async def test_stdout_diversion_survives_native_escapes(project_root: Path) -> None:
    """VC-4: AC-002 re-verified at three escape levels from a REAL tool handler
    (``_stdio_escape_driver.py``, wiring the same ``build_state``/``dispatch_call``/
    ``Server``/``stdio_server`` production pieces as ``magicite.mcp.app.run_stdio``):
    a flushed ``print()``, a raw ``os.write(1, ...)``, and a ``subprocess`` child
    inheriting fd 1 must all land on stderr, never on stdout -- stdout must still
    parse as JSON-RPC frames only.

    VC-5 (mechanical proof): the same tool handler does a non-blocking
    ``os.read(0, ...)`` and gets an immediate EOF (``stdin_read_len == 0``),
    proving fd 0 -> the null device has no blocking side effect.
    """
    proc = await _spawn_escape_driver(project_root)
    lines: list[bytes] = []
    try:
        _resp, raw_init_line = await _initialize(proc)
        lines.append(raw_init_line)

        assert proc.stdin is not None
        proc.stdin.write(_rpc("tools/call", {"name": ESCAPE_TOOL_NAME, "arguments": {}}, id=2))
        await proc.stdin.drain()
        call_line = await _read_line(proc)
        lines.append(call_line)

        stderr_bytes = await asyncio.wait_for(proc.stderr.read(4096), timeout=5.0)  # type: ignore[union-attr]
    finally:
        await _terminate(proc)

    assert len(lines) == 2
    for raw in lines:
        obj = json.loads(raw.decode("utf-8"))  # raises on anything that is not exactly one JSON value
        assert obj.get("jsonrpc") == "2.0"

    call_resp = json.loads(lines[1].decode("utf-8"))
    assert call_resp["result"]["isError"] is False, call_resp
    assert call_resp["result"]["structuredContent"]["stdin_read_len"] == 0

    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    assert "CANARY-print" in stderr_text
    assert "CANARY-raw-fd1-write" in stderr_text
    assert "CANARY-subprocess-child" in stderr_text
