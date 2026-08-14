"""``sharpen``/``promote``/``archive`` (spec §3.3 tools 14-16).

M0: the lifecycle FSM and approval machinery (``core/lifecycle.py``,
``core/approvals.py``) land in M5. Registered here with their final,
frozen schemas; every body raises a typed ``not_implemented``.
"""

from __future__ import annotations

from magicite.errors import NotImplementedToolError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    ArchiveInput,
    ArchiveOutput,
    PromoteInput,
    PromoteOutput,
    SharpenInput,
    SharpenOutput,
)

_NOT_YET = "the lifecycle FSM and approval machinery land in M5 (core/lifecycle.py, core/approvals.py)"


@magicite_tool(
    risk="R3",
    side_effect="proposal->durable",
    idempotent=True,
    input_model=SharpenInput,
    output_model=SharpenOutput,
    description="Manual sharpening pass; approval-gated re-write of an engram.",
)
def sharpen(ctx: ToolContext, params: SharpenInput) -> SharpenOutput:
    raise NotImplementedToolError(_NOT_YET)


@magicite_tool(
    risk="R3",
    side_effect="proposal->durable",
    idempotent=True,
    input_model=PromoteInput,
    output_model=PromoteOutput,
    description="Lifecycle transition upward; evidence-gated per core/fitness.py.",
)
def promote(ctx: ToolContext, params: PromoteInput) -> PromoteOutput:
    raise NotImplementedToolError(_NOT_YET)


@magicite_tool(
    risk="R3",
    side_effect="proposal->durable",
    idempotent=True,
    input_model=ArchiveInput,
    output_model=ArchiveOutput,
    description="Lifecycle transition to archived; never deletes, moves the file to .spectra/archive/.",
)
def archive(ctx: ToolContext, params: ArchiveInput) -> ArchiveOutput:
    raise NotImplementedToolError(_NOT_YET)
