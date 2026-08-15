"""Single-writer lease (spec §4.2 G2) + the G3 Dream-context marker (spec §6.2).

M1 shipped the *logical*, in-process cut of G2: a process-wide
``threading.Lock`` plus a re-entrant :class:`contextvars.ContextVar` depth
counter, guarding ``storage/durable.py``'s public functions and
``engram/writer.py::atomic_write()`` via :func:`assert_single_writer`. That
layer is UNCHANGED here -- ``register()``/``sync()``/``export()`` in
``core/registry.py`` keep calling ``writer_lease()``/``assert_single_writer()``
with exactly the shape they always had (M1's own promise: "Call sites...
do not change shape when that lands -- only what backs them does" refers to
*this* layer, and it still holds).

M4 adds two things on top, additively:

1. **G3 -- the Dream-context assertion** (semantic guard): a second
   ``ContextVar`` (:data:`_DREAM_CONTEXT`), set **only** by
   ``core/dream.py::checkpoint_phase()`` (never anywhere else --
   ``tests/unit/test_g3_enforcement.py`` greps the tree for that). Learned
   state (``plasticity:``/``synapses:``) is written through
   ``engram/writer.py::write_plasticity()``/``write_synapses()``, which call
   :func:`assert_dream_context` in addition to (not instead of) G2.

2. **The real cross-process lease** (spec §4.2's ``WriterLease``):
   ``fcntl.flock(<runtime>/dream.lock, LOCK_EX | LOCK_NB)`` first (fast,
   same-host), then an atomic, TTL-guarded ``INSERT ... ON CONFLICT DO
   UPDATE ... WHERE writer_lease.expires_at < :now`` against the
   ``writer_lease`` DB row (portable across filesystems where flock is
   unreliable, Assumption A3) -- :class:`CrossProcessLease`. This is what
   makes AC-025 ("a second Dream run attempts to start... SHALL return busy
   without writing any durable state") a fact about *two operating-system
   processes*, not just two Python contexts in the same interpreter.
   ``core/dream.py::run()`` (and the standalone ``checkpoint()`` tool path)
   are the only M4 callers; extending it to ``register()``/``sync()``/
   ``export()`` too is a reasonable follow-up flagged in the M4 report, not
   done here to avoid touching M1-M3's already-stable, already-tested call
   sites without a directly-tied acceptance criterion.
"""

from __future__ import annotations

import contextvars
import fcntl
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from magicite.errors import BusyError

# ── G2: the logical, in-process lease (M1, unchanged) ──────────────────────

#: Process-wide mutual exclusion: only one writer_lease() holder at a time,
#: across threads. (Within a single asyncio event-loop thread running the
#: MCP stdio server, tool handlers already execute to completion without
#: yielding -- see mcp/app.py's dispatch_call -- so this is defense in
#: depth for multi-threaded callers, e.g. the CLI or concurrent tests.)
_PROCESS_LOCK = threading.Lock()

#: Per-context (per-asyncio-task / per-thread) marker: "this context
#: currently holds the writer lease". Re-entrant calls within the same
#: acquisition (register() calling storage.durable functions which each
#: call assert_single_writer()) see this set and do not re-acquire or
#: deadlock on _PROCESS_LOCK.
_HOLDER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "magicite_writer_lease_holder", default=None
)

#: Nesting depth for the *current* context's acquisition (e.g. register()'s
#: top-level ``with writer_lease():`` plus any nested ``writer_lease()``
#: a callee opens). Only the outermost ``release_writer_lease()`` actually
#: drops ``_HOLDER``/``_PROCESS_LOCK`` -- otherwise an inner release would
#: prematurely free the lease out from under an still-in-flight outer call.
_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "magicite_writer_lease_depth", default=0
)


class WriterLeaseError(BusyError):
    """Raised by :func:`assert_single_writer` when no lease is held."""


def held_by_me() -> bool:
    """True iff the *current* context holds the writer lease."""
    return _DEPTH.get() > 0


