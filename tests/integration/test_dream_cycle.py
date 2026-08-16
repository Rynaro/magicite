"""AC-025 (single writer), AC-032 (session_end debounce), AC-033 (decay
floor archives, never deletes) -- the three integration-level Dream
acceptance criteria (spec §7.2's frozen VERIFY paths)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.errors import BusyError
from magicite.mcp import app as app_mod
from magicite.storage import lease as lease_mod

pytestmark = pytest.mark.acceptance

PROTON = "proton-ge-proton-downgrade"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


# ── AC-025 ────────────────────────────────────────────────────────────────


def test_single_writer_enforced(cfg, db_conn, embedder) -> None:
    """AC-025: GIVEN a Dream run already holding the writer lease WHEN a
    second Dream run attempts to start THEN the second attempt SHALL
    return busy without writing any durable state.

    Exercised against the real ``CrossProcessLease`` (flock + the
    ``writer_lease`` DB row, spec §4.2) -- not a mock -- so this test
    genuinely fails if the guard is weakened or removed: it asserts both
    the raised error type AND that zero rows/files changed, not merely
    "an exception happened.\""""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    holder = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path, conn=db_conn, holder="dream-run-already-in-progress"
    )
    holder.try_acquire()

    engram_dir = cfg.registry_dir
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in engram_dir.glob("*.egr.md")}
    run_rows_before = db_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]

    try:
        with pytest.raises(BusyError):
            dream_mod.run(cfg, db_conn, trigger="manual")
    finally:
        holder.release()

    mtimes_after = {p.name: p.stat().st_mtime_ns for p in engram_dir.glob("*.egr.md")}
    run_rows_after = db_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]

    assert mtimes_after == mtimes_before, "no .egr.md file may be touched by the denied second run"
    assert run_rows_after == run_rows_before, "no consolidation_run row may be written by the denied run"

    # And once released, a real Dream run proceeds normally -- proves the
    # earlier failure was the lease, not some unrelated breakage.
    result = dream_mod.run(cfg, db_conn, trigger="manual")
    assert result.state == "succeeded"


# ── AC-032 ────────────────────────────────────────────────────────────────


def test_session_end_debounce(cfg, embedder) -> None:
    """AC-032: GIVEN two session_end calls arriving inside the
    dream.min_interval_s window WHEN the second call is handled THEN the
    server SHALL return the already-enqueued dream_run_id rather than
    enqueuing a second run.

    Exercised through the full MCP dispatcher (``mcp.app.dispatch_call``),
    since the real enqueue write happens there (session_end() itself is
    G1-hot-path-only and only ever previews the decision -- see
    ``mcp/app.py::_apply_session_end_enqueue``'s docstring)."""
    assert cfg.dream_on_session_end is True
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")

        first = app_mod.dispatch_call(state, "session_end", {"session_id": "s1"})
        assert first.is_error is False
        first_payload = first.structured_content
        assert first_payload["enqueued"] is True
        assert first_payload["dream_run_id"] is not None

        second = app_mod.dispatch_call(state, "session_end", {"session_id": "s2"})
        assert second.is_error is False
        second_payload = second.structured_content
        assert second_payload["enqueued"] is False
        assert second_payload["dream_run_id"] == first_payload["dream_run_id"]

        run_count = state.writer_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]
        assert run_count == 1, "the second call must not enqueue a second run"
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_session_end_does_not_enqueue_when_disabled(cfg, embedder) -> None:
    cfg.dream_on_session_end = False
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        result = app_mod.dispatch_call(state, "session_end", {"session_id": "s1"})
        payload = result.structured_content
        assert payload["enqueued"] is False
        run_count = state.writer_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"]
        assert run_count == 0
    finally:
        state.conn.close()
        state.writer_conn.close()


# ── AC-033 ────────────────────────────────────────────────────────────────


def test_decay_floor_archives_never_deletes(cfg, db_conn, embedder) -> None:
    """AC-033: GIVEN an engram whose effective storage strength has decayed
    below floor_archived THEN the next Dream run SHALL move its file into
    .magicite/archive/ without deleting it.

    Exercised through the full ``core.dream.run()`` orchestrator (not just
    ``core.decay.archive_below_floor`` in isolation, which
    ``tests/unit/core/test_decay.py`` already covers) -- proving the
    archival actually fires as part of a real Dream cycle."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    original_path = cfg.project_root / db_conn.execute(
        "SELECT path FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["path"]
    assert original_path.is_file()

    # Simulate "has decayed below the floor" -- real, accumulated evidence
    # (>=3 outcomes, per core/decay.py's eligibility gate) AND a genuine
    # prior peak >= floor_archived (M5 data-integrity fix: evidence alone
    # does not prove the engram ever *had* standing to decay away from)
    # whose S has since fallen under floor_archived (0.2).
    db_conn.execute(
        "UPDATE engram SET storage_strength = 0.05, peak_storage_strength = 0.6, "
        "success_count = 3, last_applied = ? WHERE id = ?",
        (_iso_now(), engram_id),
    )

    result = dream_mod.run(cfg, db_conn, trigger="manual")

    assert PROTON in result.archived_engrams
    assert not original_path.exists()

    archive_dir = cfg.archive_dir
    archived_files = list(archive_dir.glob(f"*-{PROTON}.egr.md"))
    assert len(archived_files) == 1, "the file must exist in .magicite/archive/ -- never deleted"
    assert "status: archived" in archived_files[0].read_text(encoding="utf-8")

    row = db_conn.execute("SELECT status FROM engram WHERE id = ?", (engram_id,)).fetchone()
    assert row["status"] == "archived"

    # M5 data-integrity fix, strengthening AC-033: a plain re-sync() must
    # not delete the archived row just because its (correct) path now
    # points outside the scanned registry_dir -- "archive, never delete"
    # has to hold at the index level, not just for the file on disk.
    sync_outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert PROTON not in sync_outcome.removed
    row_after_sync = db_conn.execute(
        "SELECT status FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()
    assert row_after_sync is not None, "sync() must not delete an archived engram's row"
    assert row_after_sync["status"] == "archived"
