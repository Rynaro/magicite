from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from magicite.storage import db


def test_connect_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "skill-graph.db"
    conn = db.connect(path)
    try:
        assert path.exists()
        assert db.schema_version(conn) == 2
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for expected in ("engram", "edge", "eph_session", "eph_event", "writer_lease"):
            assert expected in tables

        applied_again = db.run_migrations(conn)
        assert applied_again == 2
    finally:
        conn.close()


def test_pragmas_applied(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_migrations_are_idempotent_even_after_user_version_is_reset(tmp_path: Path) -> None:
    """M5 security fix #3: a ``PRAGMA user_version=0`` (denied on the
    hot-path connection since M5, but this proves the *other* half of the
    fix independently of that guard) previously desynced ``run_migrations``'s
    bookkeeping from the actual schema -- the next call would see
    ``current=0`` and re-run ``CREATE TABLE``/``CREATE INDEX`` against an
    already-populated schema, raising ``OperationalError: table ... already
    exists`` and permanently bricking boot. Every statement in
    ``001_init.sql`` is now ``IF NOT EXISTS``, so re-running it against a
    fully-populated schema is a clean no-op regardless of how
    ``user_version`` got desynced."""
    path = tmp_path / "skill-graph.db"
    conn = db.connect(path)
    try:
        assert db.schema_version(conn) == 2
        conn.execute("PRAGMA user_version = 0")
        assert db.schema_version(conn) == 0

        # Previously: sqlite3.OperationalError: table engram already exists.
        applied = db.run_migrations(conn)
        assert applied == 2
        assert db.schema_version(conn) == 2

        # And the schema is still fully intact -- not half-recreated.
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for expected in ("engram", "edge", "eph_session", "eph_event", "writer_lease", "approval"):
            assert expected in tables
    finally:
        conn.close()


def test_failed_migration_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migration = tmp_path / "001_broken.sql"
    migration.write_text(
        "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table VALUES (1);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "_discover_migrations", lambda: [(1, migration)])
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.run_migrations(conn)
        assert db.schema_version(conn) == 0
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'should_rollback'"
        ).fetchone() is None
    finally:
        conn.close()
