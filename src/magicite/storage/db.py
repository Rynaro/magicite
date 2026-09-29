"""SQLite connection factory + migration runner (spec §2.1, §2.2-2.4).

M0 note: the full v1 design splits the hot path into an
authorizer-restricted ``ephemeral_connection()`` and a lease-gated
``writer_connection()`` (spec §2.1, §6.2 G1/G2) — that split lands in M1
(``storage/durable.py``) and M3 (``storage/authorizer.py``). Until then,
:func:`connect` is the single connection factory the M0 walking skeleton
uses directly; nothing here contradicts the later split, it is simply not
built yet.

S03: refuses to mutate a DB whose ``user_version`` exceeds the shipped
maximum known to this build (C8 fail-closed on newer unreadable schema).
Migration number allocation lives in ``storage.migrations.registry``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from magicite.errors import InvalidInputError
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def _discover_migrations() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for path in _migrations_dir().glob("*.sql"):
        stem = path.name.split("_", 1)[0]
        if stem.isdigit():
            out.append((int(stem), path))
    return sorted(out, key=lambda t: t[0])


def apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(str(row[1]) == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _migration_already_materialized(conn: sqlite3.Connection, number: int) -> bool:
    """Recover from user_version loss without replaying non-idempotent ALTERs."""
    if number == 2:
        return _has_column(conn, "writer_lease", "fencing_token") and _has_column(
            conn, "eph_idempotency", "state"
        )
    if number == 3:
        return (
            _has_table(conn, "migration_operation")
            and _has_table(conn, "migration_journal_step")
            and _has_table(conn, "index_generation")
            and _has_table(conn, "index_active_pointer")
        )
    return False


def assert_schema_supported(conn: sqlite3.Connection) -> int:
    """Fail closed when ``user_version`` exceeds this build's shipped max (C8)."""
    current = schema_version(conn)
    if current > MAX_KNOWN_SCHEMA_VERSION:
        raise InvalidInputError(
            f"database schema version {current} is newer than this build "
            f"supports (max={MAX_KNOWN_SCHEMA_VERSION})",
            hint="upgrade Magicite before opening this registry, or restore a "
            "matching pre-upgrade backup via core.migration.restore",
            details={
                "schema_version": current,
                "max_known_schema_version": MAX_KNOWN_SCHEMA_VERSION,
            },
        )
    return current


def run_migrations(conn: sqlite3.Connection) -> int:
    """Apply every migration whose number exceeds ``PRAGMA user_version``.

    Idempotent: re-running against an up-to-date DB is a no-op. Returns the
    resulting schema version. Refuses databases from a newer Magicite build.
    """
    assert_schema_supported(conn)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = current
    for number, path in _discover_migrations():
        if number <= current:
            continue
        if _migration_already_materialized(conn, number):
            conn.execute(f"PRAGMA user_version = {number}")
            applied = number
            continue
        script = path.read_text(encoding="utf-8")
        # sqlite3.Connection.executescript() commits any pending transaction
        # before running its input. Put the migration body and version bump
        # in the *same* script transaction so a failing statement cannot
        # leave a partially-upgraded schema with an old user_version.
        transactional_script = f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;\n"
        try:
            conn.executescript(transactional_script)
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        applied = number
    return applied


def connect(db_path: str | Path, *, migrate: bool = True) -> sqlite3.Connection:
    """Open (creating if absent) the skill-graph DB and apply PRAGMAs.

    Row access is by name (``sqlite3.Row``) throughout the codebase.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    if migrate:
        run_migrations(conn)
    else:
        # Still refuse newer-than-known schemas on read-only opens so callers
        # cannot silently operate against an unreadable layout.
        if db_path.is_file() and db_path.stat().st_size > 0:
            assert_schema_supported(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])
