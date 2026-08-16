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

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
from magicite.engram import parser as parser_mod
from magicite.storage import db as db_mod
from magicite.storage import queries as queries_mod

pytestmark = pytest.mark.acceptance

PROTON = "proton-ge-proton-downgrade"
STEAM_PREFIX = "steam-prefix-access"


def _delete_db_files(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        (db_path.parent / (db_path.name + suffix)).unlink(missing_ok=True)


def _rewind_edge_last_updated(conn, src_id: str, dst_name: str, edge_type: str, *, hours: float) -> None:
    """Fake a genuinely-separated second (or later) observation -- see
    ``core/plasticity.py::compute_eta_eff_untiered``'s docstring: a burst
    of same-instant observations must never fully potentiate an edge
    (spacing gate); only time-separated evidence can."""
    past = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    conn.execute(
        "UPDATE edge SET last_updated = ? WHERE src_id = ? AND dst_name = ? AND type = ?",
        (past, src_id, dst_name, edge_type),
    )


def test_durable_state_survives_rebuild(cfg, db_conn, embedder) -> None:
    """GIVEN a registry that has been consolidated at least once
    WHEN skill-graph.db is deleted and sync() is called
    THEN the durable projection of Tier A plus Tier B state SHALL be
    byte-identical to the pre-deletion projection."""
    register_outcome = registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
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
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
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


def test_learned_synapses_survive_rebuild(cfg, db_conn, embedder) -> None:
    """AC-009's own GIVEN is "a registry that has been consolidated at
    least once" -- until this test, nothing in this acceptance suite
    actually exercised that. ``storage/queries.py::durable_projection``'s
    edge query has no provenance filter, so it is *not* structurally
    blind to a lost learned edge -- but every case above only ever calls
    ``register()``/``sync()``, never Dream, so a *learned* (as opposed to
    merely-declared) edge was never actually present to lose. This test
    makes the fixture satisfy its own GIVEN: ten real ``signal_use ->
    signal_outcome -> dream.run()`` cycles (Tier-2/hook-verified, and
    properly time-spaced between cycles so the spacing gate in
    ``core/plasticity.py`` actually lets weight accrue instead of
    collapsing to a single burst) potentiate a real ``co_activation`` edge
    past ``theta_synapse`` with ``evidence_count >= 3`` -- the exact
    ``synapses:`` checkpoint condition, spec §4.5 -- and only then is
    ``skill-graph.db`` deleted and ``sync()`` re-run.

    Before the ``storage.durable.wire_synapse_edges`` fix (spec §2.6 step
    4's second half), this fails outright: ``sync()`` only ever re-wires
    ``needs``/``composes``/``inhibits`` from the frontmatter's declared
    composition fields, never reads the file's own ``synapses:`` block --
    and a purely-learned ``co_activation`` edge has no declared
    counterpart at all, so it has zero path back into a rebuilt DB."""
    register_outcome = registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert register_outcome.ingested == 7

    # Tier-2 (hook_verified): TIER_WEIGHT[2]==1.0 vs Tier-1's 0.6 cap
    # (core/plasticity.py). At Tier-1 the per-commit ceiling (eta * 0.6 ==
    # 0.048) is small enough that the edge spends 3 consecutive Dream runs
    # under theta_prune and core/dream.py::_prune_learned_edges deletes it
    # before it can ever reach theta_synapse -- this is real,
    # hook-verified-adapter evidence (spec §3.3 tool 5's adapter_token),
    # not a test artifice to dodge the prune floor.
    cfg.hook_token = "rebuild-invariant-hook-token"  # noqa: S105
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    steam_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (STEAM_PREFIX,)).fetchone()["id"]

    for i in range(10):
        session_id = f"rebuild-invariant-cycle-{i}"
        signals_mod.signal_use(
            cfg,
            db_conn,
            skill_ids=[PROTON, STEAM_PREFIX],
            session_id=session_id,
            adapter_token=cfg.hook_token,
        )
        signals_mod.signal_outcome(
            cfg,
            db_conn,
            valence=1.0,
            salience=1.0,
            skill_ids=[PROTON, STEAM_PREFIX],
            session_id=session_id,
            adapter_token=cfg.hook_token,
        )
        if i > 0:
            _rewind_edge_last_updated(db_conn, proton_id, STEAM_PREFIX, "co_activation", hours=24)
            _rewind_edge_last_updated(db_conn, steam_id, PROTON, "co_activation", hours=24)
        dream_mod.run(cfg, db_conn, trigger="manual")

    before = db_conn.execute(
        "SELECT storage_strength, evidence_count, provenance FROM edge "
        "WHERE src_id = ? AND dst_name = ? AND type = 'co_activation'",
        (proton_id, STEAM_PREFIX),
    ).fetchone()
    assert before is not None, "the 10-cycle potentiation loop must have created a real learned edge"
    assert before["provenance"] == "learned"
    assert before["evidence_count"] >= 3
    assert before["storage_strength"] > cfg.theta_synapse, (
        "must clear theta_synapse -- otherwise this edge would legitimately not "
        "appear in synapses: at all (spec §4.5) and this test would not actually "
        "be exercising the invariant it claims to"
    )

    db_conn.close()
    _delete_db_files(cfg.db_path)
    assert not cfg.db_path.exists()

    rebuilt_conn = db_mod.connect(cfg.db_path)
    try:
        rebuild_outcome = registry_mod.sync(cfg, rebuilt_conn, embedder)
        assert rebuild_outcome.validation_errors == []
        assert rebuild_outcome.synced == 7

        after = rebuilt_conn.execute(
            "SELECT storage_strength, evidence_count, provenance FROM edge "
            "WHERE src_id = ? AND dst_name = ? AND type = 'co_activation'",
            (proton_id, STEAM_PREFIX),
        ).fetchone()
        assert after is not None, (
            "the learned co_activation edge must survive a skill-graph.db rebuild "
            "-- sync() step 4 must read it back from the file's own synapses: "
            "block (spec §2.6), not just declared needs/composes/inhibits"
        )
        assert after["provenance"] == "learned"
        assert after["evidence_count"] == before["evidence_count"]
        # engram/writer.py's checkpoint renderer rounds every float to 4
        # decimals (AC-021 determinism / git-diff reviewability); the DB's
        # own in-memory value between checkpoints carries full float
        # precision, so the honest post-rebuild expectation is the
        # *file-rounded* value, not the pre-rebuild float exactly.
        assert after["storage_strength"] == round(before["storage_strength"], 4)
        assert after["storage_strength"] > cfg.theta_synapse
    finally:
        rebuilt_conn.close()


