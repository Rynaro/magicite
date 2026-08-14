"""``route()`` — reduced for M0 (cosine seed + declared-edge plan expansion).

The full v1 algorithm (spec §3.3: PPR activation, inhibition, context
conditioning, hub penalty, community rerank) lands in M2 alongside
``core/activation.py``/``core/communities.py``/``core/composition.py``.
This module is the M0 "walking skeleton" reduction the story explicitly
calls for: embed the query, cosine-rank the routable candidates, and
expand the winner's *declared* ``needs``/``composes`` edges into a
topologically-ordered ``composition_plan``.

AC-024 (statically enforced by ``tests/unit/test_p0_enforcement.py``):
this module MUST NOT import ``magicite.storage.durable`` or
``magicite.engram.writer`` — ``route()`` is a hot-path, read-mostly tool
(the only durable write it ever performs is Tier-C bookkeeping via plain
``eph_*`` tables, spec §3.3 step 11).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from magicite.config import Config
from magicite.embeddings.base import Embedder
from magicite.engram.model import ROUTABLE_STATUSES

INTENT_TRUNCATE = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Candidate:
    rank: int
    id: str
    name: str
    intent_does: str
    intent_use_when: str
    score: float
    status: str
    exposure_count: int
    body_ref: str
    signal_tier_0: bool = True


@dataclass
class RouteOutcome:
    candidates: list[Candidate]
    composition_plan: list[str]
    plan_confidence: float
    instructions: str
    session_id: str
    registry_size: int
    unresolved_context: list[str] = field(default_factory=list)


#: docs/05 verbatim self-report instruction text (Tier-1 signal path).
ROUTE_INSTRUCTIONS = (
    "After applying a skill, call signal_use(skill_id) with its id. When the task "
    "outcome is known (tests pass, user confirms), call signal_outcome(valence, "
    "skill_ids) to drive learning."
)


def _upsert_session(conn: sqlite3.Connection, session_id: str) -> None:
    now = _now()
    row = conn.execute(
        "SELECT session_id FROM eph_session WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO eph_session (session_id, started_at, last_seen_at) VALUES (?,?,?)",
            (session_id, now, now),
        )
    else:
        conn.execute(
            "UPDATE eph_session SET last_seen_at = ? WHERE session_id = ?", (now, session_id)
        )


def _fetch_seeds(conn: sqlite3.Connection, model_name: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in ROUTABLE_STATUSES)
    rows = conn.execute(
        f"""
        SELECT e.id, e.name, e.intent_does, e.intent_use_when, e.status, e.exposure_count,
               e.path, x.vec, x.dim
        FROM engram e
        JOIN eph_embedding x ON x.engram_id = e.id AND x.model = ?
        WHERE e.status IN ({placeholders}) AND e.verification_status = 'verified'
        """,
        (model_name, *ROUTABLE_STATUSES),
    ).fetchall()
    return rows


def _expand_composition_plan(
    conn: sqlite3.Connection, winner_id: str, winner_name: str, *, max_depth: int = 5, max_size: int = 8
) -> list[str]:
    """Topological closure over declared ``depends_on``/``composes`` edges."""
    order: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str, node_name: str, depth: int) -> None:
        if node_name in visited or depth > max_depth or len(order) >= max_size:
            return
        visited.add(node_name)
        edges = conn.execute(
            """
            SELECT dst_name, dst_id FROM edge
            WHERE src_id = ? AND type IN ('depends_on', 'composes') AND dangling = 0
            ORDER BY dst_name
            """,
            (node_id,),
        ).fetchall()
        for e in edges:
            if e["dst_id"] is not None:
                visit(e["dst_id"], e["dst_name"], depth + 1)
        if node_name not in order:
            order.append(node_name)

    visit(winner_id, winner_name, 0)
    return order[:max_size]


def _plan_confidence(conn: sqlite3.Connection, winner_id: str, plan: list[str]) -> float:
    if len(plan) <= 1:
        return 1.0
    rows = conn.execute(
        "SELECT storage_strength FROM edge WHERE src_id = ? AND type IN ('depends_on', 'composes')",
        (winner_id,),
    ).fetchall()
    declared = len(rows)
    if declared == 0:
        return 1.0
    resolved = sum(1 for name in plan if name != plan[-1])
    mean_strength = sum(r["storage_strength"] for r in rows) / declared
    return round(mean_strength * (resolved / declared), 4)


def route(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    query: str,
    context: dict | None = None,
    k: int = 5,
    session_id: str | None = None,
) -> RouteOutcome:
    sid = session_id or str(uuid.uuid4())
    _upsert_session(conn, sid)

    qvec = embedder.embed(query)
    rows = _fetch_seeds(conn, embedder.model_name)

    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        vec = np.frombuffer(row["vec"], dtype=np.float32)
        score = float(np.dot(qvec, vec))
        scored.append((score, row))
    scored.sort(key=lambda t: t[0], reverse=True)

    top = scored[:k]
    candidates = [
        Candidate(
            rank=i + 1,
            id=row["id"],
            name=row["name"],
            intent_does=row["intent_does"][:INTENT_TRUNCATE],
            intent_use_when=row["intent_use_when"][:INTENT_TRUNCATE],
            score=round(score, 6),
            status=row["status"],
            exposure_count=row["exposure_count"],
            body_ref=row["path"],
        )
        for i, (score, row) in enumerate(top)
    ]

    composition_plan: list[str] = []
    plan_confidence = 0.0
    now = _now()
    for c in candidates:
        conn.execute(
            """
            INSERT INTO eph_bookkeeping (engram_id, exposure_delta, last_activated, route_returns)
            VALUES (?, 1, ?, 1)
            ON CONFLICT(engram_id) DO UPDATE SET
              exposure_delta = eph_bookkeeping.exposure_delta + 1,
              last_activated = excluded.last_activated,
              route_returns = eph_bookkeeping.route_returns + 1
            """,
            (c.id, now),
        )
    conn.execute(
        "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, payload_json) "
        "VALUES (?,?,?,0,?,?)",
        (
            now,
            sid,
            "route",
            candidates[0].id if candidates else None,
            json.dumps({"query": query, "k": k, "candidate_ids": [c.id for c in candidates]}),
        ),
    )

    if candidates:
        winner = candidates[0]
        composition_plan = _expand_composition_plan(conn, winner.id, winner.name)
        plan_confidence = _plan_confidence(conn, winner.id, composition_plan)

    # M0's reduced router does not implement context conditioning (spec §3.3
    # step 7b lands in M2); every supplied context string is honestly
    # reported as unresolved rather than silently accepted.
    unresolved_context: list[str] = []
    if context:
        if context.get("project_tag"):
            unresolved_context.append(context["project_tag"])
        unresolved_context.extend(context.get("recent_failures") or [])
        unresolved_context.extend(context.get("user_prefs") or [])

    registry_size = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    return RouteOutcome(
        candidates=candidates,
        composition_plan=composition_plan,
        plan_confidence=plan_confidence,
        instructions=ROUTE_INSTRUCTIONS,
        session_id=sid,
        registry_size=registry_size,
        unresolved_context=unresolved_context,
    )
