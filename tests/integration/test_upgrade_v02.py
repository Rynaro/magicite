from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import migration as migration_mod
from magicite.core import registry as registry_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.errors import InvalidInputError
from magicite.storage import db as db_mod
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION


def test_v02_database_upgrades_without_losing_durable_rows(tmp_path: Path) -> None:
    path = tmp_path / "v02.db"
    conn = db_mod.connect(path, migrate=False)
    try:
        migration_001 = (Path(db_mod.__file__).resolve().parent / "migrations" / "001_init.sql").read_text(
            encoding="utf-8"
        )
        conn.executescript(f"BEGIN IMMEDIATE;\n{migration_001}\nPRAGMA user_version = 1;\nCOMMIT;\n")
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
        conn.execute("INSERT INTO eph_event (ts, tool, payload_json) VALUES (?, 'route', '{}')", (now,))
        conn.execute(
            "INSERT INTO approval "
            "(id, op, target_name, payload_json, state, proposed_by, proposed_at) "
            "VALUES ('a1', 'promote', 'one', '{}', 'proposed', 'test', ?)",
            (now,),
        )

        assert db_mod.run_migrations(conn) == MAX_KNOWN_SCHEMA_VERSION
        conn.close()
        conn = db_mod.connect(path)

        assert [row["name"] for row in conn.execute("SELECT name FROM engram ORDER BY name")] == [
            "one",
            "two",
        ]
        assert conn.execute("SELECT dst_name FROM edge").fetchone()[0] == "two"
        assert conn.execute("SELECT tool FROM eph_event").fetchone()[0] == "route"
        assert conn.execute("SELECT id FROM approval").fetchone()[0] == "a1"
        assert db_mod.schema_version(conn) == MAX_KNOWN_SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "migration_operation" in tables
        assert "index_generation" in tables
        assert "index_active_pointer" in tables
    finally:
        conn.close()


def test_v1_restore_matches_backup_manifest(cfg: Config) -> None:
    """AC-S03-04: downgrade restoration matches the supported backup manifest."""
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        registry_mod.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    finally:
        conn.close()

    before = {path.name: path.read_bytes() for path in sorted(cfg.registry_dir.glob("*.egr.md"))}
    result = migration_mod.apply(cfg, operation_id="mig_restore_demo")
    assert result.state == "completed"
    assert result.backup_relpath is not None

    op_dir = cfg.data_dir / "migrations" / "mig_restore_demo"
    restored = migration_mod.restore(cfg, backup_path=op_dir)
    assert restored.state == "completed"
    assert restored.kind == "restore_backup"

    after = {path.name: path.read_bytes() for path in sorted(cfg.registry_dir.glob("*.egr.md"))}
    assert after == before

    manifest = json.loads((op_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if not entry["path"].startswith("engrams/"):
            continue
        name = Path(entry["path"]).name
        assert hashlib.sha256(after[name]).hexdigest() == entry["sha256"]

    st = migration_mod.status(cfg, "mig_restore_demo")
    assert st.state == "restored"


def test_future_backup_manifest_rejected(cfg: Config, tmp_path: Path) -> None:
    """AC-S03-04: future/unsupported backup manifests fail closed."""
    cfg.ensure_dirs()
    backup = tmp_path / "future-backup"
    (backup / "backup" / "engrams").mkdir(parents=True)
    manifest = {
        "manifest_kind": "migration_backup/99",
        "schema_version": 99,
        "operation_id": "mig_future",
        "files": [],
        "storage_schema_version": MAX_KNOWN_SCHEMA_VERSION + 5,
    }
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="unsupported backup manifest|newer"):
        migration_mod.restore(cfg, backup_path=backup)
