"""``consolidate``/``nucleate`` (spec §3.3 tools 12-13).

M0: the Dream worker (``core/dream.py``, ``storage/lease.py``) lands in
M4; distillation (``core/distill.py``) lands in M6. Registered here with
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
        "the Dream worker lands in M4 (storage/lease.py, core/dream.py)",
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
