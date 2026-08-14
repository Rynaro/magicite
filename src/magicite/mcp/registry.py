"""``@magicite_tool`` decorator + ``TOOL_REGISTRY`` + manifest (spec §3.1).

This module is the single source of truth for tool metadata (risk class,
side effect, signal tier, idempotency) — MCP ``annotations``/``_meta`` are
*projections* of ``TOOL_REGISTRY``, never the other way around (Risk R6:
"FastMCP metadata API drift"). ``magicite tools`` and the
``magicite://tools`` resource both read this module directly, so the
authoritative manifest survives whatever the pinned MCP SDK's tool
metadata API looks like this month.

Cross-cutting concerns spec §3.1 assigns to "the decorator" (strict input
validation, idempotency replay, structured logging, error mapping)
require a live DB connection/session context that does not exist at
import time, when ``@magicite_tool`` runs. They are implemented in
``mcp/app.py``'s ``call_tool`` dispatcher instead, which is the runtime
counterpart of this module: ``registry.py`` is the static metadata table,
``app.py`` is the thing that actually calls a tool. This is an
implementation-detail split, not a departure from spec intent — nothing
here changes the 16-tool surface, the metadata fields, or the strictness
guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

RiskClass = str  # "R0".."R3" (R4/R5 are forbidden in v1, spec §Scope)
#: none | ephemeral | filesystem | durable | batch | ephemeral+enqueue | proposal | proposal->durable
SideEffect = str


@dataclass
class ToolContext:
    """Runtime handle passed to every tool handler by ``mcp/app.py``.

    Deliberately a thin bag of framework-free objects (``Config``, a
    ``sqlite3.Connection``, an ``Embedder``) -- handlers in
    ``mcp/bind_*.py`` call straight into ``magicite.core``/``magicite.engram``
    with these, never touching the MCP SDK.
    """

    cfg: Any
    conn: Any
    embedder: Any
    session_id: str | None = None


#: Concrete handlers narrow ``params``/return to a specific pydantic model per
#: tool (e.g. ``(ToolContext, RouteInput) -> RouteOutput``) -- deliberately
#: heterogeneous across the 16 tools, so this alias stays loose (``Any``)
#: rather than fighting Callable's parameter contravariance for no static-typing
#: benefit; each ``bind_*.py`` function is still fully typed at its own definition.
ToolHandler = Callable[[ToolContext, Any], Any]


@dataclass(frozen=True)
class ToolMeta:
    name: str
    risk: RiskClass
    side_effect: SideEffect
    idempotent: bool
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    signal_tier: int | str | None = None
    read_only: bool = False
    destructive: bool = False
    description: str = ""

    def manifest_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "risk_class": self.risk,
            "side_effect": self.side_effect,
            "signal_tier": self.signal_tier,
            "idempotent": self.idempotent,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }


TOOL_REGISTRY: dict[str, ToolMeta] = {}

#: Registration order == spec §3.2 table order (1..16); preserved so
#: ``magicite tools`` and ``tools/list`` are stable and diffable.
_REGISTRATION_ORDER: list[str] = []


def magicite_tool(
    *,
    risk: RiskClass,
    side_effect: SideEffect,
    idempotent: bool,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    signal_tier: int | str | None = None,
    read_only: bool = False,
    destructive: bool = False,
    description: str = "",
) -> Callable[[ToolHandler], ToolHandler]:
    """Register ``fn`` as a Magicite tool handler.

    ``fn`` has the shape ``(ctx: ToolContext, params: <input_model>) ->
    <output_model>``. Registration is pure bookkeeping: the returned
    callable is unchanged, so unit tests can call it directly without any
    MCP machinery in the loop.
    """

    def decorator(fn: ToolHandler) -> ToolHandler:
        name = fn.__name__
        if name in TOOL_REGISTRY:
            raise ValueError(f"tool {name!r} already registered")
        TOOL_REGISTRY[name] = ToolMeta(
            name=name,
            risk=risk,
            side_effect=side_effect,
            idempotent=idempotent,
            input_model=input_model,
            output_model=output_model,
            handler=fn,
            signal_tier=signal_tier,
            read_only=read_only,
            destructive=destructive,
            description=description or (fn.__doc__ or "").strip().split("\n")[0],
        )
        _REGISTRATION_ORDER.append(name)
        return fn

    return decorator


def manifest() -> list[dict[str, Any]]:
    """The authoritative tool manifest, in registration order (AC-003, AC-004)."""
    return [TOOL_REGISTRY[name].manifest_row() for name in _REGISTRATION_ORDER]


def get_tool(name: str) -> ToolMeta | None:
    return TOOL_REGISTRY.get(name)


def registered_names() -> list[str]:
    return list(_REGISTRATION_ORDER)
