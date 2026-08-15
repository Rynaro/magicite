"""``eph_*`` CRUD — the hot path (spec §1 layout, §2.3 DDL).

Every table this module writes is prefixed ``eph_``: the set G1's SQLite
authorizer (M3) will allow hot-path connections to mutate. Nothing here
asserts the writer lease (G2) — that guard exists precisely to protect
the *non*-``eph_`` tables (``storage/durable.py``); requiring a lease to
bump a Tier-C counter would defeat the point of Tier C being the
hot-path's own, unarbitrated scratch space (spec Approach commitment 2).

``core/router.py`` (route()'s Tier-C bookkeeping, spec §3.3 step 11) and
``core/registry.py`` (register()/sync()'s ``eph_embedding`` writes, spec
§2.6 step 7) both call into this module rather than issuing raw SQL
inline, so there is exactly one place that knows the ``eph_*`` schema
shapes.
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
