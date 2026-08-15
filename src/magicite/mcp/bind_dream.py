"""``consolidate``/``nucleate`` (spec §3.3 tools 12-13).

M4: the Dream worker orchestrator (``core/dream.py``, its seven phases)
lands, and ``consolidate()`` becomes real -- enqueue-only, fast (spec §4.1:
"If a run is queued/running, returns THAT consolidation_id (idempotent)").
It never runs Dream's phases inline; the actual phase execution is
``core.dream.run()``, invoked from ``magicite dream --once`` (CLI) or a
future idle-poll worker (off by default in v1, spec §4.1) -- never from
inside a live ``serve`` process by default, which is why the M4
fd-diversion/``multiprocessing`` question (flagged by FORGE) does not apply
here: see the M4 report for the full statement.

M6: ``nucleate()`` is real, backed by ``core/distill.py::
run_distillation`` -- the same function Dream's own Phase 5
(``core/dream.py``) now calls automatically. Both are proposal-only
(CR-3): this tool creates ``approval`` rows and returns candidates; it
never writes an engram.
"""

from __future__ import annotations

from magicite.core import distill as distill_mod
from magicite.core import dream as dream_mod
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    ConsolidateInput,
    ConsolidateOutput,
    NucleateCandidate,
    NucleateInput,
    NucleateOutput,
)


@magicite_tool(
    risk="R3",
    side_effect="batch",
    idempotent=True,
    input_model=ConsolidateInput,
    output_model=ConsolidateOutput,
    description="Trigger the offline Dream consolidation worker (enqueue-only, idempotent).",
)
def consolidate(ctx: ToolContext, params: ConsolidateInput) -> ConsolidateOutput:
    # spec §4.1: "manual_trigger=true only reorders the queue; it never runs
    # a second writer" -- v1's queue is a single row (at most one
    # queued/one running, spec §4.1), so there is nothing to reorder;
    # both values of the flag record the same 'manual' trigger (an
    # explicit tool call, distinct from the 'session_end' auto-trigger) and
    # go through the same enqueue/dedup path, with no min-interval throttle
    # ("Explicit tool call | ... | always on"). `params.manual_trigger` is
    # accepted (frozen input schema, spec §3.3) but does not change the
    # trigger label or throttle behavior for the reason above.
    outcome = dream_mod.enqueue(ctx.conn, ctx.cfg, trigger="manual", apply_min_interval=False)
    return ConsolidateOutput(
        consolidation_id=outcome.consolidation_id or "",
        enqueued=outcome.enqueued,
        status=outcome.status,
        estimated_start=None,
        note=outcome.note,
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
    outcome = distill_mod.run_distillation(
        ctx.cfg,
        ctx.conn,
        min_support=params.min_support,
        session_ids=params.trace_ids,
        proposed_by="nucleate-tool",
    )
    return NucleateOutput(
        candidates=[
            NucleateCandidate(
                proposal_id=approval_id,
                path_names=cand.path_names,
                support=cand.support,
                mean_valence=cand.mean_valence,
                trace_ir=cand.trace_ir,
                draft_skeleton=cand.draft_skeleton,
            )
            for approval_id, cand in zip(outcome.approval_ids, outcome.candidates, strict=True)
        ],
        approval_ids=outcome.approval_ids,
        note=outcome.note,
    )
