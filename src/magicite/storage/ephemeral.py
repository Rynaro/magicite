"""``eph_*`` CRUD — the hot path (spec §1 layout, §2.3 DDL).

Every table this module writes is prefixed ``eph_``: the set G1's SQLite
authorizer (``storage/authorizer.py``, M3) allows hot-path connections to
mutate. Nothing here asserts the writer lease (G2) — that guard exists
precisely to protect the *non*-``eph_`` tables (``storage/durable.py``);
requiring a lease to bump a Tier-C counter would defeat the point of Tier C
being the hot-path's own, unarbitrated scratch space (spec Approach
commitment 2).

``core/router.py`` (route()'s Tier-C bookkeeping, spec §3.3 step 11),
``core/registry.py`` (register()/sync()'s ``eph_embedding`` writes, spec
§2.6 step 7), ``core/signals.py`` (tag set/capture/co-activation, spec §3.3
tools 5-6) and ``core/session.py`` (session resolution, spec §3.3's session
rule) all call into this module rather than issuing raw SQL inline, so
there is exactly one place that knows the ``eph_*`` schema shapes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import numpy as np


def _now() -> str:
    return datetime.now(UTC).isoformat()


def upsert_session(conn: sqlite3.Connection, session_id: str) -> None:
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


def bump_route_bookkeeping(conn: sqlite3.Connection, engram_id: str) -> None:
    """CR-1: route()'s exposure/last-activated/route-returns accumulator.

    Folded into the file's ``plasticity.exposure_count`` at Dream's
    checkpoint phase (M4); never touches durable state directly.
    """
    now = _now()
    conn.execute(
        """
        INSERT INTO eph_bookkeeping (engram_id, exposure_delta, last_activated, route_returns)
        VALUES (?, 1, ?, 1)
        ON CONFLICT(engram_id) DO UPDATE SET
          exposure_delta = eph_bookkeeping.exposure_delta + 1,
          last_activated = excluded.last_activated,
          route_returns = eph_bookkeeping.route_returns + 1
        """,
        (engram_id, now),
    )


def append_event(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    tool: str,
    signal_tier: int | None,
    engram_id: str | None,
    payload: dict,
    valence: float | None = None,
    salience: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, valence, salience, payload_json)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (_now(), session_id, tool, signal_tier, engram_id, valence, salience, json.dumps(payload)),
    )


def upsert_embedding(
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    model_name: str,
    dim: int,
    vec: np.ndarray,
    source_sha256: str,
) -> None:
    conn.execute(
        """
        INSERT INTO eph_embedding (engram_id, model, dim, vec, source_sha256, created_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(engram_id, model) DO UPDATE SET
          dim=excluded.dim, vec=excluded.vec, source_sha256=excluded.source_sha256,
          created_at=excluded.created_at
        """,
        (engram_id, model_name, dim, vec.tobytes(), source_sha256, _now()),
    )


# ── session resolution (spec §3.3's "one rule, every tool" + core/session.py) ──


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT session_id, started_at, last_seen_at, ended_at FROM eph_session WHERE session_id = ?",
        (session_id,),
    ).fetchone()


def close_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """spec §3.3 tool 7: ``session_end`` closes the session. Returns ``True``
    iff a live (previously-unclosed) session row existed to close."""
    row = get_session(conn, session_id)
    if row is None:
        return False
    conn.execute(
        "UPDATE eph_session SET ended_at = ?, last_seen_at = ? WHERE session_id = ?",
        (_now(), _now(), session_id),
    )
    return True


# ── synaptic tags (spec §2.3 eph_tag, §3.3 tools 5-6 -- the two-phase commit) ──


def insert_node_tag(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    engram_id: str,
    signal_tier: int,
    set_at: str,
    expires_at: str,
) -> int:
    """spec §3.3 tool 5 step 3: tag ``engram_id`` (phase 1 of the two-phase
    commit, docs/03 §Key Update Rules 1). One row per ``signal_use`` call --
    ``eph_tag`` has no ``(session_id, engram_id)`` uniqueness constraint by
    design (spec §2.2 DDL: ``id INTEGER PRIMARY KEY AUTOINCREMENT``), so
    repeated applications within a session accumulate distinct, individually
    time-stamped tag rows -- exactly what :func:`count_node_tags`'s cap check
    and :func:`live_uncaptured_node_tags`'s per-row recency weighting need.
    """
    cur = conn.execute(
        """
        INSERT INTO eph_tag (session_id, subject_kind, engram_id, signal_tier, set_at, expires_at)
        VALUES (?, 'node', ?, ?, ?, ?)
        """,
        (session_id, engram_id, signal_tier, set_at, expires_at),
    )
    assert cur.lastrowid is not None  # AUTOINCREMENT PK: always set after a successful INSERT
    return cur.lastrowid


def insert_edge_tag(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    edge_src: str,
    edge_dst: str,
    edge_type: str,
    signal_tier: int,
    set_at: str,
    expires_at: str,
) -> int:
    """spec §3.3 tool 5 step 5: "tag the edge" for a co-activation candidate."""
    cur = conn.execute(
        """
        INSERT INTO eph_tag
          (session_id, subject_kind, edge_src, edge_dst, edge_type, signal_tier, set_at, expires_at)
        VALUES (?, 'edge', ?, ?, ?, ?, ?, ?)
        """,
        (session_id, edge_src, edge_dst, edge_type, signal_tier, set_at, expires_at),
    )
    assert cur.lastrowid is not None  # AUTOINCREMENT PK: always set after a successful INSERT
    return cur.lastrowid


def count_node_tags(conn: sqlite3.Connection, *, session_id: str, engram_id: str) -> int:
    """spec §3.3 tool 5 step 6: the per-skill-per-session cap denominator --
    every tag ever set this session for this skill, live or already expired
    (a capped-then-expired skill must not "refill" its cap mid-session)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag WHERE session_id = ? AND subject_kind = 'node' AND engram_id = ?",
        (session_id, engram_id),
    ).fetchone()
    return int(row["n"])


