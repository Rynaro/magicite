"""``introspect``/``flag_dead`` (spec §3.3 tools 3-4) at the MCP-adapter
layer -- M6 lands ``flag_dead`` for real (carried-forward defect #3, the
last ``not_implemented`` tool body in the 16-tool surface) and wires
``introspect(include_health=True)`` to ``obs/kpi.py``'s standing KPIs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from magicite.core import registry as registry_mod
from magicite.mcp import bind_inspect
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import FlagDeadInput, IntrospectInput

PROTON = "proton-ge-proton-downgrade"


def test_flag_dead_flags_never_routed_engrams(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput())

    names = {c.name for c in out.candidates}
    assert names == {
        "lutris-wine-prefix-setup",
        "nvidia-prime-render-offload",
        "proton-clean-install",
        "proton-ge-proton-downgrade",
        "proton-verify-installation",
        "steam-prefix-access",
        "steam-runtime-repair",
    }
    assert out.silent_pct == 1.0
    for c in out.candidates:
        assert c.reason == "never routed"
        assert c.last_routed is None


def test_flag_dead_excludes_recently_routed_engrams(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    from datetime import UTC, datetime

    db_conn.execute(
        "INSERT INTO eph_bookkeeping (engram_id, exposure_delta, last_activated, route_returns) "
        "VALUES (?, 0, ?, 3)",
        (proton_id, datetime.now(UTC).isoformat()),
    )
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput())

    names = {c.name for c in out.candidates}
    assert PROTON not in names
    assert out.silent_pct < 1.0


def test_flag_dead_respects_the_window(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    db_conn.execute(
        "INSERT INTO eph_bookkeeping (engram_id, exposure_delta, last_activated, route_returns) "
        "VALUES (?, 0, ?, 3)",
        (proton_id, old),
    )
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput(window_days=30))

    proton_candidate = next(c for c in out.candidates if c.name == PROTON)
    assert "not routed in the last 30 day(s)" in proton_candidate.reason


def test_flag_dead_respects_limit(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput(limit=2))

    assert len(out.candidates) == 2
    # silent_pct is computed over the whole registry, not just the page
    # returned after `limit` truncates it.
    assert out.silent_pct == 1.0


def test_flag_dead_recommendation_reflects_the_docs07_thresholds(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput())

    assert "systematically poor" in out.recommendation  # 100% silent > the 20% bar


def test_flag_dead_excludes_archived_engrams(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    db_conn.execute("UPDATE engram SET status = 'archived' WHERE name = ?", (PROTON,))
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput())

    assert PROTON not in {c.name for c in out.candidates}


def test_introspect_without_include_health_omits_health(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.introspect(ctx, IntrospectInput())

    assert out.health is None
    assert out.registry_summary is not None


def test_introspect_include_health_reports_standing_kpis(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.introspect(ctx, IntrospectInput(include_health=True))

    assert out.health is not None
    assert out.health["registry_size"] == 7
    assert out.health["cold_start"]["below_reference_size"] is True
    assert "silent_engrams" in out.health
    assert "hub_detection" in out.health
    assert "fitness_distribution" in out.health


def test_introspect_consolidation(cfg, db_conn, embedder) -> None:
    stats = {"audit": {"silent": 2}, "checkpoint": {"checkpointed": 1}}
    db_conn.execute(
        "INSERT INTO consolidation_run (id, trigger, state, stats_json) VALUES (?,?,?,?)",
        ("con_v03", "manual", "succeeded", json.dumps(stats)),
    )
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    out = bind_inspect.introspect(ctx, IntrospectInput(consolidation_id="con_v03"))
    assert out.consolidation is not None
    assert out.consolidation.stats == stats
    assert out.consolidation.audit_report == stats["audit"]


def test_introspect_projects_live_state(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    skill_id = db_conn.execute(
        "SELECT id FROM engram WHERE name = ?", (PROTON,)
    ).fetchone()["id"]
    now = datetime.now(UTC)
    db_conn.execute(
        "UPDATE engram SET storage_strength=0.8, success_count=3, failure_count=1, "
        "s_decayed_at=? WHERE id=?",
        ((now - timedelta(days=2)).isoformat(), skill_id),
    )
    db_conn.execute(
        "INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES (?,?,?)",
        (skill_id, 0.6, (now - timedelta(days=2)).isoformat()),
    )
    db_conn.execute(
        "INSERT INTO eph_tag (session_id, subject_kind, engram_id, signal_tier, set_at, expires_at) "
        "VALUES ('s', 'node', ?, 1, ?, ?)",
        (skill_id, now.isoformat(), (now + timedelta(hours=1)).isoformat()),
    )
    db_conn.execute(
        "INSERT INTO eph_candidate_edge (src_id, dst_id, type, pending_dw, evidence_count, "
        "first_observed, last_updated) VALUES (?,?, 'co_activation', 0.12, 1, ?, ?)",
        (skill_id, skill_id, now.isoformat(), now.isoformat()),
    )
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    out = bind_inspect.introspect(ctx, IntrospectInput(skill_id=skill_id))
    assert out.tier_state is not None
    assert out.tier_state.storage_strength_effective_now < 0.8
    assert out.tier_state.retrieval_strength < 0.6
    assert out.tier_state.reliability == 0.75
    assert out.tier_state.live_tags == 1
    assert out.tier_state.pending_dw == 0.12
