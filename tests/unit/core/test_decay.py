"""``core/decay.py``: lazy R/S decay, epsilon-gated materialisation, and
decay-floor archival (spec §4.3 Phase 3, §6.1, AC-033)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from magicite.core import decay as decay_mod
from magicite.core import registry as registry_mod

PROTON = "proton-ge-proton-downgrade"


@pytest.fixture
def registered(cfg, db_conn, embedder) -> None:
    outcome = registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    assert outcome.ingested == 7


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── effective_value: pure decay math ────────────────────────────────────


def test_effective_value_never_anchored_is_unchanged() -> None:
    assert decay_mod.effective_value(0.5, None, _iso(datetime.now(UTC)), 0.1) == 0.5


def test_effective_value_decays_exponentially() -> None:
    now = datetime.now(UTC)
    anchor = now - timedelta(days=10)
    # lambda=0.1/day over 10 days -> factor e^-1 ~= 0.3679
    result = decay_mod.effective_value(1.0, _iso(anchor), _iso(now), 0.1)
    assert result == pytest.approx(0.36788, rel=1e-3)


def test_effective_value_future_anchor_is_a_noop() -> None:
    """A decayed_at timestamp in the future (clock skew, or a row that was
    just written) never *increases* the value -- dt<=0 is treated as no
    time having passed."""
    now = datetime.now(UTC)
    future = now + timedelta(hours=1)
    assert decay_mod.effective_value(0.5, _iso(future), _iso(now), 0.1) == 0.5


# ── materialize_retrieval: always writes (R3 doesn't apply to Tier C) ──


def test_materialize_retrieval_decays_every_row(cfg, db_conn, registered) -> None:
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    old_anchor = _iso(datetime.now(UTC) - timedelta(days=20))
    db_conn.execute(
        "INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES (?, 1.0, ?)",
        (engram_id, old_anchor),
    )
    now = _iso(datetime.now(UTC))
    stats = decay_mod.materialize_retrieval(db_conn, cfg, now=now)
    assert stats.rows_decayed == 1
    row = db_conn.execute(
        "SELECT r, r_decayed_at FROM eph_retrieval WHERE engram_id = ?", (engram_id,)
    ).fetchone()
    assert row["r"] < 1.0  # lambda_r_per_day default 0.1: 20 days is a real decay
    assert row["r_decayed_at"] == now


# ── materialize_storage_strength: epsilon-gated, threshold-crossing ────


def test_storage_strength_not_materialized_below_epsilon(cfg, db_conn, registered) -> None:
    """A drift smaller than epsilon_write (0.05) and not crossing any
    lifecycle threshold is left exactly as-is -- R3's "lazy S
    materialisation" half of the checkpoint-churn defense."""
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    # S=0.5, decayed_at 1 hour ago -> negligible drift at lambda_s=0.01/day
    anchor = _iso(datetime.now(UTC) - timedelta(hours=1))
    db_conn.execute(
        "UPDATE engram SET storage_strength = 0.5, s_decayed_at = ? WHERE id = ?", (anchor, engram_id)
    )
    now = _iso(datetime.now(UTC))
    stats = decay_mod.materialize_storage_strength(db_conn, cfg, now=now)
    assert stats.engrams_materialized == 0
    row = db_conn.execute(
        "SELECT storage_strength, s_decayed_at FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()
    assert row["storage_strength"] == 0.5
    assert row["s_decayed_at"] == anchor  # anchor itself untouched too


def test_storage_strength_materialized_on_threshold_crossing(cfg, db_conn, registered) -> None:
    """Even a sub-epsilon drift is materialised if it crosses a lifecycle
    threshold (spec §6.1) -- here, floor_archived=0.2."""
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    # S just above the floor; a tiny decay pushes it under 0.2.
    anchor = _iso(datetime.now(UTC) - timedelta(days=1))
    db_conn.execute(
        "UPDATE engram SET storage_strength = 0.2001, s_decayed_at = ? WHERE id = ?", (anchor, engram_id)
    )
    now = _iso(datetime.now(UTC))
    stats = decay_mod.materialize_storage_strength(db_conn, cfg, now=now)
    assert stats.engrams_materialized == 1
    row = db_conn.execute("SELECT storage_strength FROM engram WHERE id = ?", (engram_id,)).fetchone()
    assert row["storage_strength"] < 0.2


# ── purge_retention ──────────────────────────────────────────────────────


def test_purge_retention_deletes_only_old_rows(cfg, db_conn, registered) -> None:
    old_ts = _iso(datetime.now(UTC) - timedelta(days=cfg.retention_days + 5))
    recent_ts = _iso(datetime.now(UTC))
    db_conn.execute(
        "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, payload_json) "
        "VALUES (?, 's1', 'route', 0, NULL, '{}')",
        (old_ts,),
    )
    db_conn.execute(
        "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, payload_json) "
        "VALUES (?, 's1', 'route', 0, NULL, '{}')",
        (recent_ts,),
    )
    stats = decay_mod.purge_retention(db_conn, cfg, now=_iso(datetime.now(UTC)))
    assert stats.events_deleted == 1
    remaining = db_conn.execute("SELECT COUNT(*) AS n FROM eph_event").fetchone()["n"]
    assert remaining == 1


# ── archive_below_floor: AC-033 ──────────────────────────────────────────


def _seed_evidenced_engram(db_conn, name: str, *, storage_strength: float) -> str:
    """Simulate "an engram with real, accumulated evidence" (spec's own
    docs/03 framing: archival is for a skill that *reaches*/*has decayed*
    below the floor, not a brand-new one) -- >=3 recorded outcomes and a
    real S value, exactly AC-033's GIVEN precondition."""
    row = db_conn.execute("SELECT id FROM engram WHERE name = ?", (name,)).fetchone()
    engram_id = row["id"]
    db_conn.execute(
        "UPDATE engram SET storage_strength = ?, success_count = 3, last_applied = ? WHERE id = ?",
        (storage_strength, _iso(datetime.now(UTC)), engram_id),
    )
    return str(engram_id)


def test_archive_below_floor_moves_file_never_deletes(cfg, db_conn, registered) -> None:
    """AC-033: GIVEN an engram whose effective storage strength has decayed
    below floor_archived THEN the next Dream run SHALL move its file into
    .spectra/archive/ without deleting it."""
    engram_id = _seed_evidenced_engram(db_conn, PROTON, storage_strength=0.05)
    file_path = cfg.project_root / db_conn.execute(
        "SELECT path FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["path"]
    assert file_path.is_file()

    from magicite.core.dream import checkpoint_phase
    from magicite.storage import lease as lease_mod

    with lease_mod.writer_lease(), checkpoint_phase():
        archived = decay_mod.archive_below_floor(cfg, db_conn, now=_iso(datetime.now(UTC)))

    assert len(archived) == 1
    assert archived[0].name == PROTON
    assert not file_path.exists(), "the registry copy must be gone (moved, not duplicated)"
    archive_path = cfg.project_root / archived[0].archive_path
    assert archive_path.is_file(), "the archive copy must exist -- never deleted"
    assert "status: archived" in archive_path.read_text(encoding="utf-8")

    row = db_conn.execute("SELECT status FROM engram WHERE id = ?", (engram_id,)).fetchone()
    assert row["status"] == "archived"


def test_archive_below_floor_requires_dream_context(cfg, db_conn, registered) -> None:
    """G3: archival writes plasticity: through write_plasticity()/
    write_synapses(), which assert Dream's checkpoint context -- calling it
    outside that context must fail, not silently write."""
    from magicite.storage.lease import DreamContextError

    _seed_evidenced_engram(db_conn, PROTON, storage_strength=0.05)
    with pytest.raises(DreamContextError):
        decay_mod.archive_below_floor(cfg, db_conn, now=_iso(datetime.now(UTC)))


def test_brand_new_engram_is_never_archived_on_first_dream_run(cfg, db_conn, registered) -> None:
    """A freshly-registered engram's default S=0.0 is < floor_archived
    (0.2), but it has zero recorded outcomes -- it must NOT be archived
    just because it has never been touched (see core/decay.py's
    archive_below_floor docstring for why >=3 outcomes gates eligibility,
    not merely S < floor)."""
    from magicite.core.dream import checkpoint_phase
    from magicite.storage import lease as lease_mod

    with lease_mod.writer_lease(), checkpoint_phase():
        archived = decay_mod.archive_below_floor(cfg, db_conn, now=_iso(datetime.now(UTC)))
    assert archived == []
