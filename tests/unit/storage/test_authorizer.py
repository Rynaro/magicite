"""``storage/authorizer.py``: the G1 DENY matrix per table x statement type
(spec §6.2, AC-013)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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

#: M5 test-quality fix (mutation testing found this the hard way): a valid,
#: constraint-satisfying ``INSERT`` per non-``eph_`` table -- one that
#: would actually **succeed** if the authorizer were removed entirely.
#: ``INSERT INTO <table> DEFAULT VALUES`` (the pre-M5 shape of this test)
#: happens to violate a NOT NULL constraint on every one of these tables
#: regardless of any authorizer, so `pytest.raises(sqlite3.DatabaseError)`
#: passed for the wrong reason on all six: removing the authorizer
#: entirely still passed the old test (``sqlite3.IntegrityError`` is a
#: ``DatabaseError`` subclass), giving G1's actual DENY behaviour zero real
#: coverage from this table. ``edge`` additionally needs a real ``engram``
#: row to reference (``PRAGMA foreign_keys=ON``) or the insert would fail
#: on the FK instead -- seeded via a writer connection in the test itself.
_VALID_INSERTS: dict[str, str] = {
    "engram": _MINIMAL_ENGRAM_INSERT,
    "schema_meta": "INSERT INTO schema_meta (key, value) VALUES ('k', 'v')",
    "consolidation_run": (
        "INSERT INTO consolidation_run (id, trigger, state) VALUES ('c1', 'manual', 'queued')"
    ),
    "writer_lease": (
        "INSERT INTO writer_lease (id, holder, pid, acquired_at, heartbeat_at, expires_at) "
        "VALUES (1, 'h', 1, 't', 't', 't')"
    ),
    "approval": (
        "INSERT INTO approval (id, op, target_name, payload_json, state, proposed_by, proposed_at) "
        "VALUES ('a1', 'archive', 'n', '{}', 'proposed', 'x', 't')"
    ),
    "edge": (
        "INSERT INTO edge (src_id, dst_name, dst_id, type, s_decayed_at, provenance, first_observed) "
        "VALUES ('egr_x', 'n2', NULL, 'composes', 't', 'declared', 't')"
    ),
}


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "skill-graph.db"


@pytest.fixture
def ephemeral_conn(db_path: Path) -> sqlite3.Connection:
    # migrate=True here (test-local convenience): production always migrates
    # via the writer connection first (mcp/app.py::build_state).
    conn = authorizer_mod.ephemeral_connection(db_path, migrate=True)
    yield conn
    conn.close()


@pytest.mark.parametrize(
    "table", ["engram", "edge", "consolidation_run", "writer_lease", "approval", "schema_meta"]
)
def test_insert_denied_on_non_eph_tables(ephemeral_conn: sqlite3.Connection, table: str) -> None:
    """Cheap smoke test: every non-eph_ table denies a bare ``DEFAULT
    VALUES`` insert too (whatever the reason). The real, authorizer-
    specific proof is :func:`test_insert_denied_on_non_eph_tables_with_
    valid_rows` below."""
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute(f"INSERT INTO {table} DEFAULT VALUES")


@pytest.mark.parametrize("table", sorted(_VALID_INSERTS))
def test_insert_denied_on_non_eph_tables_with_valid_rows(
    db_path: Path, ephemeral_conn: sqlite3.Connection, table: str
) -> None:
    if table == "edge":
        writer_conn = authorizer_mod.writer_connection(db_path)
        try:
            writer_conn.execute(_MINIMAL_ENGRAM_INSERT)
        finally:
            writer_conn.close()

    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute(_VALID_INSERTS[table])
    row = ephemeral_conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    assert row["n"] == 0


def test_insert_denied_on_engram_with_real_columns(ephemeral_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute(_MINIMAL_ENGRAM_INSERT)
    row = ephemeral_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()
    assert row["n"] == 0


# ── M5 security fix #3: SQLITE_PRAGMA / SQLITE_ATTACH ──────────────────


def test_pragma_denied_on_ephemeral_connection(ephemeral_conn: sqlite3.Connection) -> None:
    """No tool exposes PRAGMA today (latent, not reachable) -- denying it
    anyway removes the ``PRAGMA user_version=0`` failure mode entirely:
    that statement would otherwise desync the writer connection's
    migration bookkeeping from the actual schema and (pre-``IF NOT
    EXISTS``) permanently brick boot on the next migration run."""
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute("PRAGMA user_version=0")
    # A bare read-form PRAGMA is denied too (spec: "deny SQLITE_PRAGMA...
    # on hot-path connections", no read/write carve-out) -- but nothing in
    # the hot path ever needs one, so this is a pure hardening, not a
    # functional loss.
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute("PRAGMA user_version")


def test_attach_denied_on_ephemeral_connection(ephemeral_conn: sqlite3.Connection, tmp_path: Path) -> None:
    other_db = tmp_path / "other.db"
    with pytest.raises(sqlite3.DatabaseError):
        ephemeral_conn.execute(f"ATTACH DATABASE '{other_db}' AS other")


def test_writer_connection_pragma_and_attach_are_not_denied(tmp_path: Path) -> None:
    """G1 constrains the hot path, not the writer path (module docstring:
    "G1 exists to constrain the hot path, not to strangle the writer/
    Dream path"). No authorizer at all is installed on this connection."""
    conn = authorizer_mod.writer_connection(tmp_path / "skill-graph.db")
    try:
        conn.execute("PRAGMA user_version")
        other_db = tmp_path / "other.db"
        conn.execute(f"ATTACH DATABASE '{other_db}' AS other")
        conn.execute("DETACH DATABASE other")
    finally:
        conn.close()


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
