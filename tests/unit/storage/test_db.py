from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from magicite.storage import db
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION


def test_connect_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "skill-graph.db"
    conn = db.connect(path)
    try:
        assert path.exists()
        assert db.schema_version(conn) == MAX_KNOWN_SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for expected in (
            "engram",
            "edge",
            "eph_session",
            "eph_event",
            "writer_lease",
            "migration_operation",
            "index_generation",
        ):
            assert expected in tables

        applied_again = db.run_migrations(conn)
        assert applied_again == MAX_KNOWN_SCHEMA_VERSION
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
    """M5 security fix #3: a ``PRAGMA user_version=0`` previously desynced
    ``run_migrations`` bookkeeping from the actual schema. Every statement in
    ``001_init.sql`` is ``IF NOT EXISTS``, and S03's materialized-check covers
    migration 3, so re-running against a fully-populated schema is a no-op.
    """
    path = tmp_path / "skill-graph.db"
    conn = db.connect(path)
    try:
        assert db.schema_version(conn) == MAX_KNOWN_SCHEMA_VERSION
        conn.execute("PRAGMA user_version = 0")
        assert db.schema_version(conn) == 0

        applied = db.run_migrations(conn)
        assert applied == MAX_KNOWN_SCHEMA_VERSION
        assert db.schema_version(conn) == MAX_KNOWN_SCHEMA_VERSION

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for expected in (
            "engram",
            "edge",
            "eph_session",
            "eph_event",
            "writer_lease",
            "approval",
            "migration_operation",
        ):
            assert expected in tables
    finally:
        conn.close()


def test_failed_migration_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migration = tmp_path / "001_broken.sql"
    migration.write_text(
        "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY);\nINSERT INTO missing_table VALUES (1);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "_discover_migrations", lambda: [(1, migration)])
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.run_migrations(conn)
        assert db.schema_version(conn) == 0
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "should_rollback" not in tables
    finally:
        conn.close()
