"""Session registry + TTL window (spec §3.3 "Session resolution rule").

Implements the *one* rule every session-participating tool follows (spec
§3.3): "An explicit ``session_id`` is used verbatim and its ``eph_session``
row is upserted. An omitted ``session_id`` causes the server to mint a fresh
UUIDv4, return it, and treat the call as its own single-call session: it can
therefore never co-activate with an earlier call. Sessions expire at
``session_ttl`` (3h) of inactivity; a call naming an expired session starts a
new one under the same id and records a ``session_resumed_after_expiry``
event. No tool ever depends on connection state."

Framework-free (INV-1) and hot-path-safe: only ``storage.ephemeral`` (Tier C)
is touched, never ``storage.durable``/``engram.writer`` (AC-024 discipline,
even though this module is not itself in the frozen ``HOT_PATH_MODULES`` list
-- it is called *from* several of them, so the same import boundary applies
in spirit).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from magicite.config import Config
from magicite.storage import ephemeral as ephemeral_mod

#: spec §3.3: the event recorded when a call names a session_id whose
#: eph_session row exists but has aged past session_ttl.
SESSION_RESUMED_EVENT = "session_resumed_after_expiry"


@dataclass(frozen=True)
class SessionResolution:
    session_id: str
    minted: bool  # True iff the caller omitted session_id (server minted one)
    resumed_after_expiry: bool  # True iff a named session had expired and was restarted


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def resolve(cfg: Config, conn: sqlite3.Connection, session_id: str | None) -> SessionResolution:
    """The session resolution rule, in one place, called by every tool that
    participates in a session (``core/router.py``, ``core/signals.py``).

    Never raises: an omitted or never-seen ``session_id`` always yields a
    fresh session; only the *event recorded* differs across the three cases
    (minted / continued / resumed-after-expiry).
    """
    now = _now()

    if session_id is None:
        sid = str(uuid.uuid4())
        ephemeral_mod.upsert_session(conn, sid)
        return SessionResolution(session_id=sid, minted=True, resumed_after_expiry=False)

    row = ephemeral_mod.get_session(conn, session_id)
    if row is None:
        ephemeral_mod.upsert_session(conn, session_id)
        return SessionResolution(session_id=session_id, minted=False, resumed_after_expiry=False)

    ttl = timedelta(hours=cfg.session_ttl_hours)
    last_seen = _parse(row["last_seen_at"])
    expired = (now - last_seen) > ttl

    if expired:
        ephemeral_mod.append_event(
            conn,
            session_id=session_id,
            tool="session",
            signal_tier=0,
            engram_id=None,
            payload={"event": SESSION_RESUMED_EVENT, "idle_seconds": (now - last_seen).total_seconds()},
        )

    ephemeral_mod.upsert_session(conn, session_id)
    return SessionResolution(session_id=session_id, minted=False, resumed_after_expiry=expired)


def resolve_id(cfg: Config, conn: sqlite3.Connection, session_id: str | None) -> str:
    """Convenience wrapper for callers (``core/router.py``) that only need
    the resolved id, not the full :class:`SessionResolution`."""
    return resolve(cfg, conn, session_id).session_id


def isoformat_now() -> str:
    return _now().isoformat()


@dataclass
class SessionEndOutcome:
    session_id: str
    closed: bool
    tags_expired: int
    captured_pending: int
    dream_run_id: str | None
    enqueued: bool


def _existing_dream_run(conn: sqlite3.Connection) -> str | None:
    """A queued/running ``consolidation_run``, if one already exists --
    read-only (spec §2.4: ``consolidation_run`` is an operational table, not
    a Tier-C one, so this is exactly the kind of access G1 leaves alone:
    SELECT is never a guarded action)."""
    row = conn.execute(
        "SELECT id FROM consolidation_run WHERE state IN ('queued', 'running') "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return str(row["id"]) if row is not None else None


def session_end(
    cfg: Config, conn: sqlite3.Connection, *, session_id: str, reason: str | None = None
) -> SessionEndOutcome:
    """spec §3.3 tool 7: closes the session, expires its tags (retained for
    the next Dream run), and writes the trace summary event.

    **Deviation, documented (not a redesign of G1 -- see FINAL RESPONSE):**
    spec's prose also has this tool "enqueue Dream when
    ``dream.on_session_end`` and ``now - last_run >= dream.min_interval_s``".
    ``session_end`` is one of the seven tools spec §6.2 G1 hands *only* the
    authorizer-restricted connection (this module never imports
    ``storage.durable``/``engram.writer``, AC-024 discipline) -- and
    ``consolidation_run`` is not ``eph_``-prefixed, so G1 denies a direct
    INSERT here unconditionally, exactly as it denies one against ``engram``
    or ``edge``. The *decision* (would a run be triggered) is computed
    honestly from real reads; the *enqueue write* is left for Dream's own
    lease-guarded orchestration (``core/dream.py``, M4) to perform, rather
    than reaching around G1 -- one of this milestone's own two P0
    guards -- to fake an enqueue from the hot path.
    """
    now_dt = _now()
    now = now_dt.isoformat()

    closed = ephemeral_mod.close_session(conn, session_id)
    tags_expired = ephemeral_mod.expire_session_tags(conn, session_id=session_id, now=now)
    captured_pending = ephemeral_mod.count_pending_captured_tags(conn, session_id=session_id)

    ephemeral_mod.append_event(
        conn,
        session_id=session_id,
        tool="session_end",
        signal_tier=1,
        engram_id=None,
        payload={
            "reason": reason,
            "closed": closed,
            "tags_expired": tags_expired,
            "captured_pending": captured_pending,
        },
    )

    dream_run_id = _existing_dream_run(conn) if cfg.dream_on_session_end else None

    return SessionEndOutcome(
        session_id=session_id,
        closed=closed,
        tags_expired=tags_expired,
        captured_pending=captured_pending,
        dream_run_id=dream_run_id,
        enqueued=False,
    )
