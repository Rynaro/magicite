"""FastMCP instance + lifespan (spec §1, §3.1).

``magicite.mcp`` is the ~300-line shim INV-1 describes: this module wires
the 16-tool ``TOOL_REGISTRY`` (populated by importing every ``bind_*``
module below) onto the MCP stdio transport. FastMCP's own per-function
signature-derived schema/validation pipeline is intentionally bypassed
(see the module docstring in ``registry.py``): ``list_tools``/``call_tool``
are re-registered directly on the low-level ``mcp.server.lowlevel.Server``
FastMCP wraps, so that:

- the wire ``inputSchema``/``outputSchema`` are always exactly
  ``TOOL_REGISTRY``'s strict (``extra="forbid"``) pydantic schemas
  (AC-005), and
- every tool call goes through one place that does idempotency replay,
  structured event logging, and error-code mapping (spec §3.1) --
  independent of whatever the pinned MCP SDK's own tool-call plumbing
  does this month (Risk R6).

``obs.logging.configure()`` is imported and called **first**, before the
MCP SDK, so stdout is never touched by anything but protocol frames
(AC-002) even if a dependency misbehaves during import.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from magicite.obs.logging import configure as _configure_logging

_configure_logging()

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations  # noqa: E402

from magicite.config import Config  # noqa: E402
from magicite.embeddings.hashing_provider import get_embedder  # noqa: E402
from magicite.errors import IdempotencyKeyConflictError, MagiciteError  # noqa: E402
from magicite.mcp import (  # noqa: E402, F401  (imports register tools onto TOOL_REGISTRY)
    bind_dream,
    bind_inspect,
    bind_lifecycle,
    bind_registry,
    bind_retrieval,
    bind_signals,
)
from magicite.mcp.registry import ToolContext, get_tool, manifest  # noqa: E402
from magicite.obs.logging import get_logger  # noqa: E402
from magicite.storage import db as db_mod  # noqa: E402

logger = get_logger("magicite.mcp")

SERVER_NAME = "magicite"


@dataclass
class ServerState:
    cfg: Config
    conn: sqlite3.Connection
    embedder: Any


def build_state(cfg: Config) -> ServerState:
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    embedder = get_embedder(cfg.embedding_dim)
    return ServerState(cfg=cfg, conn=conn, embedder=embedder)


def _tool_annotations(meta: Any) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=meta.read_only,
        destructiveHint=meta.destructive,
        idempotentHint=meta.idempotent,
    )


def _tool_meta_dict(meta: Any) -> dict[str, Any]:
    return {
        "risk_class": meta.risk,
        "side_effect": meta.side_effect,
        "signal_tier": meta.signal_tier,
        "idempotent": meta.idempotent,
    }


def build_mcp_tools() -> list[Tool]:
    """The wire-level ``types.Tool`` list, sourced straight from ``TOOL_REGISTRY``."""
    tools: list[Tool] = []
    for row in manifest():
        meta = get_tool(row["name"])
        assert meta is not None
        tool = Tool(
            name=meta.name,
            description=meta.description,
            inputSchema=meta.input_model.model_json_schema(),
            outputSchema=meta.output_model.model_json_schema(),
            annotations=_tool_annotations(meta),
        )
        # `meta`'s wire alias is `_meta`; setting the attribute post-construction
        # sidesteps a pydantic/mypy constructor-signature mismatch (the field
        # *is* settable as `meta` -- confirmed against the installed mcp==1.29.0
        # -- this is purely a static-typing quirk, not a runtime one).
        tool.meta = _tool_meta_dict(meta)
        tools.append(tool)
    return tools


def _args_sha256(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _error_result(error: MagiciteError) -> CallToolResult:
    envelope = error.to_dict()
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structuredContent=envelope,
        isError=True,
    )


def dispatch_call(state: ServerState, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
    """The single place every tool call passes through (spec §3.1's "the decorator").

    Strict validation -> idempotency replay -> handler call -> idempotency
    store -> structured logging -> error mapping. Framework-adjacent (it
    imports ``mcp.types``) but calls into framework-free ``magicite.core``/
    ``magicite.engram`` handlers only.
    """
    meta = get_tool(tool_name)
    if meta is None:
        return _error_result(MagiciteError(f"unknown tool {tool_name!r}"))

    try:
        params = meta.input_model.model_validate(arguments)
    except Exception as exc:  # pydantic.ValidationError et al.
        return _error_result(
            MagiciteError(
                f"invalid input for {tool_name}: {exc}",
                hint="check for unknown or malformed fields against the tool's input schema",
            )
        )

    request_id = getattr(params, "request_id", None)
    args_hash = _args_sha256(arguments)
    if request_id:
        cached = state.conn.execute(
            "SELECT args_sha256, response_json FROM eph_idempotency WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if cached is not None:
            if cached["args_sha256"] != args_hash:
                return _error_result(
                    IdempotencyKeyConflictError(
                        f"request_id {request_id!r} was already used with different arguments"
                    )
                )
            payload = json.loads(cached["response_json"])
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload))],
                structuredContent=payload,
                isError=False,
            )

    session_id = getattr(params, "session_id", None)
    ctx = ToolContext(cfg=state.cfg, conn=state.conn, embedder=state.embedder, session_id=session_id)

    try:
        result = meta.handler(ctx, params)
    except MagiciteError as exc:
        logger.warning("tool_error", tool=tool_name, code=exc.code.value, message=exc.message)
        return _error_result(exc)
    except Exception as exc:  # pragma: no cover - defensive: never leak a raw traceback
        logger.error("tool_internal_error", tool=tool_name, error=str(exc))
        return _error_result(MagiciteError(f"internal error in {tool_name}: {exc}"))

    payload = result.model_dump(mode="json")

    if request_id:
        now = datetime.now(UTC).isoformat()
        state.conn.execute(
            """
            INSERT INTO eph_idempotency (request_id, tool, args_sha256, response_json, created_at, expires_at)
            VALUES (?,?,?,?,?,?)
            """,
            (request_id, tool_name, args_hash, json.dumps(payload), now, now),
        )

    logger.info("tool_call", tool=tool_name, risk=meta.risk)

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=False,
    )


def build_app(cfg: Config) -> tuple[FastMCP, ServerState]:
    state = build_state(cfg)
    app: FastMCP = FastMCP(name=SERVER_NAME)

    async def list_tools_handler() -> list[Tool]:
        return build_mcp_tools()

    async def call_tool_handler(name: str, arguments: dict[str, Any]) -> CallToolResult:
        return dispatch_call(state, name, arguments)

    # Replace FastMCP's own signature-derived handlers with ours (see module
    # docstring). This is a plain dict assignment inside the low-level
    # Server (mcp/server/lowlevel/server.py); re-registering after FastMCP's
    # constructor has run is the supported way to override a handler.
    app._mcp_server.list_tools()(list_tools_handler)
    app._mcp_server.call_tool(validate_input=False)(call_tool_handler)

    return app, state


async def run_stdio(cfg: Config) -> None:
    app, _state = build_app(cfg)
    await app.run_stdio_async()
