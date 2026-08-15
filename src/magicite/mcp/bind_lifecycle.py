"""``sharpen``/``promote``/``archive`` (spec §3.3 tools 14-16).

M5: the lifecycle FSM (``core/lifecycle.py``) and approval machinery
(``core/approvals.py``) land. All three follow the same shape (spec
§3.3/§5.2): validate -> :func:`magicite.core.approvals.propose` an
``approval`` row -> if ``cfg.autonomous`` (docs/06 §Autonomous Mode),
immediately decide + execute and report the terminal state; otherwise
return ``requires_approval=True`` with the proposal left ``proposed``
(AC-027) and the underlying engram **not mutated**.

``archived -> probation`` revival and ``promote()``'s failed-guard path
never auto-execute even under autonomous mode -- spec §5.1 marks both
"manual, always", a stronger guarantee than the S-evidence-gated rows'
plain "auto if guard passes, else manual".
"""

from __future__ import annotations

import os
import uuid

from magicite.core import approvals as approvals_mod
from magicite.core import dream as dream_mod
from magicite.core import lifecycle as lifecycle_mod
from magicite.errors import NotFoundError, QuarantinedError, TransitionDeniedError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    ArchiveInput,
    ArchiveOutput,
    PromoteInput,
    PromoteOutput,
    SharpenInput,
    SharpenOutput,
)
from magicite.storage import durable as durable_mod
from magicite.storage import lease as lease_mod

#: v1 has no per-caller identity (local, single-writer, spec §5.2's "Interim Sharing").
_ACTOR_PREFIX = "mcp-caller"