def live_tagged_engram_ids(conn: sqlite3.Connection, *, session_id: str, now: str) -> list[str]:
    """spec §3.3 tool 5 step 5: "every pair of skills with live tags in this
    session" -- the co-activation candidate-generation input."""
    rows = conn.execute(
        "SELECT DISTINCT engram_id FROM eph_tag "
        "WHERE session_id = ? AND subject_kind = 'node' AND expires_at > ?",
        (session_id, now),
    ).fetchall()
    return [str(r["engram_id"]) for r in rows]


def live_uncaptured_node_tags(
    conn: sqlite3.Connection, *, session_id: str, engram_id: str, now: str
) -> list[sqlite3.Row]:
    """spec §3.3 tool 6 step ("find all tags still alive"): the candidate set
    :func:`capture_node_tag` commits against, one row per still-live,
    not-yet-captured application of ``engram_id`` this session."""
    return conn.execute(
        "SELECT id, set_at FROM eph_tag WHERE session_id = ? AND subject_kind = 'node' "
        "AND engram_id = ? AND expires_at > ? AND captured_at IS NULL",
        (session_id, engram_id, now),
    ).fetchall()


def capture_node_tag(
    conn: sqlite3.Connection,
    tag_id: int,
    *,
    captured_at: str,
    valence: float,
    salience: float,
    capture_weight: float,
) -> None:
    """spec §3.3 tool 6: phase 2 of the two-phase commit (docs/03 §Key Update
    Rules 1) -- marks a live tag captured for the next Dream run. Never
    touches ``storage_strength`` itself (that is Dream's phase 2, M4,
    gated through ``core/plasticity.py::apply()``, AC-014)."""
    conn.execute(
        "UPDATE eph_tag SET captured_at = ?, capture_valence = ?, capture_salience = ?, "
        "capture_weight = ? WHERE id = ?",
        (captured_at, valence, salience, capture_weight, tag_id),
    )


def expire_session_tags(conn: sqlite3.Connection, *, session_id: str, now: str) -> int:
    """spec §3.3 tool 7: "expires its tags (retained for the next Dream
    run)" -- pulls every still-live tag's ``expires_at`` forward to ``now``
    rather than deleting rows (deletion of captured-but-unconsumed tags is
    Dream's retention-window job, spec §6.1, not session_end's). Returns the
    number of tags actually pulled forward (already-expired tags are a
    no-op, not double-counted)."""
    cur = conn.execute(
        "UPDATE eph_tag SET expires_at = ? WHERE session_id = ? AND expires_at > ?",
        (now, session_id, now),
    )
    return int(cur.rowcount)


def count_pending_captured_tags(conn: sqlite3.Connection, *, session_id: str) -> int:
    """spec §3.3 tool 7's ``captured_pending``: tags already captured
    (phase 2 done) but not yet folded into a Dream run (``consumed_run_id``
    unset)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag "
        "WHERE session_id = ? AND captured_at IS NOT NULL AND consumed_run_id IS NULL",
        (session_id,),
    ).fetchone()
    return int(row["n"])


# ── candidate co-activation edges (spec §2.3 eph_candidate_edge) ────────────


def upsert_candidate_edge(conn: sqlite3.Connection, *, src_id: str, dst_id: str, edge_type: str) -> None:
    """spec §3.3 tool 5 step 5: sub-threshold Hebbian evidence -- potentiated
    into a real ``edge`` row only by Dream (M4), never here."""
    now = _now()
    conn.execute(
        """
        INSERT INTO eph_candidate_edge (src_id, dst_id, type, evidence_count, first_observed, last_updated)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(src_id, dst_id, type) DO UPDATE SET
          evidence_count = eph_candidate_edge.evidence_count + 1,
          last_updated = excluded.last_updated
        """,
        (src_id, dst_id, edge_type, now, now),
    )


# ── retrieval strength R (spec §2.3 eph_retrieval, §6.1) ────────────────────


def bump_retrieval(conn: sqlite3.Connection, engram_id: str, *, eta_r: float) -> float:
    """spec §3.3 tool 5 step 4: ``R <- min(1, R + eta_R*(1-R))``. Lazy decay
    (``core/decay.py``, M4) is not applied to the *read* here -- R
    accumulates undamped between reads until Dream materialises decay, which
    is an honest simplification (nothing yet consumes a decayed R across a
    real gap) rather than a claim that decay is implemented."""
    row = conn.execute("SELECT r FROM eph_retrieval WHERE engram_id = ?", (engram_id,)).fetchone()
    current_r = float(row["r"]) if row is not None else 0.0
    new_r = min(1.0, current_r + eta_r * (1.0 - current_r))
    now = _now()
    conn.execute(
        """
        INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES (?, ?, ?)
        ON CONFLICT(engram_id) DO UPDATE SET r = excluded.r, r_decayed_at = excluded.r_decayed_at
        """,
        (engram_id, new_r, now),
    )
    return new_r
