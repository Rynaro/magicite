"""Direct (non-stdio) unit coverage for ``mcp/bind_signals.py``: proves the
adapter layer -> ``core.signals``/``core.session`` wiring (the "engine is a
library; MCP is an adapter" commitment, spec Approach commitment 1)."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.mcp import bind_signals
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import SessionEndInput, SignalOutcomeInput, SignalUseInput

PROTON = "proton-ge-proton-downgrade"


def test_signal_use_tool_tags_and_reports_tier(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_signals.signal_use(ctx, SignalUseInput(skill_ids=[PROTON], session_id="s1"))

    assert out.signal_tier == 1
    assert len(out.tagged) == 1
    assert out.capped == []


def test_signal_use_tool_reaches_tier2_with_the_correct_token(cfg, db_conn, embedder) -> None:
    cfg.hook_token = "correct-secret"
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_signals.signal_use(
        ctx, SignalUseInput(skill_ids=[PROTON], session_id="s1", adapter_token="correct-secret")
    )
    assert out.signal_tier == 2

    spoofed = bind_signals.signal_use(
        ctx, SignalUseInput(skill_ids=[PROTON], session_id="s2", adapter_token="hook_verified")
    )
    assert spoofed.signal_tier == 1


def test_signal_outcome_tool_captures_explicit_skill_ids(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    bind_signals.signal_use(ctx, SignalUseInput(skill_ids=[PROTON], session_id="s1"))

    out = bind_signals.signal_outcome(
        ctx, SignalOutcomeInput(valence=1.0, skill_ids=[PROTON], session_id="s1")
    )
    assert out.captured == 1
    assert out.signal_tier == 1
    assert out.consolidation_scheduled is False


def test_session_end_tool_closes_session(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    bind_signals.signal_use(ctx, SignalUseInput(skill_ids=[PROTON], session_id="s1"))

    out = bind_signals.session_end(ctx, SessionEndInput(session_id="s1", reason="task done"))

    assert out.session_id == "s1"
    assert out.closed is True
    assert out.tags_expired == 1
    assert out.enqueued is False
    assert out.dream_run_id is None
