"""``signal_use``/``signal_outcome``/``session_end`` (spec §3.3 tools 5-7).

M3: the Tier 0/1/2 signal ladder lands -- ``core/signals.py`` (tag
set/capture, co-activation, per-session caps, server-side tier assignment)
and ``core/session.py`` (session resolution + ``session_end``).

**AC-024 / hot path:** this module MUST NOT import ``magicite.storage.durable``
or ``magicite.engram.writer`` -- every tool here receives only the
authorizer-restricted connection (spec §6.2 G1, ``storage/authorizer.py``),
and only ever writes ``eph_*`` tables, via ``core.signals``/``core.session``
-> ``storage.ephemeral``.
"""

from __future__ import annotations

from magicite.core import session as session_mod
from magicite.core import signals as signals_mod
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    SessionEndInput,
    SessionEndOutput,
    SignalOutcomeInput,
    SignalOutcomeOutput,
    SignalUseInput,
    SignalUseOutput,
)


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
    outcome = signals_mod.signal_use(
        ctx.cfg,
        ctx.conn,
        skill_ids=params.skill_ids,
        session_id=params.session_id,
        adapter_token=params.adapter_token,
    )
    return SignalUseOutput(
        tagged=outcome.tagged,
        co_activation_candidates=outcome.co_activation_candidates,
        expires_at=outcome.expires_at,
        signal_tier=outcome.signal_tier,
        capped=outcome.capped,
        note=outcome.note,
    )


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
    outcome = signals_mod.signal_outcome(
        ctx.cfg,
        ctx.conn,
        valence=params.valence,
        salience=params.salience,
        skill_ids=params.skill_ids,
        session_id=params.session_id,
        adapter_token=params.adapter_token,
    )
    return SignalOutcomeOutput(
        captured=outcome.captured,
        skills_credited=outcome.skills_credited,
        signal_tier=outcome.signal_tier,
        consolidation_scheduled=outcome.consolidation_scheduled,
        note=outcome.note,
    )


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
    outcome = session_mod.session_end(ctx.cfg, ctx.conn, session_id=params.session_id, reason=params.reason)
    return SessionEndOutput(
        session_id=outcome.session_id,
        closed=outcome.closed,
        tags_expired=outcome.tags_expired,
        captured_pending=outcome.captured_pending,
        dream_run_id=outcome.dream_run_id,
        enqueued=outcome.enqueued,
    )
