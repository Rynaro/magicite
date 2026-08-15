"""Dream Phase 6 -- Audit (spec §4.3 phase table row 6: "Reads: whole graph;
Writes: runtime/audit-<run>.json, flags on engram (silent/hub); Tier
touched: reports only").

Reports only, by spec's own phase table: nothing here mutates ``engram``/
``edge`` rows. ``core/lifecycle.py``/``core/fitness.py``'s evidence gates
are *not* invoked from here to auto-transition status -- that stays M5's
job (approvals machinery); this phase only *measures* (docs/07 standing
KPIs: silent-engram %, black-hole hub share, coverage gaps) and writes the
JSON report ``checkpoint()``/``introspect(consolidation_id=...)`` can later
point at.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from magicite.config import Config

#: docs/07: "Black-hole hub detection ... usage PageRank p95 + traffic-share
#: KPI (<30%)". Not a plasticity/decay tunable (those live in Config); a
#: fixed docs/07 KPI target.
_HUB_TRAFFIC_SHARE_CAP = 0.30


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AuditReport:
    run_id: str
    generated_at: str
    registry_size: int
    silent_engrams: list[str] = field(default_factory=list)
    hub_candidates: list[str] = field(default_factory=list)
    hub_traffic_share: float = 0.0
    coverage_gaps: list[str] = field(default_factory=list)
    orphans_note: str = (
        "not computed: `yields` is not wired to a DB edge type in v1's schema "
        "(storage.durable.EDGE_TYPE_FOR_FIELD covers needs/composes/inhibits only)"
    )
    report_path: str = ""

    @property
    def silent_count(self) -> int:
        return len(self.silent_engrams)

    @property
    def silent_pct(self) -> float:
        return (self.silent_count / self.registry_size) if self.registry_size else 0.0

    def stats(self) -> dict[str, object]:
        return {
            "registry_size": self.registry_size,
            "silent_count": self.silent_count,
            "silent_pct": round(self.silent_pct, 4),
            "hub_candidates": len(self.hub_candidates),
            "hub_traffic_share": round(self.hub_traffic_share, 4),
            "coverage_gaps": len(self.coverage_gaps),
            "report_path": self.report_path,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "registry_size": self.registry_size,
            "silent_engrams": self.silent_engrams,
            "hub_candidates": self.hub_candidates,
            "hub_traffic_share": self.hub_traffic_share,
            "coverage_gaps": self.coverage_gaps,
            "orphans_note": self.orphans_note,
        }


def _silent_engrams(conn: sqlite3.Connection) -> list[str]:
    """docs/07: "stored but never retrieved in last T sessions" -- v1's
    honest cut (matching ``storage.queries.skill_detail``'s existing
    ``silent_engram_flag``): never routed at all, ever, rather than a
    T-windowed recency check (no windowed route-history table exists yet)."""
    rows = conn.execute(
        """
        SELECT e.name AS name FROM engram e
        LEFT JOIN eph_bookkeeping b ON b.engram_id = e.id
        WHERE e.status != 'archived' AND (b.route_returns IS NULL OR b.route_returns = 0)
        ORDER BY e.name
        """
    ).fetchall()
    return [str(r["name"]) for r in rows]


def _hub_candidates(conn: sqlite3.Connection, *, percentile: float) -> tuple[list[str], float]:
    """docs/07 "black-hole hub" heuristic: engrams at/above the
    ``percentile``-th route-traffic mark, flagged only if that set actually
    monopolizes traffic (share >= :data:`_HUB_TRAFFIC_SHARE_CAP`). This is
    an honest, deliberately simple usage-share heuristic over
    ``eph_bookkeeping.route_returns`` -- not a full usage-weighted-edge
    PageRank recomputation (that belongs to ``core/router.py``/
    ``core/activation.py``, out of M4's declared file set; flagged as a
    carry-forward in the M4 report)."""
    rows = conn.execute(
        """
        SELECT e.name AS name, COALESCE(b.route_returns, 0) AS returns FROM engram e
        LEFT JOIN eph_bookkeeping b ON b.engram_id = e.id
        WHERE e.status != 'archived'
        """
    ).fetchall()
    total = sum(int(r["returns"]) for r in rows)
    if total == 0 or not rows:
        return [], 0.0
    sorted_returns = sorted(int(r["returns"]) for r in rows)
    idx = min(len(sorted_returns) - 1, int(len(sorted_returns) * percentile / 100.0))
    threshold = sorted_returns[idx]
    if threshold == 0:
        return [], 0.0
    candidates = sorted(str(r["name"]) for r in rows if int(r["returns"]) >= threshold)
    candidate_traffic = sum(int(r["returns"]) for r in rows if str(r["name"]) in candidates)
    share = candidate_traffic / total
    if share < _HUB_TRAFFIC_SHARE_CAP:
        return [], share
    return candidates, share


def _coverage_gaps(conn: sqlite3.Connection) -> list[str]:
    """docs/03 phase 6: "Coverage gaps: `needs` with no provider engram ->
    nucleation candidates" -- dangling ``depends_on`` edges (spec §2.2:
    ``needs`` renders to ``type='depends_on'``, ``storage.durable.
    EDGE_TYPE_FOR_FIELD``)."""
    rows = conn.execute(
        "SELECT DISTINCT dst_name FROM edge WHERE type = 'depends_on' AND dangling = 1 "
        "ORDER BY dst_name"
    ).fetchall()
    return [str(r["dst_name"]) for r in rows]


def run_audit(cfg: Config, conn: sqlite3.Connection, *, run_id: str) -> AuditReport:
    generated_at = _now()
    registry_size = int(conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"])
    silent = _silent_engrams(conn)
    hub_candidates, hub_share = _hub_candidates(conn, percentile=cfg.hub_penalty_percentile)
    coverage_gaps = _coverage_gaps(conn)

    report = AuditReport(
        run_id=run_id,
        generated_at=generated_at,
        registry_size=registry_size,
        silent_engrams=silent,
        hub_candidates=hub_candidates,
        hub_traffic_share=hub_share,
        coverage_gaps=coverage_gaps,
    )

    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    report_path = cfg.runtime_dir / f"audit-{run_id}.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report.report_path = str(report_path)
    return report
