"""``introspect``/``flag_dead`` (spec §3.3 tools 3-4) at the MCP-adapter
layer -- M6 lands ``flag_dead`` for real (carried-forward defect #3, the
last ``not_implemented`` tool body in the 16-tool surface) and wires
``introspect(include_health=True)`` to ``obs/kpi.py``'s standing KPIs."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.mcp import bind_inspect
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import FlagDeadInput, IntrospectInput

PROTON = "proton-ge-proton-downgrade"


def test_flag_dead_flags_never_routed_engrams(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
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
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
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
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
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
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput(limit=2))

    assert len(out.candidates) == 2
    # silent_pct is computed over the whole registry, not just the page
    # returned after `limit` truncates it.
    assert out.silent_pct == 1.0


def test_flag_dead_recommendation_reflects_the_docs07_thresholds(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput())

    assert "systematically poor" in out.recommendation  # 100% silent > the 20% bar


def test_flag_dead_excludes_archived_engrams(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    db_conn.execute("UPDATE engram SET status = 'archived' WHERE name = ?", (PROTON,))
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.flag_dead(ctx, FlagDeadInput())

    assert PROTON not in {c.name for c in out.candidates}


def test_introspect_without_include_health_omits_health(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.introspect(ctx, IntrospectInput())

    assert out.health is None
    assert out.registry_summary is not None


def test_introspect_include_health_reports_standing_kpis(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    out = bind_inspect.introspect(ctx, IntrospectInput(include_health=True))

    assert out.health is not None
    assert out.health["registry_size"] == 7
    assert out.health["cold_start"]["below_break_even"] is True
    assert "silent_engrams" in out.health
    assert "hub_detection" in out.health
    assert "fitness_distribution" in out.health
