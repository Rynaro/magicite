"""``introspect`` + ``flag_dead`` (spec §3.3 tools 3-4). Both R0, read-only.

M0: ``introspect`` is implemented for the skill-lookup and registry-summary
shapes (``storage/queries.py``). Its ``consolidation_id`` branch has
nothing to look up yet (no Dream in M0) and returns ``not_found`` rather
than fabricating a run.

M6 (carried-forward defect #3, the last ``not_implemented`` tool body in
the 16-tool surface): ``flag_dead`` is now real, backed by
``storage.queries.dead_candidates`` (docs/07 "Silent Engram Report" --
skills stored but never routed within ``window_days``). M6 also wires
``introspect(include_health=True)`` to the standing-KPI computation in
``obs/kpi.py`` (silent-engram %, hub/black-hole traffic share, skill-
fitness distribution, the honest registry_size/cold-start note, R9).
"""

from __future__ import annotations

from magicite.errors import NotFoundError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    DeadCandidate,
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
from magicite.obs import kpi as kpi_mod
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
        detail = queries_mod.skill_detail(
            ctx.conn, params.skill_id, declared_edge_strength=ctx.cfg.declared_edge_strength
        )
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
    health = kpi_mod.compute_standing_kpis(ctx.cfg, ctx.conn) if params.include_health else None
    return IntrospectOutput(registry_summary=RegistrySummary(**summary), health=health)


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
    result = queries_mod.dead_candidates(
        ctx.conn,
        window_days=params.window_days,
        limit=params.limit,
        lambda_r_per_day=ctx.cfg.lambda_r_per_day,
    )
    silent_pct = float(result["silent_pct"])
    # docs/07 §Silent Engram Report KPI: target <10%; ">20% indicates
    # routing cues are systematically poor" -- verbatim thresholds.
    if silent_pct > 0.20:
        recommendation = (
            f"{silent_pct:.0%} of the registry is silent (>20%): routing cues are "
            "systematically poor (docs/07) -- review triggers/descriptions broadly, "
            "not just for the individually flagged candidates."
        )
    elif silent_pct > 0.10:
        recommendation = (
            f"{silent_pct:.0%} of the registry is silent (above the <10% target, docs/07): "
            "review the flagged candidates' triggers and descriptions; sharpen() or archive() "
            "the ones that are genuinely unreachable."
        )
    else:
        recommendation = f"{silent_pct:.0%} silent -- within the docs/07 <10% target."
    return FlagDeadOutput(
        candidates=[DeadCandidate(**c) for c in result["candidates"]],
        recommendation=recommendation,
        silent_pct=silent_pct,
    )
