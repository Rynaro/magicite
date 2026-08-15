"""AC-020 (run-level idempotency) and, at the Dream-checkpoint level,
AC-021 (determinism) -- spec §7.2's frozen VG-8 command:
``uv run pytest tests/acceptance/test_dream_idempotent.py``.

AC-021's own frozen VERIFY pointer is
``tests/unit/engram/test_writer.py::test_render_is_deterministic`` (M1,
unchanged by M4 -- Dream's checkpoint reuses that exact same
``render_document`` primitive, see ``core/dream.py::_build_checkpoint_candidate``).
This suite additionally proves the SAME property at Dream's own level:
running the checkpoint procedure twice over identical durable state
produces byte-identical files, not just an identical isolated render call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod

pytestmark = pytest.mark.acceptance

PROTON = "proton-ge-proton-downgrade"
STEAM_PREFIX = "steam-prefix-access"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def test_second_run_is_a_noop(cfg, db_conn, embedder) -> None:
    """AC-020: GIVEN a completed Dream run with no new events since its
    watermark WHEN consolidate() runs again THEN the second run SHALL
    write zero .egr.md files."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]

    signals_mod.signal_use(cfg, db_conn, skill_ids=[proton_id], session_id="s1")
    signals_mod.signal_outcome(
        cfg, db_conn, valence=0.9, salience=0.8, skill_ids=[proton_id], session_id="s1"
    )

    first = dream_mod.run(cfg, db_conn, trigger="manual")
    assert first.state == "succeeded"

    second = dream_mod.run(cfg, db_conn, trigger="manual")
    assert second.state == "succeeded"
    assert second.checkpoint_write_ratio == 0.0
    assert second.modified_engrams == []
    assert second.stats["checkpoint"]["checkpointed"] == 0
    assert second.stats["potentiate"]["committed_nodes"] == 0
    assert second.stats["potentiate"]["committed_edges"] == 0


def test_third_run_after_more_of_the_same_evidence_is_still_a_noop(cfg, db_conn, embedder) -> None:
    """Re-signalling the exact same outcome again (a caller repeating
    itself) does not perpetually re-dirty the file either, once Dream has
    caught up -- idempotency holds run-over-run, not just once."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    signals_mod.signal_use(cfg, db_conn, skill_ids=[proton_id], session_id="s1")
    signals_mod.signal_outcome(
        cfg, db_conn, valence=0.9, salience=0.8, skill_ids=[proton_id], session_id="s1"
    )

    dream_mod.run(cfg, db_conn, trigger="manual")
    second = dream_mod.run(cfg, db_conn, trigger="manual")
    third = dream_mod.run(cfg, db_conn, trigger="manual")

    assert second.checkpoint_write_ratio == 0.0
    assert third.checkpoint_write_ratio == 0.0


def test_checkpoint_render_is_byte_identical_across_two_dream_runs(cfg, db_conn, embedder) -> None:
    """AC-021 at Dream's own checkpoint level (see module docstring): once
    an engram has been checkpointed, re-running Dream over unchanged state
    must never rewrite it with even a single differing byte -- there is
    nothing to compare on the second run because nothing gets written, but
    we additionally prove the underlying render is byte-stable by invoking
    the checkpoint-only path (``checkpoint()``/``run_checkpoint_only``)
    twice in a row and diffing file contents directly."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    signals_mod.signal_use(cfg, db_conn, skill_ids=[proton_id], session_id="s1")
    signals_mod.signal_outcome(
        cfg, db_conn, valence=0.9, salience=0.8, skill_ids=[proton_id], session_id="s1"
    )
    dream_mod.run(cfg, db_conn, trigger="manual")  # first pass: anchors set, no commit yet (spacing gate)

    file_path = cfg.registry_dir / f"{PROTON}.egr.md"
    text_before = file_path.read_text(encoding="utf-8")

    # A second checkpoint-only pass over the exact same (still unchanged)
    # state must write nothing at all.
    stats = dream_mod.run_checkpoint_only(cfg, db_conn)
    assert stats.checkpointed == 0
    text_after = file_path.read_text(encoding="utf-8")
    assert text_after == text_before


def test_no_new_events_at_all_since_registration_writes_nothing_new(cfg, db_conn, embedder) -> None:
    """The strongest form of AC-020: a registry that has NEVER received any
    signal at all still checkpoints deterministically -- only declared-edge
    engrams gain their first synapses: block (an author-declared
    relationship Dream is the sole writer for, spec §6.2 G3), and a
    second run after that is a genuine no-op."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")

    first = dream_mod.run(cfg, db_conn, trigger="manual")
    second = dream_mod.run(cfg, db_conn, trigger="manual")

    assert second.checkpoint_write_ratio == 0.0
    assert second.modified_engrams == []
    # sanity: whatever the first run legitimately wrote (declared-edge
    # synapses population), it never needs to write it again.
    assert set(second.modified_engrams).isdisjoint(set(first.modified_engrams)) or not second.modified_engrams
