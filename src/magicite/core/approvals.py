"""docs/06 approval state machine (spec §5.2): ``proposed -> approved ->
executed -> succeeded|failed``, plus ``rejected``.

R3 tools (``sharpen``/``promote``/``archive``/``consolidate``'s siblings)
create *proposals* here; ``mcp/bind_lifecycle.py`` is the writer executor
that consumes them -- this module never performs the underlying engram
mutation itself (it only tracks governance state), matching spec §5.2's
"R3 tools create proposals; the writer executor consumes them."

**Two homes for one row, on purpose (spec §1 data layout, §5.2 "durable
outside the rebuildable DB").** ``approval`` is an *operational* table
(spec §2.4: alongside ``writer_lease``/``consolidation_run`` -- never
Tier A/B learned state, never part of the rebuild-invariant projection
``storage.queries.durable_projection`` diffs). Deleting ``skill-graph.db``
and calling ``sync()`` therefore does NOT preserve pending approvals
through the DB alone -- exactly the gap spec closes by also mirroring
every row to ``.spectra/approvals/<id>.json``: :func:`reload_from_mirror`
(called from ``core.registry.sync()``) re-populates the ``approval`` table
from those files, so a deleted-and-rebuilt DB loses nothing durable. The
JSON file is written **before** the DB row on every transition (same
"file wins" ordering ``core/registry.py``'s SKILL.md path already uses)
so a crash between the two never leaves the DB claiming state the file
does not also record.

**No G2/G3 lease needed for the approval row itself** -- same precedent
``core/dream.py::enqueue()`` already established for ``consolidation_run``
(another §2.4 operational table): exclusion from the hot path comes from
running on the writer connection (``mcp/app.py``'s ``_WRITER_CONNECTION_
TOOLS`` already lists ``sharpen``/``promote``/``archive``), not from
holding ``storage.lease.writer_lease()``. The *underlying engram mutation*
an approved proposal executes (``storage.durable.set_status()``,
``storage.durable.mark_archived()``, ``engram.writer.atomic_write()``) is
Tier A/B and still goes through the lease exactly as before -- callers in
``mcp/bind_lifecycle.py`` wrap only that inner step in ``storage.lease.
writer_lease()``.

**Autonomous mode (``MAGICITE_AUTONOMOUS=1`` / ``cfg.autonomous``,
docs/06 §Autonomous Mode).** :func:`decide` records ``decided_by=
"autonomous-mode"`` when the caller passes that sentinel -- the audit
trail never loses the fact that no human looked. Autonomous mode is never
this module's own decision: callers (``mcp/bind_lifecycle.py``) check
``cfg.autonomous`` and choose whether to immediately call :func:`decide`
themselves; a review-mode proposal simply stays ``proposed`` until a
*separate* actor calls :func:`decide` (there is no standing "approve"
tool in the frozen 16-tool surface -- v1's review workflow is
operator-external, per docs/operations.md).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magicite.config import Config

AUTONOMOUS_ACTOR = "autonomous-mode"

_VALID_OPS: frozenset[str] = frozenset({"sharpen", "promote", "archive", "nucleate"})
_VALID_STATES: frozenset[str] = frozenset(
    {"proposed", "approved", "rejected", "executed", "succeeded", "failed"}
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return f"appr_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    op: str
    target_name: str
    payload: dict[str, Any]
    state: str
    proposed_by: str
    proposed_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    executed_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "target_name": self.target_name,
            "payload": self.payload,
            "state": self.state,
            "proposed_by": self.proposed_by,
            "proposed_at": self.proposed_at,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "executed_run_id": self.executed_run_id,
        }


def _row_to_record(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        op=row["op"],
        target_name=row["target_name"],
        payload=json.loads(row["payload_json"]),
        state=row["state"],
        proposed_by=row["proposed_by"],
        proposed_at=row["proposed_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        reason=row["reason"],
        executed_run_id=row["executed_run_id"],
    )


def _mirror_path(cfg: Config, approval_id: str) -> Path:
    return cfg.approvals_dir / f"{approval_id}.json"


def _write_mirror(cfg: Config, record: ApprovalRecord) -> None:
    """Atomic write of the JSON sidecar -- deliberately independent of
    ``engram.writer.atomic_write`` (which asserts the G2 lease): approvals
    are operational state, not Tier A/B, so they follow ``core/dream.py``'s
    "no lease needed" precedent for this table's DB row too (see module
    docstring). The tmp+fsync+replace shape is still worth keeping for
    crash-safety even though the *gate* does not apply."""
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    path = _mirror_path(cfg, record.id)
    tmp_path = path.with_name(path.name + ".tmp")
    content = json.dumps(record.to_dict(), indent=2, sort_keys=True, default=str)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, path)


def _upsert_row(conn: sqlite3.Connection, record: ApprovalRecord) -> None:
    conn.execute(
        """
        INSERT INTO approval (
          id, op, target_name, payload_json, state, proposed_by, proposed_at,
          decided_by, decided_at, reason, executed_run_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          op=excluded.op, target_name=excluded.target_name, payload_json=excluded.payload_json,
          state=excluded.state, proposed_by=excluded.proposed_by, proposed_at=excluded.proposed_at,
          decided_by=excluded.decided_by, decided_at=excluded.decided_at, reason=excluded.reason,
          executed_run_id=excluded.executed_run_id
        """,
        (
            record.id,
            record.op,
            record.target_name,
            json.dumps(record.payload, default=str),
            record.state,
            record.proposed_by,
            record.proposed_at,
            record.decided_by,
            record.decided_at,
            record.reason,
            record.executed_run_id,
        ),
    )


def _persist(cfg: Config, conn: sqlite3.Connection, record: ApprovalRecord) -> ApprovalRecord:
    """File wins first (durable outside the DB), then the DB cache row."""
    _write_mirror(cfg, record)
    _upsert_row(conn, record)
    return record


def propose(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    op: str,
    target_name: str,
    payload: dict[str, Any],
    proposed_by: str,
) -> ApprovalRecord:
    """Create a new ``proposed`` approval (spec §5.2). ``payload`` carries
    whatever the executor needs to replay the operation later (e.g. the
    ``proposed_changes`` a ``sharpen()`` call named, or the evidence
    snapshot a ``promote()`` guard evaluated)."""
    if op not in _VALID_OPS:
        raise ValueError(f"unknown approval op {op!r}, expected one of {sorted(_VALID_OPS)}")
    record = ApprovalRecord(
        id=new_id(),
        op=op,
        target_name=target_name,
        payload=payload,
        state="proposed",
        proposed_by=proposed_by,
        proposed_at=_now(),
    )
    return _persist(cfg, conn, record)


def get(conn: sqlite3.Connection, approval_id: str) -> ApprovalRecord | None:
    row = conn.execute("SELECT * FROM approval WHERE id = ?", (approval_id,)).fetchone()
    return _row_to_record(row) if row is not None else None


#: Legal ``state -> {allowed next states}`` (docs/06 §Approval State Machine:
#: "Proposed -> [review] -> Approved -> Executed -> Succeeded" / "Rejected"
#: / "Appealed -> Re-review" -- v1 does not implement the appeal loop, spec
#: names it only for the general mcp20 machine, not a Magicite tool).
_LEGAL_NEXT: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "rejected"}),
    "approved": frozenset({"executed"}),
    "rejected": frozenset(),
    "executed": frozenset({"succeeded", "failed"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
}


def _transition(
    cfg: Config, conn: sqlite3.Connection, approval_id: str, *, to_state: str, **fields: Any
) -> ApprovalRecord:
    current = get(conn, approval_id)
    if current is None:
        raise ValueError(f"no approval {approval_id!r}")
    if to_state not in _LEGAL_NEXT.get(current.state, frozenset()):
        raise ValueError(f"approval {approval_id!r}: {current.state!r} -> {to_state!r} is not legal")
    updated = ApprovalRecord(
        id=current.id,
        op=current.op,
        target_name=current.target_name,
        payload=current.payload,
        state=to_state,
        proposed_by=current.proposed_by,
        proposed_at=current.proposed_at,
        decided_by=fields.get("decided_by", current.decided_by),
        decided_at=fields.get("decided_at", current.decided_at),
        reason=fields.get("reason", current.reason),
        executed_run_id=fields.get("executed_run_id", current.executed_run_id),
    )
    return _persist(cfg, conn, updated)


def decide(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    approval_id: str,
    approve: bool,
    decided_by: str,
    reason: str | None = None,
) -> ApprovalRecord:
    """``proposed -> approved`` or ``proposed -> rejected``. ``decided_by=
    autonomous-mode`` (see :data:`AUTONOMOUS_ACTOR`) is a normal actor
    value here, not a special code path -- the audit trail records it the
    same way it would a human reviewer's identity."""
    return _transition(
        cfg, conn, approval_id, to_state="approved" if approve else "rejected", decided_by=decided_by,
        decided_at=_now(), reason=reason,
    )


def mark_executed(conn: sqlite3.Connection, cfg: Config, *, approval_id: str) -> ApprovalRecord:
    return _transition(cfg, conn, approval_id, to_state="executed")


def mark_outcome(
    conn: sqlite3.Connection, cfg: Config, *, approval_id: str, succeeded: bool, reason: str | None = None
) -> ApprovalRecord:
    return _transition(
        cfg, conn, approval_id, to_state="succeeded" if succeeded else "failed", reason=reason
    )


def reload_from_mirror(cfg: Config, conn: sqlite3.Connection) -> int:
    """spec §5.2: "each row is mirrored to .spectra/approvals/<id>.json and
    reloaded on sync()". Called from ``core.registry.sync()`` so that a
    deleted-and-rebuilt ``skill-graph.db`` (AC-009's own scenario) recovers
    every pending/decided approval from its durable JSON sidecar, not just
    engram/edge state. Returns the number of files reloaded."""
    if not cfg.approvals_dir.is_dir():
        return 0
    count = 0
    for path in sorted(cfg.approvals_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("state") not in _VALID_STATES or data.get("op") not in _VALID_OPS:
            continue
        record = ApprovalRecord(
            id=data["id"],
            op=data["op"],
            target_name=data["target_name"],
            payload=data.get("payload") or {},
            state=data["state"],
            proposed_by=data["proposed_by"],
            proposed_at=data["proposed_at"],
            decided_by=data.get("decided_by"),
            decided_at=data.get("decided_at"),
            reason=data.get("reason"),
            executed_run_id=data.get("executed_run_id"),
        )
        _upsert_row(conn, record)
        count += 1
    return count
