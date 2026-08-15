"""``storage/authorizer.py``: the G1 DENY matrix per table x statement type
(spec §6.2, AC-013)."""

from __future__ import annotations

import sqlite3

import pytest

from magicite.storage import authorizer as authorizer_mod

#: A minimal, schema-valid ``engram`` row -- shared by every test here that
#: needs to attempt (and, depending on the connection, either be denied or
#: succeed at) a real durable-table write.
_MINIMAL_ENGRAM_INSERT = """
INSERT INTO engram (
  id, name, path, spec_version, version, origin, verification_status, status,
  intent_does, intent_use_when, s_decayed_at, created_at, updated_at,
  identity_sha256, content_sha256, body_sha256, file_mtime_ns
) VALUES (
  'egr_x', 'n', 'p', 'engram/0.2', 1, 'authored', 'pending', 'draft',
  'd', 'u', 't', 't', 't', 'i', 'c', 'b', 1
)
"""


@pytest.fixture
def ephemeral_conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "skill-graph.db"
    # migrate=True here (test-local convenience): production always migrates
    # via the writer connection first (mcp/app.py::build_state).
    conn = authorizer_mod.ephemeral_connection(db_path, migrate=True)
    yield conn
    conn.close()


@pytest.mark.parametrize(
    "table", ["engram", "edge", "consolidation_run", "writer_lease", "approval", "schema_meta"]
)
def test_insert_denied_on_non_eph_tables(ephemeral_conn: sqlite3.Connection, table: str) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute(f"INSERT INTO {table} DEFAULT VALUES")


def test_insert_denied_on_engram_with_real_columns(ephemeral_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute(_MINIMAL_ENGRAM_INSERT)
    row = ephemeral_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()
    assert row["n"] == 0


def test_update_denied_on_non_eph_table(ephemeral_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute("UPDATE schema_meta SET value = 'x' WHERE key = 'y'")


def test_delete_denied_on_non_eph_table(ephemeral_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute("DELETE FROM engram")


def test_drop_table_denied_on_non_eph_table(ephemeral_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute("DROP TABLE engram")


@pytest.mark.parametrize(
    "table",
    [
        "eph_session",
        "eph_bookkeeping",
        "eph_retrieval",
        "eph_tag",
        "eph_candidate_edge",
        "eph_embedding",
        "eph_event",
        "eph_idempotency",
    ],
)
def test_writes_allowed_on_every_eph_table(ephemeral_conn: sqlite3.Connection, table: str) -> None:
    """Belt-and-suspenders over AC-013's own DENY case: the guard must not
    accidentally over-block Tier C too (spec: "the Dream/writer path is not
    strangled by it" -- and neither is the hot path's own eph_ scratch
    space)."""
    if table == "eph_session":
        ephemeral_conn.execute(
            "INSERT INTO eph_session (session_id, started_at, last_seen_at) VALUES ('s','t','t')"
        )
    elif table == "eph_bookkeeping":
        ephemeral_conn.execute("INSERT INTO eph_bookkeeping (engram_id) VALUES ('e1')")
    elif table == "eph_retrieval":
        ephemeral_conn.execute(
            "INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES ('e1', 0.1, 't')"
        )
    elif table == "eph_tag":
        ephemeral_conn.execute(
            "INSERT INTO eph_tag (session_id, subject_kind, engram_id, signal_tier, set_at, expires_at) "
            "VALUES ('s', 'node', 'e1', 1, 't', 't')"
        )
    elif table == "eph_candidate_edge":
        ephemeral_conn.execute(
            "INSERT INTO eph_candidate_edge (src_id, dst_id, type, first_observed, last_updated) "
            "VALUES ('a', 'b', 'co_activation', 't', 't')"
        )
    elif table == "eph_embedding":
        ephemeral_conn.execute(
            "INSERT INTO eph_embedding (engram_id, model, dim, vec, source_sha256, created_at) "
            "VALUES ('e1', 'm', 1, x'00', 's', 't')"
        )
    elif table == "eph_event":
        ephemeral_conn.execute(
            "INSERT INTO eph_event (ts, tool, payload_json) VALUES ('t', 'route', '{}')"
        )
    elif table == "eph_idempotency":
        ephemeral_conn.execute(
            "INSERT INTO eph_idempotency "
            "(request_id, tool, args_sha256, response_json, created_at, expires_at) "
            "VALUES ('r1', 'route', 'h', '{}', 't', 't')"
        )

    row = ephemeral_conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    assert row["n"] == 1


def test_reads_are_never_guarded(ephemeral_conn: sqlite3.Connection) -> None:
    """SELECT is not in the guarded action set at all: reads against durable
    tables must pass on the ephemeral connection (core/session.py's debounce
    read, core/router.py's whole query path)."""
    rows = ephemeral_conn.execute("SELECT * FROM engram").fetchall()
    assert rows == []


def test_writer_connection_has_no_authorizer_and_can_write_durable_tables(tmp_path) -> None:
    db_path = tmp_path / "skill-graph.db"
    conn = authorizer_mod.writer_connection(db_path)
    try:
        conn.execute(_MINIMAL_ENGRAM_INSERT)
        row = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()
        assert row["n"] == 1
    finally:
        conn.close()


def test_ephemeral_and_writer_connections_share_the_same_database(tmp_path) -> None:
    """Two handles onto one file, not two databases (spec §2.1: WAL mode
    concurrent readers + writer)."""
    db_path = tmp_path / "skill-graph.db"
    writer_conn = authorizer_mod.writer_connection(db_path)
    eph_conn = authorizer_mod.ephemeral_connection(db_path, migrate=False)
    try:
        eph_conn.execute(
            "INSERT INTO eph_session (session_id, started_at, last_seen_at) VALUES ('s','t','t')"
        )
        row = writer_conn.execute(
            "SELECT session_id FROM eph_session WHERE session_id = 's'"
        ).fetchone()
        assert row is not None
    finally:
        writer_conn.close()
        eph_conn.close()