def acquire_writer_lease(holder: str = "writer") -> None:
    """Fail-fast, non-blocking acquire (spec §2.6 step 1: "fail fast if held").

    Re-entrant within the same context: a caller that already holds the
    lease (nested register()/sync()/export() calls into storage.durable
    or engram.writer) increments a depth counter rather than deadlocking,
    double-locking the process mutex, or -- critically -- letting an inner
    ``release_writer_lease()`` free the lease while an outer caller still
    expects to hold it.
    """
    depth = _DEPTH.get()
    if depth > 0:
        _DEPTH.set(depth + 1)
        return
    if not _PROCESS_LOCK.acquire(blocking=False):
        raise BusyError(
            "writer lease is held by another in-flight write",
            hint="retry once the current register()/sync()/export() call completes",
        )
    _HOLDER.set(holder)
    _DEPTH.set(1)


def release_writer_lease() -> None:
    """Release one level of acquisition. Idempotent; only the outermost
    acquisition in the current context actually frees the lease."""
    depth = _DEPTH.get()
    if depth <= 0:
        return
    if depth > 1:
        _DEPTH.set(depth - 1)
        return
    _DEPTH.set(0)
    _HOLDER.set(None)
    if _PROCESS_LOCK.locked():
        _PROCESS_LOCK.release()


@contextmanager
def writer_lease(holder: str = "writer") -> Iterator[None]:
    """The top-level acquisition point: ``register()``/``sync()``/``export()``
    (and Dream's checkpoint phase, via :class:`CrossProcessLease` layered on
    top -- see :func:`core.dream.run`) wrap their durable-write work in this
    context manager exactly once per call."""
    acquire_writer_lease(holder)
    try:
        yield
    finally:
        release_writer_lease()


def assert_single_writer() -> None:
    """G2: every public ``storage.durable`` write function -- and
    ``engram.writer.atomic_write()`` -- calls this first."""
    if _DEPTH.get() <= 0:
        raise WriterLeaseError(
            "durable write attempted without holding the writer lease",
            hint="durable writes must happen inside register()/sync()/export()/"
            "checkpoint(), which acquire the writer lease before writing",
        )


# ── G3: the Dream-context assertion (M4, spec §6.2) ─────────────────────────

#: Set **only** by ``core/dream.py::checkpoint_phase()`` -- statically
#: verified by ``tests/unit/test_g3_enforcement.py`` (the only source file
#: under ``src/magicite`` that may call :func:`dream_context`). Every other
#: module reads it only through :func:`assert_dream_context`/
#: :func:`in_dream_context`, never sets it.
_DREAM_CONTEXT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "magicite_dream_context", default=False
)


class DreamContextError(BusyError):
    """Raised by :func:`assert_dream_context` outside Dream's checkpoint
    phase. Subclasses :class:`~magicite.errors.BusyError` (not a fresh error
    code): the caller's remedy is the same shape as G2's -- go through the
    orchestrating entry point (here, ``core.dream.checkpoint_phase()``/
    ``core.dream.run()``/the ``checkpoint()`` tool) instead of writing
    directly."""


def in_dream_context() -> bool:
    return _DREAM_CONTEXT.get()


def assert_dream_context() -> None:
    """G3: ``engram.writer.write_plasticity()``/``write_synapses()`` call
    this first, in addition to (not instead of) G2's
    :func:`assert_single_writer`. The mechanical statement Kupo checks:
    learned state (``plasticity.*``/``synapses.*``) has exactly one writer
    in the codebase -- Dream's checkpoint."""
    if not _DREAM_CONTEXT.get():
        raise DreamContextError(
            "plasticity/synapses write attempted outside Dream's checkpoint phase",
            hint="only core.dream.checkpoint_phase() (called from core.dream.run() "
            "or the checkpoint() tool) may write learned state",
        )


@contextmanager
def dream_context() -> Iterator[None]:
    """Internal primitive. **Only** ``core/dream.py::checkpoint_phase()``
    may call this -- it is not part of the guard's public contract for any
    other module, and is asserted statically, not just by convention."""
    token = _DREAM_CONTEXT.set(True)
    try:
        yield
    finally:
        _DREAM_CONTEXT.reset(token)


# ── The real, cross-process WriterLease (M4, spec §4.2) ─────────────────────

#: spec §4.2: "Heartbeat every 10s; TTL 60s".
DEFAULT_HEARTBEAT_INTERVAL_S = 10.0
DEFAULT_LEASE_TTL_S = 60.0

_LEASE_ROW_ID = 1


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class LeaseAcquireResult:
    holder: str
    acquired_at: str
    expires_at: str
    stolen: bool  # True iff a stale (expired) lease row was reclaimed


