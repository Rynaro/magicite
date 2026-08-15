"""Standing KPI computation (docs/07 §KPIs: Standing Observables; spec §1
layout: ``obs/kpi.py``).

M6: wired into ``introspect(include_health=True)``
(``mcp/bind_inspect.py``) -- **read-only, no side effect** (``introspect``
is R0, ``side_effect="none"``, one of G1's seven hot-path-only tools), so
this module only ever *reads* the live DB; it never writes a report file
(that is ``core/audit.py::run_audit``'s job, invoked from Dream's own
audit phase, not from a live tool call) and never mutates ``engram``/
``edge`` rows.

Reuses ``core/audit.py``'s pure query functions (``silent_engrams``,
``hub_candidates``) rather than duplicating their SQL -- both this module
and ``run_audit`` now compute the *same* numbers from the *same* queries;
only ``run_audit`` additionally persists a JSON report.

Ships exactly what spec §7.3's ship/stub table names for docs/07's
"Standing Observables": the silent-engram %, the skill-fitness
distribution, and black-hole hub / traffic-share detection. The docs/07
"Hit@k vs Registry Size" scaling-law KPI is deliberately **not**
implemented here as a live/online metric -- it needs a windowed
route-history log ("for all queries routed in the last N days") that no
milestone's file set names, and the corpus itself defers fine-grained
per-query online telemetry as a standing product KPI (docs/README Known
Gaps; spec Scope "Out"). What v1 *can* answer honestly, right now, without
inventing that infrastructure, is the same signal the KPI's premise rests
on: whether the registry is even past the point where hierarchy-aware
routing is expected to matter at all (R9). That is exactly what
:func:`cold_start_signal` reports, using the same ``route()``-returned
``registry_size`` that risk R9 says must not be buried.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from magicite.config import Config
from magicite.core import audit as audit_mod

#: docs/07 §Cold start / risk R9's ORIGINAL, pre-measurement heuristic:
#: "If the registry stays under ~50 skills, native SKILL.md matching is
#: fine and Magicite is overhead." Named ``..._REFERENCE_SIZE``, not
#: ``..._BREAK_EVEN`` (VIVI, M7 conformance fix): docs/01's Falsification
#: Record (measured 2026-08-15) tested a registry of 70 skills -- ABOVE
#: this number -- and found plain dense-embedding retrieval still beat
#: the full Magicite pipeline (Hit@1 0.5476 vs 0.4619, p=0.00053); the
#: claim that routing "pays off exactly where native routing breaks" is
#: not evidenced at any size tested (13, 40, 70 -- docs/07 §4). The
#: heuristic's own crossing point is separately unevidenced: a single,
#: unreplicated 39-query-slice result that does not hold on the full
#: 120-query set (docs/07's H-SCALE row: "INCONCLUSIVE"). Keeping the
#: number only as a reporting anchor -- never as a resolved break-even a
#: registry can be said to have "crossed" -- is what closes the drift
#: between this module and README.md's "Honest limits" section.
COLD_START_REFERENCE_SIZE = 50

#: docs/07 §Skill Fitness Distribution buckets (S_node ranges) and their
#: target share of the registry.
_FITNESS_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("nascent_or_failed", 0.0, 0.3),   # S < 0.3, target <10%
    ("probation", 0.3, 0.6),           # 0.3 <= S < 0.6, target 10-20%
    ("consolidated", 0.6, 0.85),       # 0.6 <= S < 0.85, target 60-70%
    ("promoted", 0.85, float("inf")),  # S >= 0.85, target 5-10%
)

#: docs/07: "Percentage of traffic routed through top-5 skills. Target:
#: <30% (if >50%, indicates hub concentration problem)."
TOP_N_HUB_TRAFFIC = 5


def cold_start_signal(registry_size: int) -> dict[str, Any]:
    """R9, honestly: ``route()`` already returns ``registry_size``; this
    reports that number and this registry's position relative to the
    ~50-skill heuristic docs/07 originally proposed -- WITHOUT asserting
    that heuristic as a resolved, evidenced break-even (VIVI, M7
    conformance fix). docs/01's Falsification Record measured routing
    quality at 70 skills (above the heuristic) and found dense-embedding
    retrieval still beat the full pipeline (p=0.00053); crossing the
    heuristic is therefore not evidence anything "pays off". Reporting
    the number honestly -- registry size plus its raw position, no
    inferred verdict -- is what this function now does instead."""
    below_reference = registry_size < COLD_START_REFERENCE_SIZE
    note = (
        f"registry_size={registry_size}, below the ~{COLD_START_REFERENCE_SIZE}-skill "
        "heuristic docs/07 originally proposed ('native SKILL.md matching is likely "
        "competitive here'). That heuristic's own crossing point is itself unevidenced "
        "(docs/01, measured 2026-08-15: a single unreplicated crossing on a 39-query "
        "slice that does not hold on the full 120-query set) -- no claim is made about "
        "whether Magicite's routing machinery is worth its overhead at this size."
        if below_reference
        else (
            f"registry_size={registry_size}, at/above the ~{COLD_START_REFERENCE_SIZE}-skill "
            "heuristic -- but crossing it is NOT evidence that hierarchy-aware routing pays "
            "off: at 70 skills (docs/01, measured 2026-08-15) plain dense-embedding retrieval "
            "still beat the full Magicite pipeline (Hit@1 0.5476 vs 0.4619, p=0.00053). This "
            "number is reported honestly; no routing-quality verdict is inferred from it."
        )
    )
    return {
        "registry_size": registry_size,
        "cold_start_reference_size": COLD_START_REFERENCE_SIZE,
        "below_reference_size": below_reference,
        "note": note,
    }


def fitness_distribution(conn: sqlite3.Connection) -> dict[str, Any]:
    """docs/07 §Skill Fitness Distribution: a histogram of ``S_node``
    (``engram.storage_strength``, the raw column -- not decay-adjusted at
    read time, matching ``core/audit.py``'s own reports-only, no-I/O-side
    -effect posture) across every non-archived engram."""
    rows = conn.execute(
        "SELECT storage_strength FROM engram WHERE status != 'archived'"
    ).fetchall()
    total = len(rows)
    buckets: dict[str, int] = {name: 0 for name, _, _ in _FITNESS_BUCKETS}
    for row in rows:
        s = float(row["storage_strength"])
        for name, lo, hi in _FITNESS_BUCKETS:
            if lo <= s < hi:
                buckets[name] += 1
                break
    histogram = {
        name: {
            "count": count,
            "pct": round(count / total, 4) if total else 0.0,
        }
        for name, count in buckets.items()
    }
    target_range_count = buckets["consolidated"] + buckets["promoted"]
    target_range_pct = (target_range_count / total) if total else 0.0
    return {
        "total": total,
        "histogram": histogram,
        # docs/07 §Passing criteria: "Majority in consolidated+promoted
        # range (>65%)."
        "consolidated_or_promoted_pct": round(target_range_pct, 4),
        "meets_majority_target": target_range_pct > 0.65,
    }


def top_n_traffic_share(conn: sqlite3.Connection, *, n: int = TOP_N_HUB_TRAFFIC) -> dict[str, Any]:
    """docs/07 §Black-Hole Hub Detection's *other* named KPI: "Percentage
    of traffic routed through top-5 skills" -- distinct from
    ``core/audit.py::hub_candidates``'s p95-threshold pathology flag
    (AC-030's own criterion), this is the plain top-``n``-by-route-return
    traffic concentration docs/07 tracks as a standing number regardless
    of whether any single engram crosses the p95 bar."""
    rows = conn.execute(
        """
        SELECT COALESCE(b.route_returns, 0) AS returns FROM engram e
        LEFT JOIN eph_bookkeeping b ON b.engram_id = e.id
        WHERE e.status != 'archived'
        ORDER BY returns DESC
        """
    ).fetchall()
    total = sum(int(r["returns"]) for r in rows)
    if total == 0:
        return {"top_n": n, "share": 0.0, "target": 0.30, "exceeds_target": False}
    top_sum = sum(int(r["returns"]) for r in rows[:n])
    share = top_sum / total
    return {
        "top_n": n,
        "share": round(share, 4),
        "target": 0.30,
        "exceeds_target": share > 0.30,
    }


def compute_standing_kpis(cfg: Config, conn: sqlite3.Connection) -> dict[str, Any]:
    """``introspect(include_health=True)``'s payload (spec §3.3 tool 3:
    "include_health -> the docs/07 standing KPIs"). Every sub-computation
    here is a plain ``SELECT`` -- no write, so this is safe to run on the
    authorizer-restricted hot-path connection ``introspect`` actually
    holds (G1)."""
    registry_size = int(conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"])
    silent = audit_mod.silent_engrams(conn)
    hubs, hub_share = audit_mod.hub_candidates(conn, percentile=cfg.hub_penalty_percentile)
    non_archived = int(
        conn.execute("SELECT COUNT(*) AS n FROM engram WHERE status != 'archived'").fetchone()["n"]
    )
    silent_pct = (len(silent) / non_archived) if non_archived else 0.0

    return {
        "registry_size": registry_size,
        "cold_start": cold_start_signal(registry_size),
        "silent_engrams": {
            "count": len(silent),
            "pct": round(silent_pct, 4),
            "target": 0.10,
            # docs/07: "if >20%, indicates routing cues are systematically poor".
            "systematically_poor": silent_pct > 0.20,
        },
        "hub_detection": {
            "candidates": hubs,
            "traffic_share": round(hub_share, 4),
            "target": audit_mod.HUB_TRAFFIC_SHARE_CAP,
            "exceeds_target": hub_share > audit_mod.HUB_TRAFFIC_SHARE_CAP,
        },
        "top5_traffic_share": top_n_traffic_share(conn, n=TOP_N_HUB_TRAFFIC),
        "fitness_distribution": fitness_distribution(conn),
    }
