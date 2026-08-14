"""Read models for ``route``/``introspect``/``flag_dead`` (spec §1 layout).

M0 note: only the two read models ``introspect()`` needs in the walking
skeleton are implemented here (``registry_summary``, ``skill_detail``).
``flag_dead()``'s dead-candidate query is not implemented in M0 (the tool
body itself raises ``not_implemented``); this module is the correct,
spec-named home for it once M1 wires ``flag_dead`` up.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def registry_summary(conn: sqlite3.Connection, *, embedding_model: str, autonomous: bool) -> dict[str, Any]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM engram GROUP BY status").fetchall()
    counts_by_status = {row["status"]: row["n"] for row in rows}
    registry_size = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]
    last_sync_row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'last_sync'"
    ).fetchone()
    last_consolidation_row = conn.execute(
        "SELECT finished_at FROM consolidation_run WHERE state = 'succeeded' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return {
        "counts_by_status": counts_by_status,
        "detector": "none",  # core/communities.py lands in M2
        "last_sync": last_sync_row["value"] if last_sync_row else None,
        "last_consolidation": last_consolidation_row["finished_at"] if last_consolidation_row else None,
        "registry_size": registry_size,
        "embedding_model": embedding_model,
        "autonomous_mode": autonomous,
    }


def skill_detail(conn: sqlite3.Connection, skill_id_or_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM engram WHERE id = ? OR name = ?", (skill_id_or_name, skill_id_or_name)
    ).fetchone()
    if row is None:
        return None

    outbound = conn.execute(
        "SELECT dst_name AS target, type, storage_strength, provenance, evidence_count "
        "FROM edge WHERE src_id = ? ORDER BY type, dst_name",
        (row["id"],),
    ).fetchall()
    inbound = conn.execute(
        "SELECT e.name AS target, edge.type, edge.storage_strength, edge.provenance, "
        "edge.evidence_count FROM edge JOIN engram e ON e.id = edge.src_id "
        "WHERE edge.dst_id = ? ORDER BY edge.type, e.name",
        (row["id"],),
    ).fetchall()
    journal = conn.execute(
        "SELECT version, ts AS timestamp, author, event, note FROM engram_journal "
        "WHERE engram_id = ? ORDER BY version",
        (row["id"],),
    ).fetchall()
    bookkeeping = conn.execute(
        "SELECT route_returns FROM eph_bookkeeping WHERE engram_id = ?", (row["id"],)
    ).fetchone()
    retrieval = conn.execute(
        "SELECT r FROM eph_retrieval WHERE engram_id = ?", (row["id"],)
    ).fetchone()

    silent = bookkeeping is None or bookkeeping["route_returns"] == 0

    return {
        "skill": {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "storage_strength": row["storage_strength"],
            "exposure_count": row["exposure_count"],
            "outcome": {"success": row["success_count"], "failure": row["failure_count"]},
        },
        "outbound_edges": [dict(e) for e in outbound],
        "inbound_edges": [dict(e) for e in inbound],
        "history": [dict(h) for h in journal],
        "silent_engram_flag": silent,
        "tier_state": {
            "storage_strength": row["storage_strength"],
            "storage_strength_effective_now": row["storage_strength"],
            "retrieval_strength": retrieval["r"] if retrieval else 0.0,
            "live_tags": 0,
            "pending_dw": 0.0,
        },
    }