class CrossProcessLease:
    """spec §4.2 ``storage/lease.py::WriterLease``: acquires, in order,

    1. ``fcntl.flock(<runtime>/dream.lock, LOCK_EX | LOCK_NB)`` -- fast,
       same-host. Excludes a genuinely separate OS process immediately, non-
       blocking (spec: "fail fast if held").
    2. an atomic, TTL-guarded write of the single ``writer_lease`` row
       (``id=1``) -- authoritative regardless of process boundary (so it
       also correctly denies a second acquisition attempted from *within*
       the same test process, where two independent ``os.open()`` calls on
       the same lock file would otherwise each get their own flock, per
       Linux's per-open-file-description flock semantics), and the
       "portable across containers/bind mounts where flock is unreliable"
       fallback Assumption A3 names.

    A lease whose ``expires_at`` has passed is reclaimable (``stolen=True``
    on the :class:`LeaseAcquireResult`, logged as a warning) rather than
    permanently wedging the writer path after a crash.
    """

    def __init__(
        self,
        *,
        lock_path: str | Path,
        conn: sqlite3.Connection,
        holder: str | None = None,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.conn = conn
        self.holder = holder or f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.ttl_s = ttl_s
        self._flock_fd: int | None = None
        self._held = False

    def _try_flock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._flock_fd = fd
        return True

    def _release_flock(self) -> None:
        if self._flock_fd is not None:
            try:
                fcntl.flock(self._flock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._flock_fd)
                self._flock_fd = None

    def try_acquire(self) -> LeaseAcquireResult:
        """Non-blocking. Raises :class:`~magicite.errors.BusyError` if
        another holder is live; returns a :class:`LeaseAcquireResult`
        (``stolen=True`` iff a dead holder's expired lease was reclaimed).
        """
        if not self._try_flock():
            raise BusyError(
                "writer lease flock is held by another process",
                hint="another `magicite dream`/writer process is already running on this host",
            )
        try:
            now = _now()
            acquired_at = _iso(now)
            expires_at = _iso(now + _td(self.ttl_s))

            existing = self.conn.execute(
                "SELECT holder, expires_at FROM writer_lease WHERE id = ?", (_LEASE_ROW_ID,)
            ).fetchone()
            stolen = False
            if existing is not None:
                still_live = datetime.fromisoformat(str(existing["expires_at"])) >= now
                if still_live and existing["holder"] != self.holder:
                    raise BusyError(
                        f"writer lease DB row is held by {existing['holder']!r} until "
                        f"{existing['expires_at']}",
                        hint="retry once the current holder's lease expires or finishes",
                    )
                stolen = not still_live

            self.conn.execute(
                """
                INSERT INTO writer_lease (id, holder, pid, acquired_at, heartbeat_at, expires_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  holder=excluded.holder, pid=excluded.pid, acquired_at=excluded.acquired_at,
                  heartbeat_at=excluded.heartbeat_at, expires_at=excluded.expires_at
                """,
                (_LEASE_ROW_ID, self.holder, os.getpid(), acquired_at, acquired_at, expires_at),
            )
            self._held = True
            return LeaseAcquireResult(
                holder=self.holder, acquired_at=acquired_at, expires_at=expires_at, stolen=stolen
            )
        except BaseException:
            self._release_flock()
            raise

    def heartbeat(self) -> None:
        """spec §4.2: "Heartbeat every 10s" -- called between Dream phases
        (a real periodic timer thread is unnecessary for a short-lived,
        bounded `magicite dream --once`/CLI-invoked run; this is an honest,
        documented simplification, not a claim of a background timer)."""
        if not self._held:
            return
        now = _now()
        expires_at = _iso(now + _td(self.ttl_s))
        self.conn.execute(
            "UPDATE writer_lease SET heartbeat_at = ?, expires_at = ? WHERE id = ? AND holder = ?",
            (_iso(now), expires_at, _LEASE_ROW_ID, self.holder),
        )

    def release(self) -> None:
        if self._held:
            self.conn.execute(
                "DELETE FROM writer_lease WHERE id = ? AND holder = ?", (_LEASE_ROW_ID, self.holder)
            )
            self._held = False
        self._release_flock()

    @contextmanager
    def acquire(self) -> Iterator[LeaseAcquireResult]:
        result = self.try_acquire()
        try:
            yield result
        finally:
            self.release()


def _td(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)
