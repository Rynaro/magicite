"""``core/session.py``: the spec §3.3 session resolution rule
(mint/reuse/expire) and ``session_end`` (spec §3.3 tool 7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from magicite.core import session as session_mod


def test_resolve_mints_a_fresh_uuid_when_omitted(cfg, db_conn) -> None:
    resolution = session_mod.resolve(cfg, db_conn, None)
    assert resolution.minted is True
    assert resolution.resumed_after_expiry is False
    row = db_conn.execute(
        "SELECT session_id FROM eph_session WHERE session_id = ?", (resolution.session_id,)
    ).fetchone()
    assert row is not None


def test_resolve_reuses_an_explicit_session_id(cfg, db_conn) -> None:
    first = session_mod.resolve(cfg, db_conn, "my-session")
    second = session_mod.resolve(cfg, db_conn, "my-session")
    assert first.session_id == "my-session"
    assert second.session_id == "my-session"
    assert second.minted is False
    assert second.resumed_after_expiry is False


def test_resolve_flags_and_records_resumption_after_expiry(cfg, db_conn) -> None:
    session_mod.resolve(cfg, db_conn, "old-session")
    stale = (datetime.now(UTC) - timedelta(hours=cfg.session_ttl_hours) - timedelta(minutes=1)).isoformat()
    db_conn.execute(
        "UPDATE eph_session SET last_seen_at = ? WHERE session_id = ?", (stale, "old-session")
    )

    resolution = session_mod.resolve(cfg, db_conn, "old-session")
    assert resolution.resumed_after_expiry is True

    events = db_conn.execute(
        "SELECT payload_json FROM eph_event WHERE session_id = ? AND tool = 'session'",
        ("old-session",),
    ).fetchall()
    assert any(session_mod.SESSION_RESUMED_EVENT in e["payload_json"] for e in events)


def test_resolve_does_not_flag_a_still_live_session(cfg, db_conn) -> None:
    session_mod.resolve(cfg, db_conn, "fresh-session")
    resolution = session_mod.resolve(cfg, db_conn, "fresh-session")
    assert resolution.resumed_after_expiry is False


def test_session_end_closes_and_expires_tags(cfg, db_conn) -> None:
    sid = "s1"
    session_mod.resolve(cfg, db_conn, sid)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    db_conn.execute(
        "INSERT INTO eph_tag (session_id, subject_kind, engram_id, signal_tier, set_at, expires_at) "
        "VALUES (?, 'node', 'e1', 1, ?, ?)",
        (sid, datetime.now(UTC).isoformat(), future),
    )

    outcome = session_mod.session_end(cfg, db_conn, session_id=sid, reason="done")

    assert outcome.closed is True
    assert outcome.tags_expired == 1
    row = db_conn.execute("SELECT ended_at FROM eph_session WHERE session_id = ?", (sid,)).fetchone()
    assert row["ended_at"] is not None
    tag_row = db_conn.execute(
        "SELECT expires_at FROM eph_tag WHERE session_id = ? AND engram_id = 'e1'", (sid,)
    ).fetchone()
    assert tag_row["expires_at"] <= datetime.now(UTC).isoformat()


def test_session_end_on_unknown_session_reports_not_closed(cfg, db_conn) -> None:
    outcome = session_mod.session_end(cfg, db_conn, session_id="never-existed")
    assert outcome.closed is False
    assert outcome.tags_expired == 0


def test_session_end_never_writes_a_consolidation_run_row_from_the_hot_path(cfg, db_conn) -> None:
    """Documented deviation (see core/session.py's docstring + the mission
    FINAL RESPONSE): session_end() computes the enqueue *decision* from
    reads only; it never performs the enqueue *write* itself, since
    consolidation_run is not eph_-prefixed and G1 would deny it on the
    authorizer-restricted connection session_end actually runs on in
    production. This test locks that behaviour in at the core layer."""
    sid = "s1"
    session_mod.resolve(cfg, db_conn, sid)
    outcome = session_mod.session_end(cfg, db_conn, session_id=sid)
    assert outcome.enqueued is False
    assert db_conn.execute("SELECT COUNT(*) AS n FROM consolidation_run").fetchone()["n"] == 0


def test_session_end_reports_an_already_queued_run_without_enqueuing_a_second(cfg, db_conn) -> None:
    """AC-032's spirit (full debounce lands with Dream, M4): if a run is
    already queued/running, session_end() reports it rather than pretending
    none exists -- purely from a read, no write performed here."""
    db_conn.execute(
        "INSERT INTO consolidation_run (id, trigger, state) VALUES ('c1', 'manual', 'queued')"
    )
    sid = "s1"
    session_mod.resolve(cfg, db_conn, sid)
    outcome = session_mod.session_end(cfg, db_conn, session_id=sid)
    assert outcome.dream_run_id == "c1"
    assert outcome.enqueued is False
