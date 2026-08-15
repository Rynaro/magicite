"""``consolidate``/``nucleate`` (spec §3.3 tools 12-13).

M0/M1: the Dream worker orchestrator (``core/dream.py``, its seven
phases) lands in M4; distillation (``core/distill.py``) lands in M6.
``storage/lease.py``'s in-process ``WriterLease`` already exists as of
M1 (spec §6.2 G2) -- M4 upgrades it to the full flock+DB-row lease with
TTL/heartbeat, it does not create it from scratch. Registered here with
their final, frozen schemas; every body raises a typed ``not_implemented``.
"""

from __future__ import annotations

from magicite.errors import NotImplementedToolError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import ConsolidateInput, ConsolidateOutput, NucleateInput, NucleateOutput


@magicite_tool(
    risk="R3",
    side_effect="batch",
    idempotent=True,
    input_model=ConsolidateInput,
    output_model=ConsolidateOutput,
    description="Trigger the offline Dream consolidation worker (enqueue-only, idempotent).",
)
def consolidate(ctx: ToolContext, params: ConsolidateInput) -> ConsolidateOutput:
    raise NotImplementedToolError(
        "the Dream worker orchestrator lands in M4 (core/dream.py)",
    )


@magicite_tool(
    risk="R3",
    side_effect="proposal",
    idempotent=True,
    input_model=NucleateInput,
    output_model=NucleateOutput,
    description=(
        "Manual induction trigger; emits nucleation proposals "
        "(CR-3: the server never generates prose)."
    ),
)
def nucleate(ctx: ToolContext, params: NucleateInput) -> NucleateOutput:
    raise NotImplementedToolError(
        "frequent-path distillation lands in M6 (core/distill.py)",
    )
