from __future__ import annotations

from pathlib import Path

from magicite.storage import db as db_mod


def test_v02_database_upgrades_without_losing_durable_rows(tmp_path: Path) -> None:
    path = tmp_path / "v02.db"
    conn = db_mod.connect(path, migrate=False)
    try:
        migration_001 = (
            Path(db_mod.__file__).resolve().parent / "migrations" / "001_init.sql"
        ).read_text(encoding="utf-8")
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            f"{migration_001}\n"
            "PRAGMA user_version = 1;\n"
            "COMMIT;\n"
        )
        engram_insert = """
            INSERT INTO engram
              (id, name, path, spec_version, origin, verification_status, status,
               intent_does, intent_use_when, s_decayed_at, identity_sha256,
               content_sha256, body_sha256, file_mtime_ns, created_at, updated_at)
            VALUES (?, ?, ?, 'engram/0.2', 'authored', 'verified', 'nascent',
                    'does', 'when', ?, 'identity', 'content', 'body', 1, ?, ?)
        """
        now = "2026-08-18T00:00:00+00:00"
        conn.execute(engram_insert, ("e1", "one", "one.egr.md", now, now, now))
        conn.execute(engram_insert, ("e2", "two", "two.egr.md", now, now, now))
        conn.execute(
            "INSERT INTO edge "
            "(src_id, dst_name, dst_id, type, s_decayed_at, provenance, first_observed) "
            "VALUES ('e1', 'two', 'e2', 'depends_on', ?, 'declared', ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO eph_event (ts, tool, payload_json) VALUES (?, 'route', '{}')", (now,)
        )
        conn.execute(
            "INSERT INTO approval "
            "(id, op, target_name, payload_json, state, proposed_by, proposed_at) "
            "VALUES ('a1', 'promote', 'one', '{}', 'proposed', 'test', ?)",
            (now,),
        )

        assert db_mod.run_migrations(conn) == 2
        conn.close()
        conn = db_mod.connect(path)

        assert [
            row["name"] for row in conn.execute("SELECT name FROM engram ORDER BY name")
        ] == ["one", "two"]
        assert conn.execute("SELECT dst_name FROM edge").fetchone()[0] == "two"
        assert conn.execute("SELECT tool FROM eph_event").fetchone()[0] == "route"
        assert conn.execute("SELECT id FROM approval").fetchone()[0] == "a1"
        assert db_mod.schema_version(conn) == 2
    finally:
        conn.close()
