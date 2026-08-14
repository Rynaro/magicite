from __future__ import annotations

from pathlib import Path

from magicite.storage import db


def test_connect_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "skill-graph.db"
    conn = db.connect(path)
    try:
        assert path.exists()
        assert db.schema_version(conn) == 1
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for expected in ("engram", "edge", "eph_session", "eph_event", "writer_lease"):
            assert expected in tables

        applied_again = db.run_migrations(conn)
        assert applied_again == 1
    finally:
        conn.close()


def test_pragmas_applied(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
