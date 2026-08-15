"""Read models for ``route``/``introspect``/``flag_dead`` (spec §1 layout),
plus the rebuild-invariant helpers M1's acceptance suite diffs (spec §2.6).

M0 note: only the two read models ``introspect()`` needs in the walking
skeleton are implemented here (``registry_summary``, ``skill_detail``).
``flag_dead()``'s dead-candidate query is not implemented in M0 (the tool
body itself raises ``not_implemented``); this module is the correct,
spec-named home for it once M3 wires ``flag_dead`` up.

M1 adds :func:`durable_projection` (the "the ``durable_projection()``
helper the invariant test diffs" the M1 story action plan names) and
:func:`tier_c_table_counts` (AC-010's "freshly rebuilt index" check).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from magicite.core import edge_weight as edge_weight_mod
from magicite.core.decay_math import effective_value


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
    # engram_community is a derived index (spec §2.2: "rebuilt, never
    # checkpointed") -- its most recently computed `algo` is the live,
    # ground-truth answer to "which CommunityDetector is active" (AC-022).
    # A registry that has never run sync() has never populated this table,
    # so "none" here is honest, not a placeholder.
    detector_row = conn.execute(
        "SELECT algo FROM engram_community ORDER BY computed_at DESC LIMIT 1"
    ).fetchone()
    return {
        "counts_by_status": counts_by_status,
        "detector": detector_row["algo"] if detector_row else "none",
        "last_sync": last_sync_row["value"] if last_sync_row else None,
        "last_consolidation": last_consolidation_row["finished_at"] if last_consolidation_row else None,
        "registry_size": registry_size,
        "embedding_model": embedding_model,
        "autonomous_mode": autonomous,
    }


def skill_detail(
    conn: sqlite3.Connection,
    skill_id_or_name: str,
    *,
    declared_edge_strength: float = edge_weight_mod.DEFAULT_DECLARED_EDGE_STRENGTH,
) -> dict[str, Any] | None:
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

    def _edge_dict(e: sqlite3.Row) -> dict[str, Any]:
        # spec §3.3.1 call site 6 / AC-041: additive `effective_strength`
        # field alongside the still-raw, still-learned-only
        # `storage_strength` -- neither tool is added, removed nor
        # re-signatured (AC-003/INV-4 untouched).
        d = dict(e)
        d["effective_strength"] = edge_weight_mod.effective_strength(
            float(e["storage_strength"]), e["provenance"], declared_edge_strength
        )
        return d

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
        "outbound_edges": [_edge_dict(e) for e in outbound],
        "inbound_edges": [_edge_dict(e) for e in inbound],
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


def dead_candidates(
    conn: sqlite3.Connection, *, window_days: int, limit: int, lambda_r_per_day: float
) -> dict[str, Any]:
    """``flag_dead()`` (spec §3.3 tool 4, docs/07 "Silent Engram Report" /
    M6's carried-forward defect #3 -- the last ``not_implemented`` tool
    body in the 16-tool surface).

    "Dead" (docs/07: "stored but never retrieved in last N days") covers
    two cases, both driven by ``eph_bookkeeping.last_activated`` (bumped
    by every ``route()`` return, CR-1's own accumulator -- see
    ``storage.ephemeral.bump_route_bookkeeping``): never routed at all
    (``last_activated IS NULL``), or routed, but not within
    ``window_days``. Archived engrams are excluded (the same convention
    ``core/audit.py``'s silent-engram check already uses -- an archived
    engram not being routed is not a routing-cue problem, it is the
    intended outcome of archival).

    ``retrieval_strength`` is R decayed **at read time** (spec §6.1: "R
    ... Evaluated lazily at read time"), the same
    ``core.decay_math.effective_value`` primitive ``core/router.py``
    already reads with, so a stale, undecayed R never inflates a
    candidate's reported strength.

    Returns ``{"candidates": [...], "silent_pct": float, "total_dead":
    int}`` -- ``silent_pct`` is computed over *every* dead candidate
    before ``limit`` truncates the returned list (docs/07's KPI is "% of
    registry flagged silent", not "% of the page returned").
    """
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    cutoff = now_dt - timedelta(days=window_days)

    rows = conn.execute(
        """
        SELECT e.id, e.name, b.last_activated,
               COALESCE(r.r, 0.0) AS r, r.r_decayed_at
        FROM engram e
        LEFT JOIN eph_bookkeeping b ON b.engram_id = e.id
        LEFT JOIN eph_retrieval r ON r.engram_id = e.id
        WHERE e.status != 'archived'
        ORDER BY e.name
        """
    ).fetchall()

    dead: list[dict[str, Any]] = []
    for row in rows:
        last_activated = row["last_activated"]
        if last_activated is None:
            reason = "never routed"
        elif datetime.fromisoformat(str(last_activated)) < cutoff:
            reason = f"not routed in the last {window_days} day(s) (last: {last_activated})"
        else:
            continue  # routed recently enough -- not a candidate

        r_effective = effective_value(float(row["r"]), row["r_decayed_at"], now, lambda_r_per_day)
        dead.append(
            {
                "id": row["id"],
                "name": row["name"],
                "last_routed": last_activated,
                "retrieval_strength": round(r_effective, 4),
                "reason": reason,
            }
        )

    # Never-routed candidates (last_routed=None) sort first (the worst
    # offenders -- no evidence at all), then oldest-last-routed first,
    # name as the final deterministic tiebreak.
    dead.sort(key=lambda c: (c["last_routed"] is not None, c["last_routed"] or "", c["name"]))

    registry_size = len(rows)
    silent_pct = (len(dead) / registry_size) if registry_size else 0.0

    return {
        "candidates": dead[:limit],
        "silent_pct": round(silent_pct, 4),
        "total_dead": len(dead),
    }


# ── the rebuild invariant (spec §2.6, AC-009/AC-010/AC-018) ────────────


def durable_projection(conn: sqlite3.Connection) -> dict[str, Any]:
    """A canonical, order-independent snapshot of Tier A + Tier B durable
    state (the file-mirrored engram/edge/journal content), for the
    rebuild-invariant test to diff before/after a ``skill-graph.db``
    delete + ``sync()``.

    Deliberately excludes:

    - every Tier-C (``eph_*``) and operational table (``writer_lease``,
      ``consolidation_run``, ``approval``) -- not durable learned state;
    - ``engram_community`` -- spec §2.2 calls it a "derived index;
      rebuilt, never checkpointed", i.e. explicitly *not* part of Tier A/B;
    - row-bookkeeping columns that legitimately differ across two
      independent ``sync()`` runs even when the *file* content is
      unchanged: ``created_at``/``updated_at``/``s_decayed_at`` (real
      wall-clock write timestamps, spec §2.2's "anchor for lazy S decay"),
      ``file_mtime_ns`` (filesystem metadata, not engram state), and
      ``embedding_model``/``embedding_ref``/``embedding_refreshed_at``
      (a Tier-C cache pointer, re-derived on every re-embed). Excluding
      these is what makes "byte-identical projection" an honest,
      achievable assertion about *content* rather than a wall-clock race.

    Includes ``peak_storage_strength`` (M7 conformance fix): now that it
    has a root-level file representation (``engram/model.py``'s
    ``EngramFrontmatter.peak_storage_strength``, checkpointed by Dream
    same as every other Tier A field), it is real durable state and the
    invariant should catch a regression in it exactly like it already
    does for ``storage_strength``.
    """
    engrams = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, name, path, spec_version, version, origin, verification_status, status,
                   intent_does, intent_use_when, intent_not_when,
                   storage_strength, peak_storage_strength, exposure_count, success_count,
                   failure_count, excitability, last_applied, last_checkpoint,
                   has_exec_blocks, identity_sha256, content_sha256, body_sha256
            FROM engram ORDER BY id
            """
        ).fetchall()
    ]
    steps = [
        dict(row)
        for row in conn.execute(
            "SELECT engram_id, step_no, text, ok_count, total_count, fault_class "
            "FROM engram_step ORDER BY engram_id, step_no"
        ).fetchall()
    ]
    triggers = [
        dict(row)
        for row in conn.execute(
            "SELECT engram_id, polarity, ord, text FROM engram_trigger "
            "ORDER BY engram_id, polarity, ord"
        ).fetchall()
    ]
    context = [
        dict(row)
        for row in conn.execute(
            """
            SELECT engram_context.engram_id AS engram_id, context_node.name AS context_name,
                   engram_context.weight AS weight
            FROM engram_context JOIN context_node ON context_node.id = engram_context.context_id
            ORDER BY engram_context.engram_id, context_node.name
            """
        ).fetchall()
    ]
    edges = [
        dict(row)
        for row in conn.execute(
            "SELECT src_id, dst_name, type, storage_strength, evidence_count, provenance, dangling "
            "FROM edge ORDER BY src_id, dst_name, type"
        ).fetchall()
    ]
    journal = [
        dict(row)
        for row in conn.execute(
            "SELECT engram_id, version, ts, author, event, note, signal_tier, base_version "
            "FROM engram_journal ORDER BY engram_id, version, ts, author, event"
        ).fetchall()
    ]

    return {
        "engrams": engrams,
        "steps": steps,
        "triggers": triggers,
        "context": context,
        "edges": edges,
        "journal": journal,
    }


#: AC-010: every Tier-C table (spec §2.3 DDL) except ``eph_embedding``,
#: which is exempted since ``sync()`` step 7 re-embeds on rebuild.
TIER_C_TABLES: tuple[str, ...] = (
    "eph_session",
    "eph_bookkeeping",
    "eph_retrieval",
    "eph_tag",
    "eph_candidate_edge",
    "eph_embedding",
    "eph_event",
    "eph_idempotency",
)


def tier_c_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for every Tier-C table -- AC-010's "freshly rebuilt
    index" check: everything here should be empty after a delete + sync()
    except ``eph_embedding`` (recomputed, not lost)."""
    return {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in TIER_C_TABLES
    }
