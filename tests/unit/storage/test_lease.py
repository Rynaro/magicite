"""``storage/lease.py``: acquire/contend/release, re-entrancy, the G2 assertion.

M1's minimal in-process ``WriterLease`` (spec §6.2 G2, per the orchestrator's
ruling on the P0 guard seam: a real guard now, upgraded to flock+DB-row+TTL
in M4 -- not a silently-passing stub).
"""

from __future__ import annotations

import pytest

from magicite.errors import BusyError
from magicite.storage import lease


def test_assert_single_writer_denies_without_a_held_lease() -> None:
    assert not lease.held_by_me()
    with pytest.raises(lease.WriterLeaseError):
        lease.assert_single_writer()


def test_writer_lease_context_manager_grants_and_releases() -> None:
    assert not lease.held_by_me()
    with lease.writer_lease():
        assert lease.held_by_me()
        lease.assert_single_writer()  # does not raise
    assert not lease.held_by_me()
    with pytest.raises(lease.WriterLeaseError):
        lease.assert_single_writer()


def test_writer_lease_is_reentrant_within_the_same_context() -> None:
    """Nested acquisition (register() acquiring once, storage.durable's
    functions each calling assert_single_writer()) never deadlocks."""
    with lease.writer_lease():
        with lease.writer_lease():
            assert lease.held_by_me()
            lease.assert_single_writer()
        # still held: the inner `with` releasing must not release a lease
        # the outer `with` is still using.
        assert lease.held_by_me()
    assert not lease.held_by_me()


def test_writer_lease_contention_raises_busy() -> None:
    """spec §2.6 step 1: "acquire writer lease (fail fast if held)" -- two
    independent threads (independent contexts) contending for the same
    process-wide writer lease: the second is denied, fast, non-blocking."""
    import threading

    holder_ready = threading.Event()
    release_holder = threading.Event()
    contended = {"busy": False}

    def _hold() -> None:
        with lease.writer_lease("thread-a"):
            holder_ready.set()
            release_holder.wait(timeout=5)

    t = threading.Thread(target=_hold)
    t.start()
    try:
        assert holder_ready.wait(timeout=5)
        try:
            lease.acquire_writer_lease("thread-b")
        except BusyError:
            contended["busy"] = True
        else:  # pragma: no cover - would indicate the guard is not real
            lease.release_writer_lease()
    finally:
        release_holder.set()
        t.join(timeout=5)

    assert contended["busy"]
    assert not lease.held_by_me()


def test_release_writer_lease_is_idempotent() -> None:
    lease.release_writer_lease()  # never acquired: no-op, does not raise
    with lease.writer_lease():
        pass
    lease.release_writer_lease()  # already released: still a no-op
    assert not lease.held_by_me()
