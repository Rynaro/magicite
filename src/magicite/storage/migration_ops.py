"""Durable helpers for migration journals and index-generation pointers.

All public writers call :func:`magicite.storage.lease.assert_single_writer`
(G2). Domain orchestration lives in ``magicite.core.migration``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from magicite.errors import InvalidInputError
from magicite.storage.lease import assert_single_writer

_ACTIVE_POINTER_ID = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── migration operation journal ───────────────────────────────────────────


def upsert_migration_operation(
    conn: sqlite3.Connection,
    *,
    operation_id: str,
    kind: str,
    state: str,
    source_format: str,
    target_format: str,
    backup_relpath: str | None = None,
    preview_digest: str | None = None,
    manifest_digest: str | None = None,
    error_message: str | None = None,
    completed_at: str | None = None,
) -> None:
    assert_single_writer()
    now = _now()
    existing = conn.execute(
        "SELECT created_at FROM migration_operation WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    created_at = existing["created_at"] if existing else now
    conn.execute(
        """
        INSERT INTO migration_operation (
          operation_id, kind, state, source_format, target_format,
          backup_relpath, preview_digest, manifest_digest,
          created_at, updated_at, completed_at, error_message
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(operation_id) DO UPDATE SET
          kind=excluded.kind,
          state=excluded.state,
          source_format=excluded.source_format,
          target_format=excluded.target_format,
          backup_relpath=COALESCE(excluded.backup_relpath, migration_operation.backup_relpath),
          preview_digest=COALESCE(excluded.preview_digest, migration_operation.preview_digest),
          manifest_digest=COALESCE(excluded.manifest_digest, migration_operation.manifest_digest),
          updated_at=excluded.updated_at,
          completed_at=COALESCE(excluded.completed_at, migration_operation.completed_at),
          error_message=excluded.error_message
        """,
        (
            operation_id,
            kind,
            state,
            source_format,
            target_format,
            backup_relpath,
            preview_digest,
            manifest_digest,
            created_at,
            now,
            completed_at,
            error_message,
        ),
    )


def get_migration_operation(conn: sqlite3.Connection, operation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM migration_operation WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    return dict(row) if row else None


def list_journal_steps(conn: sqlite3.Connection, operation_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM migration_journal_step WHERE operation_id = ? ORDER BY committed_at, step_key",
        (operation_id,),
    ).fetchall()
    return {str(row["step_key"]): dict(row) for row in rows}


def record_journal_step(
    conn: sqlite3.Connection,
    *,
    operation_id: str,
    step_key: str,
    status: str = "done",
    detail: dict[str, Any] | None = None,
) -> None:
    assert_single_writer()
    conn.execute(
        """
        INSERT INTO migration_journal_step (operation_id, step_key, status, committed_at, detail_json)
        VALUES (?,?,?,?,?)
        ON CONFLICT(operation_id, step_key) DO UPDATE SET
          status=excluded.status,
          committed_at=excluded.committed_at,
          detail_json=excluded.detail_json
        """,
        (
            operation_id,
            step_key,
            status,
            _now(),
            json.dumps(detail, sort_keys=True, separators=(",", ":")) if detail else None,
        ),
    )


def update_engram_format_mirror(
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    spec_version: str,
    content_sha256: str,
    body_sha256: str,
    file_mtime_ns: int,
    path: str,
) -> None:
    """Refresh mirror columns after an on-disk format migration (S03).

    Does not invent trust/status changes: verification_status and origin
    remain whatever the pre-migration row held.
    """
    assert_single_writer()
    conn.execute(
        """
        UPDATE engram SET
          spec_version = ?,
          content_sha256 = ?,
          body_sha256 = ?,
          file_mtime_ns = ?,
          path = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (spec_version, content_sha256, body_sha256, file_mtime_ns, path, _now(), engram_id),
    )


# ── index generation pointers (C11) ───────────────────────────────────────


def begin_index_generation(
    conn: sqlite3.Connection,
    *,
    generation_id: str,
    fingerprint: dict[str, Any],
    fingerprint_digest: str,
    schema_version: int,
) -> None:
    assert_single_writer()
    conn.execute(
        """
        INSERT INTO index_generation (
          generation_id, fingerprint_json, fingerprint_digest, state,
          schema_version, created_at, completed_at, published_at, error_message
        ) VALUES (?,?,?,'building',?,?,NULL,NULL,NULL)
        """,
        (
            generation_id,
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")),
            fingerprint_digest,
            schema_version,
            _now(),
        ),
    )


