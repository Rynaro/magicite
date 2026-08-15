"""Low-level ``mcp.server.lowlevel.Server`` instance + lifespan (spec §1, §3.1; A1-REVISED).

``magicite.mcp`` is the ~300-line shim INV-1 describes: this module wires
the 16-tool ``TOOL_REGISTRY`` (populated by importing every ``bind_*``
module below) onto the MCP stdio transport. The high-level server's own
per-function signature-derived schema/validation pipeline is intentionally
bypassed (see the module docstring in ``registry.py``): ``list_tools``/
``call_tool`` are registered directly as ``on_list_tools=``/``on_call_tool=``
constructor kwargs on ``mcp.server.lowlevel.Server`` -- a public API, not a
reach-through into a private attribute of a high-level wrapper -- so that:

- the wire ``inputSchema``/``outputSchema`` are always exactly
  ``TOOL_REGISTRY``'s strict (``extra="forbid"``) pydantic schemas
  (AC-005), and
- every tool call goes through one place that does idempotency replay,
  structured event logging, and error-code mapping (spec §3.1) --
  independent of whatever the pinned MCP SDK's own tool-call plumbing
  does this month (Risk R6).

``obs.logging.configure()`` is imported and called **first**, before the
MCP SDK, so stdout is never touched by anything but protocol frames
(AC-002) even if a dependency misbehaves during import. On ``mcp>=2.0``
``stdio_server()`` additionally diverts fd 1 to stderr for the whole
serving window (framework-enforced, not just discipline) -- see
``run_stdio`` below.

**G1, wired end-to-end (spec §6.2, AC-013):** :func:`build_state` opens the
*two* connections spec §2.1 names -- an authorizer-restricted
``ephemeral_conn`` (``storage/authorizer.py::ephemeral_connection``) and an
unrestricted ``writer_conn`` (``...::writer_connection``), both onto the
same on-disk WAL database. :func:`dispatch_call` is what actually realizes
"writers are a place, not a rule": it looks up which physical connection a
given tool name gets (:data:`_WRITER_CONNECTION_TOOLS`, exactly the 9
R2/R3 tools spec §6.2 does *not* list as hot-path) and builds that call's
``ToolContext`` around it -- so every ``bind_*.py`` handler keeps using
``ctx.conn`` unchanged; only the dispatcher's *choice* of which connection
object that name resolves to changed. This is also the Tier-0
passive-inference capture point (spec §3.3, ``obs/events.py``): every
successful, non-replayed tool call appends a generic ``eph_event`` row.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from magicite.obs.logging import configure as _configure_logging

_configure_logging()

from mcp.server.lowlevel.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import (  # noqa: E402
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
    ToolAnnotations,
)

from magicite.config import Config  # noqa: E402
from magicite.core import dream as dream_mod  # noqa: E402
from magicite.embeddings import get_embedder  # noqa: E402
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
from magicite.obs import events as events_mod  # noqa: E402
from magicite.obs.logging import get_logger  # noqa: E402
from magicite.storage import authorizer as authorizer_mod  # noqa: E402

logger = get_logger("magicite.mcp")

SERVER_NAME = "magicite"

#: spec §6.2 G1, enumerated: exactly the 9 R2/R3 tools that mutate durable
#: (non-``eph_``) state get the unrestricted writer connection. The other 7
#: (``route``, ``load_skill_body``, ``signal_use``, ``signal_outcome``,
#: ``session_end``, ``introspect``, ``flag_dead`` -- spec's own list) get
#: only the authorizer-restricted one. This is the dispatcher-level
#: enforcement of "the hot path physically cannot write durable state"
#: (spec Approach commitment 2): no ``bind_*.py`` handler chooses its own
#: connection, it only ever sees ``ctx.conn``.
_WRITER_CONNECTION_TOOLS: frozenset[str] = frozenset(
    {
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
)


@dataclass
class ServerState:
    cfg: Config
    conn: sqlite3.Connection  # authorizer-restricted (G1); the hot path's only connection
    writer_conn: sqlite3.Connection  # unrestricted; G2 (the lease assertion) governs this one
    embedder: Any


def build_state(cfg: Config) -> ServerState:
    cfg.ensure_dirs()
    # The writer connection is opened first: it is the one that runs
    # migrations (a durable-schema, i.e. writer, concern), so the ephemeral
    # connection can skip re-running them (storage/authorizer.py's
    # ``ephemeral_connection`` defaults ``migrate=False``).
    writer_conn = authorizer_mod.writer_connection(cfg.db_path)
    conn = authorizer_mod.ephemeral_connection(cfg.db_path)
    # get_embedder() only *constructs* the provider here; FastEmbedProvider
    # defers the actual fastembed.TextEmbedding construction (and its
    # cache-check/download) to the first embed() call, not this line, so
    # server boot never itself makes a network call (spec R4) even though
    # the resolved default provider is now "fastembed", not "hashing" --
    # see embeddings/fastembed_provider.py's module docstring for why that
    # guarantee is self-enforced rather than delegated to fastembed's own
    # (insufficient) lazy_load kwarg.
    embedder = get_embedder(cfg)
    return ServerState(cfg=cfg, conn=conn, writer_conn=writer_conn, embedder=embedder)


def _tool_annotations(meta: Any) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=meta.read_only,
        destructive_hint=meta.destructive,
        idempotent_hint=meta.idempotent,
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
            input_schema=meta.input_model.model_json_schema(),
            output_schema=meta.output_model.model_json_schema(),
            annotations=_tool_annotations(meta),
            _meta=_tool_meta_dict(meta),
        )
        tools.append(tool)
    return tools


def _args_sha256(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _error_result(error: MagiciteError) -> CallToolResult:
    envelope = error.to_dict()
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structured_content=envelope,
        is_error=True,
    )


def _apply_session_end_enqueue(state: ServerState, result: Any) -> Any:
    """M4 carry-forward: ``core.session.session_end()`` -- one of G1's seven
    hot-path-only tools (spec §6.2; it never opens a writer connection,
    unchanged by M4) -- can only honestly *preview* whether Dream should be
    enqueued; ``consolidation_run`` is not ``eph_``-prefixed, so G1 denies a
    direct write there from the ephemeral connection it holds, exactly as
    it denies one against ``engram``/``edge``. The dispatcher already owns
    *both* connections (spec's own G1 wiring point, ``build_state``), so
    the real enqueue write happens here -- through ``core.dream.enqueue()``
    on the writer connection, itself just a single-row read + conditional
    insert into an *operational* table (no G2/G3 lease needed, spec §2.4)
    -- rather than by reaching around G1 to let the hot-path connection
    write it directly.

    AC-032 (debounce): ``core.dream.enqueue(..., apply_min_interval=True)``
    is the same dedup+throttle path ``consolidate()`` uses (minus its own
    min-interval exemption), so two ``session_end`` calls inside
    ``dream.min_interval_s`` return the same ``dream_run_id``.
    """
    if not state.cfg.dream_on_session_end:
        return result
    outcome = dream_mod.enqueue(state.writer_conn, state.cfg, trigger="session_end", apply_min_interval=True)
    return result.model_copy(update={"dream_run_id": outcome.consolidation_id, "enqueued": outcome.enqueued})


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
        # M5 security fix #2: keyed on (tool, request_id) -- NOT request_id
        # alone. Before this fix, `checkpoint` and `consolidate` (both
        # effectively argument-less beyond request_id, so their
        # `args_sha256` values could coincide) sharing a caller-chosen
        # request_id meant whichever tool ran first "owned" that id: the
        # second tool's call was treated as a replay of the FIRST tool's
        # response, silently no-op'ing the second call's real side effect
        # while still reporting success -- and breaking AC-019/AC-005's
        # "identical arguments" replay contract, since the two calls were
        # never actually the same request.
        cached = state.conn.execute(
            "SELECT args_sha256, response_json FROM eph_idempotency WHERE tool = ? AND request_id = ?",
            (tool_name, request_id),
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
                structured_content=payload,
                is_error=False,
            )

    session_id = getattr(params, "session_id", None)
    # spec §6.2 G1 / AC-013: durable-write tools (§3.2 R2/R3) get the
    # unrestricted writer connection; every other tool gets only the
    # authorizer-restricted one. Handlers never choose this themselves --
    # they only ever see ``ctx.conn``.
    tool_conn = state.writer_conn if tool_name in _WRITER_CONNECTION_TOOLS else state.conn
    ctx = ToolContext(cfg=state.cfg, conn=tool_conn, embedder=state.embedder, session_id=session_id)

    try:
        result = meta.handler(ctx, params)
    except MagiciteError as exc:
        logger.warning("tool_error", tool=tool_name, code=exc.code.value, message=exc.message)
        return _error_result(exc)
    except Exception as exc:  # pragma: no cover - defensive: never leak a raw traceback
        logger.error("tool_internal_error", tool=tool_name, error=str(exc))
        return _error_result(MagiciteError(f"internal error in {tool_name}: {exc}"))

    if tool_name == "session_end":
        result = _apply_session_end_enqueue(state, result)

    payload = result.model_dump(mode="json")

    # Tier-0 passive-inference capture (spec §3.3, obs/events.py): every
    # successful, non-replayed call is itself server-observable evidence --
    # written on the ephemeral connection (eph_event is eph_-prefixed, so
    # either connection could write it; using the same one always keeps the
    # ledger's writer path singular regardless of which tool ran).
    #
    # M4 fix: use the tool's own *resolved* session_id when its output
    # schema exposes one (currently `route`, whose caller-omitted
    # session_id is server-minted, spec §3.3's session resolution rule) --
    # the raw `session_id` local above is the caller's *input*, which is
    # `None` on exactly the calls where minting happened, so the Tier-0
    # ledger row was silently orphaned from the session that produced it.
    # `signal_use`/`signal_outcome` do not echo a resolved session_id in
    # their output schema (a tool-surface gap outside this milestone's
    # mandate to change, spec §3.2's frozen 16-tool surface); they fall
    # back to the same raw value as before.
    resolved_session_id = getattr(result, "session_id", None) or session_id
    events_mod.record_tool_call(
        state.conn, session_id=resolved_session_id, tool=tool_name, arguments=arguments
    )

    if request_id:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        # M5 security fix #2: a real TTL (previously `expires_at` was set
        # equal to `created_at` -- already-expired the instant it was
        # written -- and nothing ever purged rows anyway, so replay rows
        # accumulated forever). `core/decay.py::purge_retention` (Dream
        # phase 3) now actually deletes rows past `expires_at`.
        expires_at = (now_dt + timedelta(seconds=state.cfg.idempotency_ttl_s)).isoformat()
        state.conn.execute(
            """
            INSERT INTO eph_idempotency (tool, request_id, args_sha256, response_json, created_at, expires_at)
            VALUES (?,?,?,?,?,?)
            """,
            (tool_name, request_id, args_hash, json.dumps(payload), now, expires_at),
        )

    logger.info("tool_call", tool=tool_name, risk=meta.risk)

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=False,
    )


def build_app(cfg: Config) -> tuple[Server, ServerState]:
    state = build_state(cfg)

    async def list_tools_handler(ctx: Any, params: Any) -> ListToolsResult:
        return ListToolsResult(tools=build_mcp_tools())

    async def call_tool_handler(ctx: Any, params: Any) -> CallToolResult:
        return dispatch_call(state, params.name, params.arguments or {})

    # `on_list_tools=`/`on_call_tool=` are public constructor kwargs on the
    # low-level Server (see module docstring) -- no private-attribute
    # reach-through and no framework-derived schema/validation pipeline to
    # opt out of.
    server = Server(SERVER_NAME, on_list_tools=list_tools_handler, on_call_tool=call_tool_handler)

    return server, state


async def run_stdio(cfg: Config) -> None:
    server, _state = build_app(cfg)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
