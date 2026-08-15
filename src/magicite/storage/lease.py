"""In-process single-writer lease (spec §4.2 G2), M1 minimal cut.

Spec's G2 ("Lease assertion (logical)", §6.2) reads: "``storage/durable.py``
is the only module that may open a ``writer_connection()``, and every
public function in it starts with ``assert_single_writer()``, which fails
unless the calling task holds the ``WriterLease``. ``engram/writer.py::
atomic_write()`` carries the same assertion."

M0 shipped both of those call sites as no-op stubs (``assert_single_writer
_stub``/``assert_dream_context_stub`` in ``engram/writer.py``) because
nothing durable was written yet. M1 lands the real minimum viable cut of
G2 per the orchestrator's ruling on the P0 guard seam: a silently-passing
guard "reads as enforcement" without being enforcement, which is worse
than having none. This module is that real guard -- deliberately
minimal (a process-local mutex plus a :class:`contextvars.ContextVar`
holder marker, not the flock+DB-row+TTL+heartbeat lease spec §4.2
describes for Dream's cross-process/cross-container concurrency story).

G3 (the Dream-context assertion) is unaffected and stays a no-op stub in
``engram/writer.py`` until M4 defines ``core/dream.py::checkpoint_phase()``
and the ``ContextVar`` it sets -- ``register()``/``sharpen()`` legitimately
write *authored* state through ``atomic_write()`` without ever being in a
Dream checkpoint context, so G3 cannot live inside the generic
``atomic_write()`` path the way G2 does.

M4 upgrades *this exact module* to the full ``fcntl.flock(<runtime>/dream
.lock)`` + ``INSERT OR REPLACE INTO writer_lease`` DB-row fallback with
10s heartbeat / 60s TTL (spec §4.2). Call sites (``assert_single_writer()``,
the ``writer_lease()`` context manager) do not change shape when that
lands -- only what backs them does.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from magicite.errors import BusyError

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
    (and, from M4, Dream's checkpoint phase) wrap their durable-write work
    in this context manager exactly once per call."""
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