def complete_index_generation(conn: sqlite3.Connection, generation_id: str) -> None:
    assert_single_writer()
    cur = conn.execute(
        """
        UPDATE index_generation
        SET state = 'complete', completed_at = ?
        WHERE generation_id = ? AND state = 'building'
        """,
        (_now(), generation_id),
    )
    if cur.rowcount != 1:
        raise InvalidInputError(
            f"index generation {generation_id!r} is not in building state",
            hint="only a fully built generation can be marked complete",
        )


def fail_index_generation(conn: sqlite3.Connection, generation_id: str, *, error: str) -> None:
    assert_single_writer()
    conn.execute(
        """
        UPDATE index_generation
        SET state = 'failed', error_message = ?, completed_at = ?
        WHERE generation_id = ?
        """,
        (error, _now(), generation_id),
    )


def publish_index_generation(conn: sqlite3.Connection, generation_id: str) -> dict[str, Any]:
    """Atomically swap the active pointer to a complete generation (C11)."""
    assert_single_writer()
    row = conn.execute(
        "SELECT state FROM index_generation WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise InvalidInputError(f"unknown index generation {generation_id!r}")
    if row["state"] != "complete":
        raise InvalidInputError(
            f"refusing to publish generation {generation_id!r} in state {row['state']!r}",
            hint="publish only after validation marks the generation complete",
        )

    pointer = conn.execute(
        "SELECT generation_id, previous_generation_id FROM index_active_pointer WHERE id = ?",
        (_ACTIVE_POINTER_ID,),
    ).fetchone()
    previous = pointer["generation_id"] if pointer else None
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if previous and previous != generation_id:
            conn.execute(
                "UPDATE index_generation SET state = 'superseded' WHERE generation_id = ?",
                (previous,),
            )
        conn.execute(
            """
            UPDATE index_generation
            SET state = 'published', published_at = ?
            WHERE generation_id = ?
            """,
            (now, generation_id),
        )
        conn.execute(
            """
            UPDATE index_active_pointer
            SET generation_id = ?, previous_generation_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (generation_id, previous, now, _ACTIVE_POINTER_ID),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {
        "generation_id": generation_id,
        "previous_generation_id": previous,
        "updated_at": now,
    }


def rollback_index_generation(conn: sqlite3.Connection) -> dict[str, Any]:
    """Re-activate the previous complete generation pointer (C11)."""
    assert_single_writer()
    pointer = conn.execute(
        "SELECT generation_id, previous_generation_id FROM index_active_pointer WHERE id = ?",
        (_ACTIVE_POINTER_ID,),
    ).fetchone()
    if pointer is None or not pointer["previous_generation_id"]:
        raise InvalidInputError(
            "no previous index generation available to roll back to",
            hint="keep the prior complete generation before publishing a new one",
        )
    previous = str(pointer["previous_generation_id"])
    current = pointer["generation_id"]
    prev_row = conn.execute(
        "SELECT state FROM index_generation WHERE generation_id = ?",
        (previous,),
    ).fetchone()
    if prev_row is None:
        raise InvalidInputError(f"previous generation {previous!r} is missing")
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if current:
            conn.execute(
                "UPDATE index_generation SET state = 'superseded' WHERE generation_id = ?",
                (current,),
            )
        conn.execute(
            """
            UPDATE index_generation
            SET state = 'published', published_at = ?
            WHERE generation_id = ?
            """,
            (now, previous),
        )
        conn.execute(
            """
            UPDATE index_active_pointer
            SET generation_id = ?, previous_generation_id = NULL, updated_at = ?
            WHERE id = ?
            """,
            (previous, now, _ACTIVE_POINTER_ID),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {
        "generation_id": previous,
        "rolled_back_from": current,
        "updated_at": now,
    }


def active_index_generation(conn: sqlite3.Connection) -> dict[str, Any] | None:
    pointer = conn.execute(
        "SELECT generation_id, previous_generation_id, updated_at FROM index_active_pointer WHERE id = ?",
        (_ACTIVE_POINTER_ID,),
    ).fetchone()
    if pointer is None or pointer["generation_id"] is None:
        return None
    gen = conn.execute(
        "SELECT * FROM index_generation WHERE generation_id = ?",
        (pointer["generation_id"],),
    ).fetchone()
    if gen is None:
        return None
    out = dict(gen)
    out["previous_generation_id"] = pointer["previous_generation_id"]
    out["pointer_updated_at"] = pointer["updated_at"]
    return out