def test_peak_storage_strength_and_content_sha256_survive_rebuild(cfg, db_conn, embedder) -> None:
    """Two further rebuild losses ATLAS measured, closed by the same
    conformance pass as the synapses fix above:

    - ``peak_storage_strength`` (the M5 fix ``core/decay.py``'s AC-033
      auto-archive gate depends on) had no file representation at all;
      before this fix, a rebuild silently reset it to the engram's
      *current* S, defeating the requirement that a genuine prior peak
      exist before an honestly-decayed skill is auto-archived.
    - ``content_sha256`` drifted from the file's real digest on every
      single Dream checkpoint (``core/dream.py``'s Phase 7 previously
      updated only ``exposure_count``/``last_checkpoint``) --
      ``durable_projection()`` includes this column, so it alone would
      fail the rebuild invariant even with the synapses/peak fixes above
      in place.
    """
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]

    # An engram that reached a real historical high (0.6) and has since
    # decayed to 0.25 -- still routable, still above floor_archived (0.2),
    # so no archival fires here; only the peak/digest bookkeeping is
    # under test.
    db_conn.execute(
        "UPDATE engram SET storage_strength = 0.25, peak_storage_strength = 0.6, "
        "success_count = 3, last_applied = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), engram_id),
    )
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert PROTON in result.modified_engrams, "the seeded S must actually trigger a checkpoint write"

    file_path = cfg.project_root / db_conn.execute(
        "SELECT path FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["path"]
    real_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()

    before = db_conn.execute(
        "SELECT storage_strength, peak_storage_strength, content_sha256 FROM engram WHERE id = ?",
        (engram_id,),
    ).fetchone()
    assert before["content_sha256"] == real_sha256, (
        "engram.content_sha256 must match the file Dream's own checkpoint just "
        "wrote, not a stale pre-checkpoint digest"
    )
    assert before["peak_storage_strength"] == pytest.approx(0.6)

    db_conn.close()
    _delete_db_files(cfg.db_path)

    rebuilt_conn = db_mod.connect(cfg.db_path)
    try:
        registry_mod.sync(cfg, rebuilt_conn, embedder)
        after = rebuilt_conn.execute(
            "SELECT storage_strength, peak_storage_strength, content_sha256 FROM engram WHERE name = ?",
            (PROTON,),
        ).fetchone()
        assert after["peak_storage_strength"] == pytest.approx(0.6), (
            "peak_storage_strength must survive a rebuild -- without a file "
            "representation, this silently resets to current S and defeats "
            "archive_below_floor's peak-based eligibility gate"
        )
        assert after["content_sha256"] == real_sha256
    finally:
        rebuilt_conn.close()


def test_checkpoint_persists_provenance_journal_to_file(cfg, db_conn, embedder) -> None:
    """The defect VIVI found and empirically confirmed during the drift-fix
    pass, then correctly left out of scope: ``engram/writer.py::
    render_frontmatter``'s round-trip branch refreshes
    ``version``/``plasticity``/``peak_storage_strength``/``synapses`` on
    the ruamel carrier document but never ``provenance_journal`` -- so
    every Dream-checkpoint-appended entry (``core/dream.py::
    _phase7_checkpoint``'s ``event: consolidated``, ``core/decay.py``'s
    ``event: archived``, ...) is computed onto
    ``engram.frontmatter.provenance_journal`` in memory but never reaches
    the file, even on a dirty checkpoint write. ``docs/06-trust-
    governance-lifecycle.md`` sells the provenance trail as the audit
    mechanism for autonomous mutation -- a governance feature that
    silently no-ops is the same failure class as a phantom config knob.

    This is the reason the invariant above (``durable_projection()``
    before/after comparisons) structurally cannot catch it: the DB's own
    ``engram_journal`` mirror is written by a *separate* path
    (``storage/durable.py::upsert_journal``, called from
    ``register()``/``decay.py``'s archive path -- never from Dream's
    checkpoint phase), so a before/after DB projection stays
    self-consistent whether or not the value ever reaches the file. The
    only way to observe this bug is to read the real bytes Dream wrote to
    disk (and re-parse them), which is what this test does instead of
    extending the DB-projection-based tests above."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]

    # Force a genuinely dirty checkpoint the same way
    # test_peak_storage_strength_and_content_sha256_survive_rebuild does --
    # a changed storage_strength/peak_storage_strength is what makes Dream's
    # Phase 7 decide this engram needs a checkpoint write at all.
    db_conn.execute(
        "UPDATE engram SET storage_strength = 0.25, peak_storage_strength = 0.6, "
        "success_count = 3, last_applied = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), engram_id),
    )
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert PROTON in result.modified_engrams, "the seeded S must actually trigger a checkpoint write"

    file_path = cfg.project_root / db_conn.execute(
        "SELECT path FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["path"]
    on_disk = file_path.read_text()

    # The fixture's own pre-existing entry (event: authored) must still be
    # there -- this bug is about the *new* entry never being added, not
    # about the whole block disappearing.
    assert "event: authored" in on_disk

    assert "event: consolidated" in on_disk, (
        "Dream's checkpoint-appended provenance_journal entry must be "
        "persisted onto the .egr.md file's own bytes, not just computed "
        "onto the in-memory Engram object"
    )
    assert "author: dream-worker" in on_disk

    # And it must be real, round-trippable YAML -- not a coincidental
    # substring match -- by re-parsing the exact file Dream just wrote.
    reparsed = parser_mod.parse_file(file_path, registry_root=cfg.project_root.resolve())
    events = [e.event for e in reparsed.engram.frontmatter.provenance_journal]
    assert events == ["authored", "consolidated"], (
        "the re-parsed file's own provenance_journal must carry both the "
        "original fixture entry and the newly-appended checkpoint entry, "
        "in append order"
    )
    consolidated_entry = reparsed.engram.frontmatter.provenance_journal[-1]
    assert consolidated_entry.author == "dream-worker"
    assert consolidated_entry.timestamp, "the persisted entry must carry a real checkpoint timestamp"
