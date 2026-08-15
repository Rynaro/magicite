"""AC-009/AC-010: the rebuild invariant (spec §2.6) -- delete
``skill-graph.db``, run ``sync()``, and the durable projection (Tier A +
Tier B) comes back byte-identical; only Tier C (ephemeral retrieval
state, tags, candidate edges) is lost.

M1 note: full Dream consolidation ("a registry that has been consolidated
at least once", AC-009's GIVEN) is M4 scope (``core/dream.py``'s seven
phases). This suite proves the invariant over what M1 actually persists --
the Tier A node mirror and Tier B declared-edge state ``register()``/
``sync()`` copy verbatim from the ``.egr.md`` files (spec §2.6 step 3) --
which is the honest, buildable subset of the invariant at this milestone.
M4 reuses the same ``durable_projection()``/``tier_c_table_counts()``
helpers once Dream's checkpoint phase adds learned ``plasticity:``/
``synapses:`` state to what gets mirrored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magicite.core import registry as registry_mod
from magicite.storage import db as db_mod
from magicite.storage import queries as queries_mod

pytestmark = pytest.mark.acceptance


def _delete_db_files(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        (db_path.parent / (db_path.name + suffix)).unlink(missing_ok=True)


def test_durable_state_survives_rebuild(cfg, db_conn, embedder) -> None:
    """GIVEN a registry that has been consolidated at least once
    WHEN skill-graph.db is deleted and sync() is called
    THEN the durable projection of Tier A plus Tier B state SHALL be
    byte-identical to the pre-deletion projection."""
    register_outcome = registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    assert register_outcome.ingested == 7
    assert register_outcome.validation_errors == []

    sync_outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert sync_outcome.validation_errors == []
    assert sync_outcome.synced == 7

    before = queries_mod.durable_projection(db_conn)
    assert len(before["engrams"]) == 7
    assert before["steps"], "expected engram_step rows in the pre-deletion projection"
    assert before["triggers"], "expected engram_trigger rows in the pre-deletion projection"
    assert before["edges"], "expected declared edge rows (needs:) in the pre-deletion projection"
    assert before["journal"], "expected engram_journal rows mirrored from provenance_journal"

    db_conn.close()
    _delete_db_files(cfg.db_path)
    assert not cfg.db_path.exists()

    rebuilt_conn = db_mod.connect(cfg.db_path)
    try:
        rebuild_outcome = registry_mod.sync(cfg, rebuilt_conn, embedder)
        assert rebuild_outcome.validation_errors == []
        assert rebuild_outcome.synced == 7
        assert rebuild_outcome.removed == []

        after = queries_mod.durable_projection(rebuilt_conn)
        assert after == before
    finally:
        rebuilt_conn.close()


def test_imported_engrams_survive_resync_and_rebuild(cfg, db_conn, embedder, toy_registry_dir) -> None:
    """Regression: sync()'s registry-wide scan must not strict-relint an
    imported engram's own file (CR-4 keeps applying after the moment of
    conversion, spec §2.6 step 2's ``lint(profile=strict)`` is read as
    "strict for authored/sharpened/distilled files", not "strict for
    every ``.egr.md`` regardless of ``origin``"). Before this fix, a
    freshly-imported draft would report a hard ``negative_triggers``
    validation error on the very next ``sync()`` and, worse, would never
    re-appear at all after a DB rebuild -- silently violating AC-009 for
    every SKILL.md-imported engram."""
    import shutil

    from magicite.core import registry as registry_mod

    skills_dir = cfg.project_root / "skills"
    shutil.copytree(toy_registry_dir / "skills", skills_dir)
    register_outcome = registry_mod.register(cfg, db_conn, embedder, path="skills", fmt="skill")
    assert register_outcome.ingested == 3
    assert register_outcome.validation_errors == []

    # Re-running sync() against the now-materialised imported .egr.md files
    # (alongside the project_root fixture's 7 native engrams) must not flag
    # them as validation errors (CR-4 still applies).
    resync_outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert resync_outcome.validation_errors == []
    assert resync_outcome.synced == 10

    before = queries_mod.durable_projection(db_conn)
    imported_names = {e["name"] for e in before["engrams"] if e["origin"] == "imported"}
    assert imported_names == {
        "proton-battleye-eac-toggle",
        "steam-download-region-fix",
        "wine-dxvk-cache-clear",
    }

    db_conn.close()
    _delete_db_files(cfg.db_path)

    rebuilt_conn = db_mod.connect(cfg.db_path)
    try:
        rebuild_outcome = registry_mod.sync(cfg, rebuilt_conn, embedder)
        assert rebuild_outcome.validation_errors == []
        assert rebuild_outcome.synced == 10

        after = queries_mod.durable_projection(rebuilt_conn)
        assert after == before
        imported_statuses = {
            r["status"]
            for r in rebuilt_conn.execute(
                "SELECT status FROM engram WHERE origin = 'imported'"
            ).fetchall()
        }
        assert imported_statuses == {"draft"}
    finally:
        rebuilt_conn.close()


def test_only_tier_c_is_lost(cfg, db_conn, embedder) -> None:
    """GIVEN a freshly rebuilt index
    THEN all Tier-C tables (eph_retrieval, eph_tag, eph_candidate_edge,
    eph_embedding excepted for recompute) SHALL be empty."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)

    engram_id = db_conn.execute("SELECT id FROM engram LIMIT 1").fetchone()["id"]
    now = "2026-08-14T00:00:00Z"
    # Simulate real hot-path usage (route()/signal_use()-shaped rows) that
    # accumulated between syncs -- exactly the Tier-C state the invariant
    # says a rebuild is allowed to lose.
    db_conn.execute(
        "INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES (?, 0.9, ?)",
        (engram_id, now),
    )
    db_conn.execute(
        "INSERT INTO eph_tag (session_id, subject_kind, engram_id, signal_tier, set_at, expires_at) "
        "VALUES ('s1', 'node', ?, 1, ?, ?)",
        (engram_id, now, now),
    )
    db_conn.execute(
        "INSERT INTO eph_candidate_edge (src_id, dst_id, type, first_observed, last_updated) "
        "VALUES (?, ?, 'co_activation', ?, ?)",
        (engram_id, engram_id, now, now),
    )

    before_counts = queries_mod.tier_c_table_counts(db_conn)
    assert before_counts["eph_retrieval"] > 0
    assert before_counts["eph_tag"] > 0
    assert before_counts["eph_candidate_edge"] > 0
    assert before_counts["eph_embedding"] > 0  # register()/sync() already embedded everything

    db_conn.close()
    _delete_db_files(cfg.db_path)

    rebuilt_conn = db_mod.connect(cfg.db_path)
    try:
        registry_mod.sync(cfg, rebuilt_conn, embedder)
        after_counts = queries_mod.tier_c_table_counts(rebuilt_conn)
        for table, n in after_counts.items():
            if table == "eph_embedding":
                assert n > 0, "eph_embedding is recomputed on rebuild (sync() step 7), not lost"
            else:
                assert n == 0, f"{table} should be empty after a fresh rebuild, got {n} row(s)"
    finally:
        rebuilt_conn.close()
