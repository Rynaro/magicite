"""``storage/ephemeral.py``: M4 input-hygiene hardening -- the R refractory
window (``bump_retrieval``), the non-suppressive ``expire_session_tags``
fix, and the bounded retroactive-credit helper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from magicite.core import registry as registry_mod
from magicite.storage import ephemeral as ephemeral_mod

PROTON = "proton-ge-proton-downgrade"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── bump_retrieval: refractory window (temporal rate limit, not identity) ──


def test_bump_retrieval_first_call_always_bumps(db_conn) -> None:
    new_r, bumped = ephemeral_mod.bump_retrieval(db_conn, "egr_x", eta_r=0.15, refractory_s=30.0)
    assert bumped is True
    assert new_r == 0.15


def test_bump_retrieval_within_refractory_window_is_a_noop() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE eph_retrieval (engram_id TEXT PRIMARY KEY, r REAL NOT NULL, r_decayed_at TEXT NOT NULL)"
    )
    r1, bumped1 = ephemeral_mod.bump_retrieval(conn, "egr_x", eta_r=0.15, refractory_s=30.0)
    assert bumped1 is True
    # A second call an instant later (well inside the 30s window) must not
    # bump again -- "R must count occasions, not calls" (a buggy or
    # adversarial caller cannot inflate R by looping signal_use()).
    r2, bumped2 = ephemeral_mod.bump_retrieval(conn, "egr_x", eta_r=0.15, refractory_s=30.0)
    assert bumped2 is False
    assert r2 == r1


def test_bump_retrieval_after_refractory_window_bumps_again() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE eph_retrieval (engram_id TEXT PRIMARY KEY, r REAL NOT NULL, r_decayed_at TEXT NOT NULL)"
    )
    ephemeral_mod.bump_retrieval(conn, "egr_x", eta_r=0.15, refractory_s=30.0)
    # Move the anchor into the past (simulating real elapsed time) --
    # directly mutating r_decayed_at is the standard way these tests
    # simulate wall-clock passage without sleeping.
    past = _iso(datetime.now(UTC) - timedelta(seconds=60))
    conn.execute("UPDATE eph_retrieval SET r_decayed_at = ? WHERE engram_id = ?", (past, "egr_x"))
    r2, bumped2 = ephemeral_mod.bump_retrieval(conn, "egr_x", eta_r=0.15, refractory_s=30.0)
    assert bumped2 is True
    assert r2 > 0.15


def test_bump_retrieval_refractory_disabled_by_default_zero() -> None:
    """refractory_s=0.0 (the function's own default) preserves M3's
    original behaviour exactly -- every call bumps."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE eph_retrieval (engram_id TEXT PRIMARY KEY, r REAL NOT NULL, r_decayed_at TEXT NOT NULL)"
    )
    ephemeral_mod.bump_retrieval(conn, "egr_x", eta_r=0.15)
    r2, bumped2 = ephemeral_mod.bump_retrieval(conn, "egr_x", eta_r=0.15)
    assert bumped2 is True
    assert r2 > 0.15


def test_bump_retrieval_windowing_is_keyed_on_engram_not_session() -> None:
    """The refractory window is keyed on engram_id (something a caller
    cannot forge), not session_id (caller-minted, spec §3.3) -- rotating
    session ids does not defeat it. This is asserted at the storage layer
    directly (bump_retrieval never even takes a session_id parameter)."""
    import inspect

    sig = inspect.signature(ephemeral_mod.bump_retrieval)
    assert "session_id" not in sig.parameters


# ── expire_session_tags: must not suppress already-captured signals ────


