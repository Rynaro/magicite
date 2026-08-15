"""``core/dream.py``: per-phase stats, watermark advance, failure isolation
(spec §7.1 unit-test table row), the enqueue/dedup machinery, and the two
R1 input-hygiene properties FORGE's review made load-bearing for this
milestone: Dream's S-input is captured ``eph_tag`` rows only (never the
uncapped ``eph_event`` ledger), and a single burst call cannot fully
potentiate many edges at once (the spacing gate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
from magicite.errors import BusyError

PROTON = "proton-ge-proton-downgrade"
STEAM_PREFIX = "steam-prefix-access"


@pytest.fixture
def registered(cfg, db_conn, embedder) -> None:
    outcome = registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    assert outcome.ingested == 7


def _engram_id(db_conn, name: str) -> str:
    return str(db_conn.execute("SELECT id FROM engram WHERE name = ?", (name,)).fetchone()["id"])


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _rewind_last_applied(db_conn, engram_id: str, hours: float) -> None:
    past = _iso(datetime.now(UTC) - timedelta(hours=hours))
    db_conn.execute("UPDATE engram SET last_applied = ? WHERE id = ?", (past, engram_id))


def _rewind_edge_last_updated(db_conn, src_id: str, dst_name: str, edge_type: str, hours: float) -> None:
    past = _iso(datetime.now(UTC) - timedelta(hours=hours))
    db_conn.execute(
        "UPDATE edge SET last_updated = ? WHERE src_id = ? AND dst_name = ? AND type = ?",
        (past, src_id, dst_name, edge_type),
    )


# ── enqueue / dedup (spec §4.1) ───────────────────────────────────────────


def test_enqueue_creates_a_queued_run(cfg, db_conn) -> None:
    outcome = dream_mod.enqueue(db_conn, cfg, trigger="manual")
    assert outcome.enqueued is True
    assert outcome.status == "queued"
    row = db_conn.execute(
        "SELECT trigger, state FROM consolidation_run WHERE id = ?", (outcome.consolidation_id,)
    ).fetchone()
    assert row["trigger"] == "manual"
    assert row["state"] == "queued"


def test_enqueue_dedups_to_the_existing_queued_run(cfg, db_conn) -> None:
    first = dream_mod.enqueue(db_conn, cfg, trigger="manual")
    second = dream_mod.enqueue(db_conn, cfg, trigger="manual")
    assert second.enqueued is False
    assert second.consolidation_id == first.consolidation_id
    count = db_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]
    assert count == 1


def test_enqueue_min_interval_throttle(cfg, db_conn) -> None:
    """AC-032's underlying mechanism (the min_interval_s half): a
    just-finished run blocks a fresh enqueue within dream.min_interval_s
    when apply_min_interval=True (session_end's path); consolidate()
    itself never applies this throttle (spec §4.1: "always on")."""
    now = _iso(datetime.now(UTC))
    db_conn.execute(
        "INSERT INTO consolidation_run (id, trigger, state, watermark_event_id, finished_at) "
        "VALUES ('dream_prev', 'manual', 'succeeded', 0, ?)",
        (now,),
    )
    outcome = dream_mod.enqueue(db_conn, cfg, trigger="session_end", apply_min_interval=True)
    assert outcome.enqueued is False
    assert outcome.status == "throttled"
    assert outcome.consolidation_id is None


def test_enqueue_min_interval_does_not_apply_to_consolidate(cfg, db_conn) -> None:
    now = _iso(datetime.now(UTC))
    db_conn.execute(
        "INSERT INTO consolidation_run (id, trigger, state, watermark_event_id, finished_at) "
        "VALUES ('dream_prev', 'manual', 'succeeded', 0, ?)",
        (now,),
    )
    outcome = dream_mod.enqueue(db_conn, cfg, trigger="manual", apply_min_interval=False)
    assert outcome.enqueued is True


# ── run(): watermark, phase stats, idempotency, failure isolation ──────


def test_run_advances_the_watermark(cfg, db_conn, registered) -> None:
    engram_id = _engram_id(db_conn, PROTON)
    db_conn.execute(
        "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, payload_json) "
        "VALUES (?, 's1', 'route', 0, ?, '{}')",
        (_iso(datetime.now(UTC)), engram_id),
    )
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert result.watermark_event_id >= 1
    row = db_conn.execute(
        "SELECT watermark_event_id FROM consolidation_run WHERE id = ?", (result.run_id,)
    ).fetchone()
    assert row["watermark_event_id"] == result.watermark_event_id


def test_run_reports_every_phase_in_stats(cfg, db_conn, registered) -> None:
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    for phase in ("replay", "potentiate", "decay", "renormalise", "distill", "audit", "checkpoint"):
        assert phase in result.stats, f"missing phase stats: {phase}"


def test_run_marks_state_succeeded(cfg, db_conn, registered) -> None:
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert result.state == "succeeded"
    row = db_conn.execute(
        "SELECT state, phase FROM consolidation_run WHERE id = ?", (result.run_id,)
    ).fetchone()
    assert row["state"] == "succeeded"
    assert row["phase"] is None


def test_run_failure_is_isolated_and_recorded(cfg, db_conn, registered, monkeypatch) -> None:
    """spec §7.1 unit-test table row ("core/dream.py: ... failure
    isolation"): a phase raising marks the run 'failed' with the error
    recorded, does not corrupt the DB into an unusable state, and a fresh
    run afterward can proceed normally (the failed run is not stuck
    'running' forever, blocking every future attempt)."""

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic phase failure")

    monkeypatch.setattr(dream_mod, "_phase4_renormalise", _boom)

    with pytest.raises(RuntimeError, match="synthetic phase failure"):
        dream_mod.run(cfg, db_conn, trigger="manual")

    row = db_conn.execute(
        "SELECT state, error FROM consolidation_run ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row["state"] == "failed"
    assert "synthetic phase failure" in row["error"]

    monkeypatch.undo()
    # A fresh run must succeed -- the failed run's cross-process lease was
    # released (context manager __exit__ always runs) and it does not
    # dedup-block a new attempt (only queued/running runs dedup).
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert result.state == "succeeded"


def test_second_run_over_unchanged_state_writes_zero_files(cfg, db_conn, registered) -> None:
    """AC-020 at the unit level (the acceptance suite proves the same
    thing end-to-end): a Dream run with nothing new since the last one
    checkpoints zero engrams."""
    dream_mod.run(cfg, db_conn, trigger="manual")
    second = dream_mod.run(cfg, db_conn, trigger="manual")
    assert second.checkpoint_write_ratio == 0.0
    assert second.modified_engrams == []


# ── R1 hardening: eph_event is never a plasticity-S input ──────────────


def test_eph_event_flooding_cannot_move_storage_strength(cfg, db_conn, registered) -> None:
    """The concrete FORGE finding: 100 unauthenticated, uncapped
    eph_event rows stamped Tier-1 with valence=+1.0 (exactly what 100
    no-op signal_outcome() calls with no live tags produce, per spec's own
    credit-set rule: nothing is captured, but the Tier-0 ledger event is
    still written by mcp/app.py's dispatcher) must move nothing -- Dream's
    Phase 2 never reads eph_event for S at all."""
    engram_id = _engram_id(db_conn, PROTON)
    before = db_conn.execute(
        "SELECT storage_strength FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["storage_strength"]

    now = _iso(datetime.now(UTC))
    for _ in range(100):
        db_conn.execute(
            "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, valence, payload_json) "
            "VALUES (?, 's-flood', 'signal_outcome', 1, ?, 1.0, '{}')",
            (now, engram_id),
        )

    result = dream_mod.run(cfg, db_conn, trigger="manual")

    after = db_conn.execute(
        "SELECT storage_strength FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["storage_strength"]
    assert after == before
    assert result.stats["potentiate"]["committed_nodes"] == 0


# ── R1 hardening: spacing-gated edge potentiation / fan-out bound ──────


def test_burst_signal_use_does_not_fully_potentiate_all_pairs_in_one_run(cfg, db_conn, registered) -> None:
    """FORGE's concrete finding: "one 20-id call currently asserts 380
    directed facts from a single observation." This is the Dream-side
    close: a single burst of co-activation candidate/edge-tag evidence,
    all captured in ONE call (no real time separation), commits ZERO
    S_edge in the very next Dream run -- only a properly time-spaced
    SECOND observation can. (core/signals.py generating the O(n^2) rows in
    the first place is a resource-bound concern flagged separately in the
    M4 report, not fixed here.)"""
    names = [PROTON, STEAM_PREFIX, "proton-clean-install", "proton-verify-installation"]
    signals_mod.signal_use(cfg, db_conn, skill_ids=names, session_id="burst")
    signals_mod.signal_outcome(cfg, db_conn, valence=0.9, salience=0.9, skill_ids=names, session_id="burst")

    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert result.stats["potentiate"]["committed_edges"] == 0

    learned_edges_with_weight = db_conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE provenance = 'learned' AND storage_strength > 0"
    ).fetchone()["n"]
    assert learned_edges_with_weight == 0


def test_edge_potentiates_only_after_a_properly_spaced_second_observation(cfg, db_conn, registered) -> None:
    proton_id = _engram_id(db_conn, PROTON)
    steam_id = _engram_id(db_conn, STEAM_PREFIX)

    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON, STEAM_PREFIX], session_id="s1")
    signals_mod.signal_outcome(
        cfg, db_conn, valence=0.9, salience=0.9, skill_ids=[proton_id], session_id="s1"
    )
    dream_mod.run(cfg, db_conn, trigger="manual")  # first pass: anchors established, S_edge still 0

    _rewind_edge_last_updated(db_conn, proton_id, "steam-prefix-access", "co_activation", hours=7)
    _rewind_edge_last_updated(db_conn, steam_id, "proton-ge-proton-downgrade", "co_activation", hours=7)

    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON, STEAM_PREFIX], session_id="s2")
    signals_mod.signal_outcome(
        cfg, db_conn, valence=0.9, salience=0.9, skill_ids=[proton_id], session_id="s2"
    )
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert result.stats["potentiate"]["committed_edges"] >= 1

    row = db_conn.execute(
        "SELECT storage_strength FROM edge WHERE src_id = ? AND dst_name = 'steam-prefix-access' "
        "AND type = 'co_activation'",
        (proton_id,),
    ).fetchone()
    assert row is not None and row["storage_strength"] > 0.0


# ── AC-025 at the core.dream.run() level (integration suite has the full,
#    two-process-flavoured version; this is the same mechanism, unit-scoped) ──


def test_run_raises_busy_when_lease_already_held(cfg, db_conn, registered) -> None:
    from magicite.storage import lease as lease_mod

    held = lease_mod.CrossProcessLease(lock_path=cfg.dream_lock_path, conn=db_conn, holder="external")
    held.try_acquire()
    try:
        run_count_before = db_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]
        with pytest.raises(BusyError):
            dream_mod.run(cfg, db_conn, trigger="manual")
        run_count_after = db_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]
        assert run_count_after == run_count_before
    finally:
        held.release()
