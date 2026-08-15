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
from datetime import UTC, datetime, timedelta

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


def get_potentiation_anchor(conn: sqlite3.Connection, engram_id: str) -> str | None:
    """M6 defect fix (write_ratio churn): Dream Phase 2's spacing-effect
    dt anchor for a node, read from Tier C (never the file-mirrored
    ``engram.last_applied``) -- see :func:`set_potentiation_anchor` and
    ``storage/migrations/001_init.sql``'s ``eph_bookkeeping.
    last_dw_input_at`` column comment."""
    row = conn.execute(
        "SELECT last_dw_input_at FROM eph_bookkeeping WHERE engram_id = ?", (engram_id,)
    ).fetchone()
    return str(row["last_dw_input_at"]) if row is not None and row["last_dw_input_at"] is not None else None


def set_potentiation_anchor(conn: sqlite3.Connection, engram_id: str, at: str) -> None:
    """Advance the Tier-C spacing anchor unconditionally -- called for
    *every* processed node tag (committed or not), so a zero-dw
    observation still establishes the timing anchor a later, properly
    spaced observation needs, without ever touching the file-mirrored
    ``engram.last_applied`` column (that one only moves on a real
    commit -- ``core/dream.py``'s job, not this module's)."""
    conn.execute(
        """
        INSERT INTO eph_bookkeeping
          (engram_id, exposure_delta, last_activated, route_returns, last_dw_input_at)
        VALUES (?, 0, NULL, 0, ?)
        ON CONFLICT(engram_id) DO UPDATE SET last_dw_input_at = excluded.last_dw_input_at
        """,
        (engram_id, at),
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


def live_tagged_engram_ids_recent(
    conn: sqlite3.Connection, *, session_id: str, now: str, limit: int
) -> list[str]:
    """M4 hardening: the retroactive-credit analogue of
    :func:`live_tagged_engram_ids`, bounded to the ``limit`` *most
    recently* (re-)tagged engrams (``MAX(set_at) DESC``). spec §3.3 tool 6
    rule 2's "capture(all_tags_alive_in_window)" is otherwise unbounded --
    a caller with a large ``per_skill_session_cap``-respecting but
    wide-fan-out session could retroactively credit an unbounded number of
    skills from a single high-salience outcome call. Capping to the most
    recent ``limit`` loses little real signal: ``signal_outcome()``'s own
    ``capture_weight = salience * exp(-dt/tau_credit)`` already decays
    older tags toward zero."""
    rows = conn.execute(
        "SELECT engram_id, MAX(set_at) AS last_set_at FROM eph_tag "
        "WHERE session_id = ? AND subject_kind = 'node' AND expires_at > ? "
        "GROUP BY engram_id ORDER BY last_set_at DESC LIMIT ?",
        (session_id, now, limit),
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


def expire_session_tags(
    conn: sqlite3.Connection, *, session_id: str, now: str, grace_s: float = 0.0
) -> int:
    """spec §3.3 tool 7: "expires its tags (retained for the next Dream
    run)" -- pulls every still-live, **not-yet-captured** tag's
    ``expires_at`` forward to ``now`` rather than deleting rows (deletion
    of captured-but-unconsumed tags is Dream's retention-window job, spec
    §6.1, not session_end's). Returns the number of tags actually pulled
    forward (already-expired tags are a no-op, not double-counted).

    M4 hardening: ``captured_at IS NULL`` is deliberate -- an already-
    captured tag (``signal_outcome()`` has already recorded its
    valence/weight; it is only waiting for Dream to consume it) has its
    ``expires_at`` left untouched by ``session_end``. A captured tag's
    ``expires_at`` is not read by anything that gates Dream's replay
    (``pending_captured_*_tags`` filter on ``captured_at``/
    ``consumed_run_id``, never ``expires_at``) or by ``signal_outcome()``'s
    own re-capture guard (``live_uncaptured_*_tags`` already excludes
    captured rows) -- but ``live_tagged_engram_ids``/``live_tagged_edge_keys``
    (the co-activation/retroactive-credit "is this tag live" checks) *do*
    key on ``expires_at`` alone, so closing a session must never be able to
    retroactively suppress a signal that has already been captured.

    M6 hardening (carried-forward defect #1, "session-suppression
    hijack"): ``session_id`` is not a capability -- *any* caller that
    names it can call ``session_end`` on it, including a caller that is
    not its owner (spec §3.3 forbids server-side session minting/auth, so
    there is no principal to check here). ``grace_s`` (``cfg.
    session_end_tag_grace_s``) bounds the *effect* instead: a tag is only
    eligible to have its expiry pulled forward once it is already at
    least ``grace_s`` seconds old, measured from its immutable ``set_at``
    (never touched by this function or by anything else after insertion).
    A freshly-set, not-yet-captured tag therefore survives an out-of-band
    ``session_end`` call -- whether it is the owner's own legitimate call
    racing a same-turn ``signal_outcome()``, or a stranger's -- for at
    least ``grace_s`` seconds, closing the realistic
    "``signal_use()`` -> [session_end elsewhere] -> ``signal_outcome()``"
    suppression window. This bounds the blast radius; it does not
    eliminate it (``grace_s=0`` reproduces the exploitable M4 behaviour
    exactly -- see ``tests/unit/storage/test_ephemeral.py``'s mutation
    check).
    """
    if grace_s > 0:
        cutoff = (datetime.fromisoformat(now) - timedelta(seconds=grace_s)).isoformat()
        cur = conn.execute(
            "UPDATE eph_tag SET expires_at = ? WHERE session_id = ? AND expires_at > ? "
            "AND captured_at IS NULL AND set_at <= ?",
            (now, session_id, now, cutoff),
        )
    else:
        cur = conn.execute(
            "UPDATE eph_tag SET expires_at = ? WHERE session_id = ? AND expires_at > ? "
            "AND captured_at IS NULL",
            (now, session_id, now),
        )
    return int(cur.rowcount)


def live_tagged_edge_keys(
    conn: sqlite3.Connection, *, session_id: str, now: str
) -> list[tuple[str, str, str]]:
    """Edge-tag analogue of :func:`live_tagged_engram_ids` (spec §3.3 tool 5
    step 5's edge side): every ``(edge_src, edge_dst, edge_type)`` with a
    live tag in this session."""
    rows = conn.execute(
        "SELECT DISTINCT edge_src, edge_dst, edge_type FROM eph_tag "
        "WHERE session_id = ? AND subject_kind = 'edge' AND expires_at > ?",
        (session_id, now),
    ).fetchall()
    return [(str(r["edge_src"]), str(r["edge_dst"]), str(r["edge_type"])) for r in rows]


def live_uncaptured_edge_tags(
    conn: sqlite3.Connection, *, session_id: str, edge_src: str, edge_dst: str, edge_type: str, now: str
) -> list[sqlite3.Row]:
    """Edge-tag analogue of :func:`live_uncaptured_node_tags` -- the M3
    two-phase-commit machinery built the SET side for edges
    (:func:`insert_edge_tag`) but not the CAPTURE side; this closes that
    gap (M4 carry-forward: "fold captured eph_tag rows into real edge
    ... updates")."""
    return conn.execute(
        "SELECT id, set_at FROM eph_tag WHERE session_id = ? AND subject_kind = 'edge' "
        "AND edge_src = ? AND edge_dst = ? AND edge_type = ? AND expires_at > ? AND captured_at IS NULL",
        (session_id, edge_src, edge_dst, edge_type, now),
    ).fetchall()


def capture_edge_tag(
    conn: sqlite3.Connection,
    tag_id: int,
    *,
    captured_at: str,
    valence: float,
    salience: float,
    capture_weight: float,
) -> None:
    """Edge-tag analogue of :func:`capture_node_tag` -- same columns, same
    two-phase-commit semantics, just for a ``subject_kind='edge'`` row."""
    conn.execute(
        "UPDATE eph_tag SET captured_at = ?, capture_valence = ?, capture_salience = ?, "
        "capture_weight = ? WHERE id = ?",
        (captured_at, valence, salience, capture_weight, tag_id),
    )


def pending_captured_node_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Dream Phase 1 (spec §4.3): every captured, not-yet-consumed node tag,
    across *every* session -- the plasticity input for ``engram.
    storage_strength``. Deliberately reads only ``eph_tag`` (capped by
    ``core/signals.py``'s per-skill-per-session cap at capture time), never
    the uncapped ``eph_event`` passive-inference ledger -- that ledger is
    Tier-0 exposure/bookkeeping evidence only (spec §3.3: "Tier-0 evidence
    may move R and bookkeeping only, never S", AC-014) and is never a
    plasticity-S input, capped or not.

    Ordered by ``(captured_at, id)`` so replay -- and therefore every S
    update it drives -- is deterministic (AC-021: a re-run over the exact
    same rows in the exact same order reproduces the exact same output).
    """
    return conn.execute(
        "SELECT id, session_id, engram_id, signal_tier, set_at, captured_at, "
        "capture_valence, capture_salience, capture_weight FROM eph_tag "
        "WHERE subject_kind = 'node' AND captured_at IS NOT NULL AND consumed_run_id IS NULL "
        "ORDER BY captured_at, id"
    ).fetchall()


def all_captured_node_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """``core/distill.py``'s frequent-path mining input (spec §4.3 Phase 5
    / M6): every captured node tag, **regardless of ``consumed_run_id``**
    -- unlike :func:`pending_captured_node_tags` (Dream Phase 2's S-update
    input, which must stop seeing a tag once it has been folded into a
    Δw), distillation needs the full session history of "what sequence of
    skills, together, produced a captured outcome" to detect a frequent
    path, and a tag already consumed by an earlier potentiation run is
    still perfectly good evidence of that. Tags are purged only by Dream's
    retention window (spec §6.1, ``core/decay.py::purge_retention``), so
    this is bounded by ``retention_days``, not unbounded history.

    Ordered by ``(session_id, set_at)`` so a caller can group by session
    and read off each session's tagged-skill sequence in the order it was
    actually applied.
    """
    return conn.execute(
        "SELECT id, session_id, engram_id, set_at, captured_at, capture_valence FROM eph_tag "
        "WHERE subject_kind = 'node' AND captured_at IS NOT NULL "
        "ORDER BY session_id, set_at, id"
    ).fetchall()


def pending_captured_edge_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Edge-tag analogue of :func:`pending_captured_node_tags`."""
    return conn.execute(
        "SELECT id, session_id, edge_src, edge_dst, edge_type, signal_tier, set_at, captured_at, "
        "capture_valence, capture_salience, capture_weight FROM eph_tag "
        "WHERE subject_kind = 'edge' AND captured_at IS NOT NULL AND consumed_run_id IS NULL "
        "ORDER BY captured_at, id"
    ).fetchall()


def mark_tag_consumed(conn: sqlite3.Connection, tag_id: int, *, run_id: str) -> None:
    """Dream Phase 2: mark a captured tag consumed by this run -- the
    mechanism that makes a second Dream run over unchanged state a genuine
    no-op (AC-020): a re-run's Phase 1 replay simply finds zero pending rows."""
    conn.execute("UPDATE eph_tag SET consumed_run_id = ? WHERE id = ?", (run_id, tag_id))


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


def bump_retrieval(
    conn: sqlite3.Connection, engram_id: str, *, eta_r: float, refractory_s: float = 0.0
) -> tuple[float, bool]:
    """spec §3.3 tool 5 step 4: ``R <- min(1, R + eta_R*(1-R))``.

    ``core/decay.py``/``core/router.py`` (M4) close the *read*-side half of
    R's "reversible and expires naturally" property (docs/03): R is now
    decayed at every read, not just when Dream happens to materialise it.
    This function closes the *write*-side half -- a **refractory window**
    (``refractory_s``, default off): a caller cannot mint a fresh "occasion"
    of retrieval evidence more than once per ``refractory_s`` for the same
    engram, no matter how many ``signal_use`` calls it makes or how many
    session ids it mints (the window is keyed on ``engram_id`` +
    wall-clock time -- exactly the thing a caller cannot forge). This is
    deliberately a **rate limit in time**, not a normalised score: R still
    accumulates toward 1.0 the same way it always has across genuinely
    spaced-out occasions; a burst of calls within one window is simply
    treated as the *same* occasion (returns the current value unbumped,
    ``bumped=False``) rather than N independent ones.

    Returns ``(current_or_new_r, bumped)``.
    """
    row = conn.execute(
        "SELECT r, r_decayed_at FROM eph_retrieval WHERE engram_id = ?", (engram_id,)
    ).fetchone()
    current_r = float(row["r"]) if row is not None else 0.0
    last_touch = row["r_decayed_at"] if row is not None else None
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()

    if refractory_s > 0 and last_touch:
        elapsed = (now_dt - datetime.fromisoformat(str(last_touch))).total_seconds()
        if elapsed < refractory_s:
            return current_r, False

    new_r = min(1.0, current_r + eta_r * (1.0 - current_r))
    conn.execute(
        """
        INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES (?, ?, ?)
        ON CONFLICT(engram_id) DO UPDATE SET r = excluded.r, r_decayed_at = excluded.r_decayed_at
        """,
        (engram_id, new_r, now),
    )
    return new_r, True
