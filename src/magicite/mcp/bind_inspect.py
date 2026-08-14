"""``introspect`` + ``flag_dead`` (spec §3.3 tools 3-4). Both R0, read-only.

M0: ``introspect`` is implemented for the skill-lookup and registry-summary
shapes (``storage/queries.py``). Its ``consolidation_id`` branch has
nothing to look up yet (no Dream in M0) and returns ``not_found`` rather
than fabricating a run. ``flag_dead`` needs ``eph_retrieval``/session
history that only becomes meaningful once signals exist (M3); it raises a
typed ``not_implemented`` for now.
"""

from __future__ import annotations

from magicite.errors import NotFoundError, NotImplementedToolError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    EdgeOut,
    FlagDeadInput,
    FlagDeadOutput,
    HistoryEntry,
    IntrospectInput,
    IntrospectOutput,
    RegistrySummary,
    SkillIntrospect,
    TierState,
)
from magicite.storage import queries as queries_mod


@magicite_tool(
    risk="R0",
    side_effect="none",
    idempotent=True,
    read_only=True,
    input_model=IntrospectInput,
    output_model=IntrospectOutput,
    description="Full audit: neighborhood, weights, history, signal tiers -- or a registry summary.",
)
def introspect(ctx: ToolContext, params: IntrospectInput) -> IntrospectOutput:
    if params.skill_id is not None:
        detail = queries_mod.skill_detail(ctx.conn, params.skill_id)
        if detail is None:
            raise NotFoundError(f"no engram named or id'd {params.skill_id!r}")
        return IntrospectOutput(
            skill=SkillIntrospect(**detail["skill"]),
            outbound_edges=[EdgeOut(**e) for e in detail["outbound_edges"]],
            inbound_edges=[EdgeOut(**e) for e in detail["inbound_edges"]],
            history=[HistoryEntry(**h) for h in detail["history"]],
            silent_engram_flag=detail["silent_engram_flag"],
            tier_state=TierState(**detail["tier_state"]),
        )

    if params.consolidation_id is not None:
        raise NotFoundError(
            f"no consolidation run {params.consolidation_id!r}",
            hint="Dream lands in M4; no consolidation_run rows exist in M0",
        )

    summary = queries_mod.registry_summary(
        ctx.conn, embedding_model=ctx.embedder.model_name, autonomous=ctx.cfg.autonomous
    )
    return IntrospectOutput(registry_summary=RegistrySummary(**summary))


@magicite_tool(
    risk="R0",
    side_effect="none",
    idempotent=True,
    read_only=True,
    input_model=FlagDeadInput,
    output_model=FlagDeadOutput,
    description="Find silent engrams: stored but never routed in the last N days.",
)
def flag_dead(ctx: ToolContext, params: FlagDeadInput) -> FlagDeadOutput:
    raise NotImplementedToolError(
        "flag_dead() needs decayed retrieval strength over real session history; "
        "it lands in M3 alongside core/signals.py",
    )
