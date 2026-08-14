"""``signal_use``/``signal_outcome``/``session_end`` (spec §3.3 tools 5-7).

M0: none of these are implemented yet -- the Tier 0/1/2 signal ladder,
``core/signals.py`` and ``core/session.py`` land in M3. Registered here
with their final, frozen schemas (AC-003/AC-004); every body raises a
typed ``not_implemented`` rather than a stub success (INV-4).
"""

from __future__ import annotations

from magicite.errors import NotImplementedToolError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    SessionEndInput,
    SessionEndOutput,
    SignalOutcomeInput,
    SignalOutcomeOutput,
    SignalUseInput,
    SignalUseOutput,
)

_NOT_YET = "the Tier 0/1/2 signal ladder lands in M3 (core/signals.py, core/session.py)"


@magicite_tool(
    risk="R1",
    side_effect="ephemeral",
    signal_tier="1|2",
    idempotent=True,
    input_model=SignalUseInput,
    output_model=SignalUseOutput,
    description="Mark skill application; sets ephemeral tags and candidate co-activation edges.",
)
def signal_use(ctx: ToolContext, params: SignalUseInput) -> SignalUseOutput:
    raise NotImplementedToolError(_NOT_YET)


@magicite_tool(
    risk="R1",
    side_effect="ephemeral",
    signal_tier="1|2",
    idempotent=True,
    input_model=SignalOutcomeInput,
    output_model=SignalOutcomeOutput,
    description="Verified outcome signal; captures tags into pending Dream-consolidation weight changes.",
)
def signal_outcome(ctx: ToolContext, params: SignalOutcomeInput) -> SignalOutcomeOutput:
    raise NotImplementedToolError(_NOT_YET)


@magicite_tool(
    risk="R1",
    side_effect="ephemeral+enqueue",
    signal_tier=1,
    idempotent=True,
    input_model=SessionEndInput,
    output_model=SessionEndOutput,
    description="Close a session, expire its tags, and (maybe) enqueue Dream.",
)
def session_end(ctx: ToolContext, params: SessionEndInput) -> SessionEndOutput:
    raise NotImplementedToolError(_NOT_YET)