def _fetch_engram_row(conn, name: str):  # noqa: ANN001, ANN202
    row = conn.execute("SELECT * FROM engram WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise NotFoundError(f"no engram named {name!r}")
    return row


def _cross_process_lease(ctx: ToolContext, holder_prefix: str) -> lease_mod.CrossProcessLease:
    """M5 data-integrity fix (spec §4.2, same defect class as
    ``core.registry``'s ``register()``/``sync()``/``export()``): every
    direct ``engram`` table write this module performs (the quarantine
    flip and the auto-execute status write, both bypassing ``core.
    lifecycle.execute_sharpen``/``core.dream.archive_engram``'s own
    lease acquisition) must not interleave with a running Dream cycle
    either."""
    ctx.cfg.ensure_dirs()
    return lease_mod.CrossProcessLease(
        lock_path=ctx.cfg.dream_lock_path,
        conn=ctx.conn,
        holder=f"{holder_prefix}:{os.getpid()}:{uuid.uuid4().hex[:6]}",
    )


@magicite_tool(
    risk="R3",
    side_effect="proposal->durable",
    idempotent=True,
    input_model=SharpenInput,
    output_model=SharpenOutput,
    description="Manual sharpening pass; approval-gated re-write of an engram.",
)
def sharpen(ctx: ToolContext, params: SharpenInput) -> SharpenOutput:
    _fetch_engram_row(ctx.conn, params.name)  # 404s before ever proposing

    payload = {
        "proposed_changes": params.proposed_changes.model_dump() if params.proposed_changes else {}
    }
    approval = approvals_mod.propose(
        ctx.conn, ctx.cfg, op="sharpen", target_name=params.name, payload=payload, proposed_by=_ACTOR_PREFIX
    )

    if not ctx.cfg.autonomous:
        return SharpenOutput(approval_id=approval.id, state=approval.state, requires_approval=True)

    approvals_mod.decide(
        ctx.conn, ctx.cfg, approval_id=approval.id, approve=True, decided_by=approvals_mod.AUTONOMOUS_ACTOR
    )
    approvals_mod.mark_executed(ctx.conn, ctx.cfg, approval_id=approval.id)
    try:
        result = lifecycle_mod.execute_sharpen(
            ctx.cfg,
            ctx.conn,
            ctx.embedder,
            name=params.name,
            proposed_changes=params.proposed_changes,
            actor=approvals_mod.AUTONOMOUS_ACTOR,
        )
    except Exception as exc:  # LintFailedError et al. -- spec §5.4: "nothing is half-applied"
        approvals_mod.mark_outcome(
            ctx.conn, ctx.cfg, approval_id=approval.id, succeeded=False, reason=str(exc)
        )
        return SharpenOutput(
            sharpened=False, approval_id=approval.id, state="failed", requires_approval=False
        )

    approvals_mod.mark_outcome(ctx.conn, ctx.cfg, approval_id=approval.id, succeeded=True)
    return SharpenOutput(
        sharpened=True,
        version_bumped=str(result.new_version),
        changes=result.changes,
        consolidation_scheduled=False,
        approval_id=approval.id,
        state="succeeded",
        requires_approval=False,
    )


@magicite_tool(
    risk="R3",
    side_effect="proposal->durable",
    idempotent=True,
    input_model=PromoteInput,
    output_model=PromoteOutput,
    description="Lifecycle transition upward; evidence-gated per core/fitness.py.",
)
def promote(ctx: ToolContext, params: PromoteInput) -> PromoteOutput:
    row = _fetch_engram_row(ctx.conn, params.name)
    project_root = ctx.cfg.project_root.resolve()

    # spec §5.1 "any -> quarantined ... none (safety direction is free)":
    # unconditional on status (unlike the ladder below, whose own re-scan
    # only ever fires as a side effect of evaluating the nascent branch's
    # guard) and outranks it -- a caller cannot leave a flagged engram
    # merely "denied" and routable-adjacent; it is quarantined immediately,
    # regardless of whether the engram is nascent, draft, probation, ...
    scan = lifecycle_mod.check_injection_scan(project_root, row)
    if scan.quarantine_recommended:
        with _cross_process_lease(ctx, "promote").acquire(), lease_mod.writer_lease(holder="promote"):
            durable_mod.set_verification_status(ctx.conn, engram_id=row["id"], to_status="quarantined")
        raise QuarantinedError(
            f"{params.name!r} failed the injection scan during promote() and was quarantined",
            details={
                "has_exec_blocks": scan.has_exec_blocks,
                "over_broad_triggers": scan.over_broad_triggers,
            },
        )

    revival = row["status"] == "archived"
    check = (
        lifecycle_mod.evaluate_revival(ctx.conn, row)
        if revival
        else lifecycle_mod.evaluate_upward_transition(ctx.conn, ctx.cfg, project_root, row, scan=scan)
    )

    if not check.passed:
        # AC-016: transition_denied, naming the unmet guards, engram
        # unchanged -- no approval is created for a guard that has not
        # been cleared (docs/06's "else propose + await review" governs
        # *when a passing evidence gate's write executes*, not whether an
        # unmet mechanical bar can be proposed around).
        raise TransitionDeniedError(
            f"{check.from_status} -> {check.to_status or '?'} denied: evidence bar not cleared",
            unmet=check.unmet,
        )

    approval = approvals_mod.propose(
        ctx.conn,
        ctx.cfg,
        op="promote",
        target_name=params.name,
        payload={"from_status": check.from_status, "to_status": check.to_status, "evidence": check.evidence},
        proposed_by=_ACTOR_PREFIX,
    )

    # revival ("archived -> probation") is "manual, always" (spec §5.1) --
    # never auto-executed, even under autonomous mode.
    if not ctx.cfg.autonomous or revival:
        return PromoteOutput(
            name=params.name,
            evidence=check.evidence,
            approval_id=approval.id,
            state=approval.state,
            requires_approval=True,
        )

    approvals_mod.decide(
        ctx.conn, ctx.cfg, approval_id=approval.id, approve=True, decided_by=approvals_mod.AUTONOMOUS_ACTOR
    )
    approvals_mod.mark_executed(ctx.conn, ctx.cfg, approval_id=approval.id)
    with _cross_process_lease(ctx, "promote").acquire(), lease_mod.writer_lease(holder="promote"):
        assert check.to_status is not None
        durable_mod.set_status(ctx.conn, engram_id=row["id"], to_status=check.to_status)
        durable_mod.append_transition_journal(
            ctx.conn,
            engram_id=row["id"],
            version=int(row["version"]),
            author=approvals_mod.AUTONOMOUS_ACTOR,
            event="promoted",
            note=f"{check.from_status} -> {check.to_status}",
        )
    approvals_mod.mark_outcome(ctx.conn, ctx.cfg, approval_id=approval.id, succeeded=True)

    return PromoteOutput(
        promoted=True,
        name=params.name,
        new_status=check.to_status,
        evidence=check.evidence,
        approval_id=approval.id,
        state="succeeded",
        requires_approval=False,
    )


@magicite_tool(
    risk="R3",
    side_effect="proposal->durable",
    idempotent=True,
    input_model=ArchiveInput,
    output_model=ArchiveOutput,
    description="Lifecycle transition to archived; never deletes, moves the file to .spectra/archive/.",
)
def archive(ctx: ToolContext, params: ArchiveInput) -> ArchiveOutput:
    row = _fetch_engram_row(ctx.conn, params.name)
    if row["status"] in dream_mod.NOT_ARCHIVABLE_STATUSES:
        raise TransitionDeniedError(
            f"cannot archive {params.name!r}: status {row['status']!r} is not a legal source "
            "for the any(!=draft) -> archived transition",
            unmet=[f"status {row['status']!r} is excluded"],
        )

    # AC-027: review mode (the default) creates the proposal and stops --
    # the engram is not mutated until a separate decision executes it.
    approval = approvals_mod.propose(
        ctx.conn, ctx.cfg, op="archive", target_name=params.name, payload={"reason": params.reason},
        proposed_by=_ACTOR_PREFIX,
    )

    if not ctx.cfg.autonomous:
        return ArchiveOutput(
            archived=False, name=params.name, approval_id=approval.id, state=approval.state,
            requires_approval=True,
        )

    approvals_mod.decide(
        ctx.conn, ctx.cfg, approval_id=approval.id, approve=True, decided_by=approvals_mod.AUTONOMOUS_ACTOR
    )
    approvals_mod.mark_executed(ctx.conn, ctx.cfg, approval_id=approval.id)
    try:
        entry = dream_mod.archive_engram(
            ctx.cfg, ctx.conn, name=params.name, reason=params.reason, actor=approvals_mod.AUTONOMOUS_ACTOR
        )
    except Exception as exc:
        approvals_mod.mark_outcome(
            ctx.conn, ctx.cfg, approval_id=approval.id, succeeded=False, reason=str(exc)
        )
        raise
    approvals_mod.mark_outcome(ctx.conn, ctx.cfg, approval_id=approval.id, succeeded=True)

    return ArchiveOutput(
        archived=True,
        name=params.name,
        provenance_entry=entry.archive_path,
        approval_id=approval.id,
        state="succeeded",
        requires_approval=False,
    )