def test_expire_session_tags_does_not_touch_captured_tags(cfg, db_conn, embedder) -> None:
    """M4 hardening: closing a session must not suppress capture
    eligibility (as read by live_tagged_engram_ids/live_tagged_edge_keys)
    for signals already captured -- expire_session_tags only pulls forward
    the expiry of NOT-yet-captured tags."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]

    now = _iso(datetime.now(UTC))
    future = _iso(datetime.now(UTC) + timedelta(hours=2))
    tag_id = ephemeral_mod.insert_node_tag(
        db_conn, session_id="sX", engram_id=engram_id, signal_tier=1, set_at=now, expires_at=future
    )
    ephemeral_mod.capture_node_tag(
        db_conn, tag_id, captured_at=now, valence=0.9, salience=0.8, capture_weight=0.7
    )
    before_expiry = db_conn.execute("SELECT expires_at FROM eph_tag WHERE id = ?", (tag_id,)).fetchone()[
        "expires_at"
    ]
    assert before_expiry == future

    changed = ephemeral_mod.expire_session_tags(db_conn, session_id="sX", now=now)
    assert changed == 0  # nothing eligible to expire -- the only tag is already captured

    after_expiry = db_conn.execute("SELECT expires_at FROM eph_tag WHERE id = ?", (tag_id,)).fetchone()[
        "expires_at"
    ]
    assert after_expiry == future, "a captured tag's expires_at must survive session_end untouched"


def test_expire_session_tags_still_expires_uncaptured_tags(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    now = _iso(datetime.now(UTC))
    future = _iso(datetime.now(UTC) + timedelta(hours=2))
    ephemeral_mod.insert_node_tag(
        db_conn, session_id="sY", engram_id=engram_id, signal_tier=1, set_at=now, expires_at=future
    )
    # grace_s defaults to 0.0 here -- this test documents expire_session_
    # tags' bare primitive behaviour (no grace floor applied), unrelated
    # to the M6 grace-floor hardening below.
    changed = ephemeral_mod.expire_session_tags(db_conn, session_id="sY", now=now)
    assert changed == 1


# ── M6 hardening: expire_session_tags' grace floor (carried-forward
#    defect #1, "session-suppression hijack") ──────────────────────────


def test_expire_session_tags_grace_floor_protects_a_fresh_tag(cfg, db_conn, embedder) -> None:
    """A tag younger than ``grace_s`` (measured from its immutable
    ``set_at``) must not have its expiry pulled forward -- this is the
    primitive the still-live suppression exploit (a stranger's, or a
    race with the owner's own, ``session_end(<id>)`` call landing between
    ``signal_use()`` and ``signal_outcome()``) is bounded by."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    now_dt = datetime.now(UTC)
    now = _iso(now_dt)
    future = _iso(now_dt + timedelta(hours=2))
    ephemeral_mod.insert_node_tag(
        db_conn, session_id="sZ", engram_id=engram_id, signal_tier=1, set_at=now, expires_at=future
    )

    changed = ephemeral_mod.expire_session_tags(db_conn, session_id="sZ", now=now, grace_s=60.0)

    assert changed == 0, "a tag set 'now' must survive a 60s grace floor"
    row = db_conn.execute(
        "SELECT expires_at FROM eph_tag WHERE session_id = 'sZ' AND engram_id = ?", (engram_id,)
    ).fetchone()
    assert row["expires_at"] == future


def test_expire_session_tags_grace_floor_still_expires_a_stale_tag(cfg, db_conn, embedder) -> None:
    """The floor bounds the effect, it does not disable session_end() for
    tags that really are old enough -- the "genuinely stale, uncaptured
    tag" case must still expire, or session_end() would stop doing its
    spec-named job at all."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    now_dt = datetime.now(UTC)
    now = _iso(now_dt)
    stale_set_at = _iso(now_dt - timedelta(seconds=90))
    future = _iso(now_dt + timedelta(hours=2))
    ephemeral_mod.insert_node_tag(
        db_conn, session_id="sZ", engram_id=engram_id, signal_tier=1, set_at=stale_set_at, expires_at=future
    )

    changed = ephemeral_mod.expire_session_tags(db_conn, session_id="sZ", now=now, grace_s=60.0)

    assert changed == 1, "a tag already older than the grace floor must still expire"


def test_expire_session_tags_grace_floor_mutation_check(cfg, db_conn, embedder) -> None:
    """Mutation check: ``grace_s=0.0`` (the default, and what M4 shipped)
    must reproduce the exploitable behaviour exactly -- the identical
    fresh tag from the "protects a fresh tag" test above IS suppressed
    when the floor is disabled, proving that test's green result actually
    depends on the floor being > 0 and not on some other, coincidental
    reason."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    now = _iso(datetime.now(UTC))
    future = _iso(datetime.now(UTC) + timedelta(hours=2))
    ephemeral_mod.insert_node_tag(
        db_conn, session_id="sZ", engram_id=engram_id, signal_tier=1, set_at=now, expires_at=future
    )

    changed = ephemeral_mod.expire_session_tags(db_conn, session_id="sZ", now=now, grace_s=0.0)

    assert changed == 1, "grace_s=0.0 must reproduce the pre-M6 suppression of a fresh tag"


# ── retroactive credit: bounded, most-recent-first ──────────────────────


def test_live_tagged_engram_ids_recent_orders_by_recency_and_caps(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    rows = db_conn.execute("SELECT id FROM engram ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    assert len(ids) >= 3
    now = datetime.now(UTC)
    future = _iso(now + timedelta(hours=1))
    # tag them in a known order, each with an increasing set_at.
    for i, engram_id in enumerate(ids):
        set_at = _iso(now + timedelta(seconds=i))
        ephemeral_mod.insert_node_tag(
            db_conn, session_id="sZ", engram_id=engram_id, signal_tier=1, set_at=set_at, expires_at=future
        )
    recent = ephemeral_mod.live_tagged_engram_ids_recent(
        db_conn, session_id="sZ", now=_iso(now), limit=2
    )
    assert recent == [ids[-1], ids[-2]]  # most-recently-tagged first, capped to 2
