"""``storage/lease.py``: acquire/contend/release, re-entrancy, the G2 assertion.

M1's minimal in-process ``WriterLease`` (spec §6.2 G2, per the orchestrator's
ruling on the P0 guard seam: a real guard now, upgraded to flock+DB-row+TTL
in M4 -- not a silently-passing stub).
"""

from __future__ import annotations

import threading
import time

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


# ── G3: the Dream-context assertion (M4, spec §6.2) ─────────────────────


def test_assert_dream_context_denies_outside_dream() -> None:
    assert not lease.in_dream_context()
    with pytest.raises(lease.DreamContextError):
        lease.assert_dream_context()


def test_dream_context_grants_and_releases() -> None:
    assert not lease.in_dream_context()
    with lease.dream_context():
        assert lease.in_dream_context()
        lease.assert_dream_context()  # does not raise
    assert not lease.in_dream_context()
    with pytest.raises(lease.DreamContextError):
        lease.assert_dream_context()


def test_dream_context_error_is_a_busy_error() -> None:
    """Same remedy shape as G2: retry through the correct entry point."""
    from magicite.errors import BusyError

    assert issubclass(lease.DreamContextError, BusyError)


# ── The real cross-process WriterLease (M4, spec §4.2, AC-025) ──────────


@pytest.fixture
def lease_conn(tmp_path):

    from magicite.storage import db as db_mod

    conn = db_mod.connect(tmp_path / "lease.db")
    yield conn
    conn.close()


def test_cross_process_lease_acquire_and_release(tmp_path, lease_conn) -> None:
    cp_lease = lease.CrossProcessLease(lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-a")
    result = cp_lease.try_acquire()
    assert result.holder == "holder-a"
    assert not result.stolen
    row = lease_conn.execute("SELECT holder FROM writer_lease WHERE id = 1").fetchone()
    assert row["holder"] == "holder-a"
    cp_lease.release()
    assert lease_conn.execute("SELECT holder FROM writer_lease WHERE id = 1").fetchone() is None


def test_cross_process_lease_contention_raises_busy(tmp_path, lease_conn) -> None:
    """AC-025's own mechanism: a second holder attempting the SAME lock
    path + DB row while the first is still held is denied, fast, without
    writing any durable state -- and this is asserted against the real
    ``writer_lease`` DB row (not a mock), so removing/weakening the guard
    would make this test fail."""
    first = lease.CrossProcessLease(lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-a")
    first.try_acquire()
    try:
        second = lease.CrossProcessLease(
            lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-b"
        )
        with pytest.raises(BusyError):
            second.try_acquire()
        # the losing side must not have touched the lease row.
        row = lease_conn.execute("SELECT holder FROM writer_lease WHERE id = 1").fetchone()
        assert row["holder"] == "holder-a"
    finally:
        first.release()


def test_cross_process_lease_reclaims_an_expired_lease(tmp_path, lease_conn) -> None:
    """spec §4.2: "a lease whose expires_at has passed is reclaimable"."""
    import os
    from datetime import UTC, datetime, timedelta

    lease_conn.execute(
        "INSERT INTO writer_lease (id, holder, pid, acquired_at, heartbeat_at, expires_at) "
        "VALUES (1, 'stale-holder', 999999, ?, ?, ?)",
        (
            (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),  # expired 5 minutes ago
        ),
    )
    new_holder = lease.CrossProcessLease(
        lock_path=tmp_path / "dream.lock", conn=lease_conn, holder=f"fresh:{os.getpid()}"
    )
    result = new_holder.try_acquire()
    assert result.stolen is True
    row = lease_conn.execute("SELECT holder FROM writer_lease WHERE id = 1").fetchone()
    assert row["holder"] == new_holder.holder
    new_holder.release()


def test_cross_process_lease_heartbeat_extends_expiry(tmp_path, lease_conn) -> None:
    cp_lease = lease.CrossProcessLease(
        lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-a", ttl_s=60.0
    )
    cp_lease.try_acquire()
    before = lease_conn.execute("SELECT expires_at FROM writer_lease WHERE id = 1").fetchone()["expires_at"]
    cp_lease.heartbeat()
    after = lease_conn.execute("SELECT expires_at FROM writer_lease WHERE id = 1").fetchone()["expires_at"]
    assert after >= before
    cp_lease.release()


def test_cross_process_lease_context_manager_releases_on_exception(tmp_path, lease_conn) -> None:
    cp_lease = lease.CrossProcessLease(lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-a")
    with pytest.raises(RuntimeError):
        with cp_lease.acquire():
            raise RuntimeError("boom")
    assert lease_conn.execute("SELECT holder FROM writer_lease WHERE id = 1").fetchone() is None
    # a fresh acquire must now succeed -- proves the flock was released too.
    other = lease.CrossProcessLease(lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-b")
    other.try_acquire()
    other.release()


def test_concurrent_expired_acquisition_has_one_winner(tmp_path, monkeypatch) -> None:
    from magicite.storage import db as db_mod

    db_path = tmp_path / "lease.db"
    seed = db_mod.connect(db_path)
    seed.execute(
        "INSERT INTO writer_lease "
        "(id, holder, pid, acquired_at, heartbeat_at, expires_at, fencing_token) "
        "VALUES (1, 'expired', 1, '2000-01-01T00:00:00+00:00', "
        "'2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00', 4)"
    )
    seed.close()

    monkeypatch.setattr(lease.CrossProcessLease, "_try_flock", lambda self: True)
    barrier = threading.Barrier(2)
    winners: list[tuple[str, int]] = []
    busy: list[str] = []

    def contend(holder: str) -> None:
        conn = db_mod.connect(db_path)
        candidate = lease.CrossProcessLease(
            lock_path=tmp_path / "dream.lock", conn=conn, holder=holder
        )
        barrier.wait(timeout=5)
        try:
            result = candidate.try_acquire()
            winners.append((holder, result.fencing_token))
        except BusyError:
            busy.append(holder)
        finally:
            conn.close()

    threads = [threading.Thread(target=contend, args=(holder,)) for holder in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(winners) == 1
    assert len(busy) == 1
    assert winners[0][1] == 5


def test_ttl_overrun_fences_stale_writer(tmp_path, lease_conn) -> None:
    cp_lease = lease.CrossProcessLease(
        lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-a", ttl_s=0.01
    )
    cp_lease.try_acquire()
    time.sleep(0.03)
    with pytest.raises(BusyError):
        cp_lease.heartbeat()
    cp_lease.release()


def test_context_heartbeats_during_long_work(tmp_path, lease_conn) -> None:
    cp_lease = lease.CrossProcessLease(
        lock_path=tmp_path / "dream.lock",
        conn=lease_conn,
        holder="holder-a",
        # Preserve the production 6:1 TTL/heartbeat margin while leaving
        # enough scheduling room for shared CI runners. The work below still
        # exceeds the original TTL and therefore requires a real heartbeat.
        ttl_s=1.0,
        heartbeat_interval_s=0.1,
    )
    with cp_lease.acquire():
        time.sleep(1.2)
        cp_lease.assert_owned()


def test_lost_token_fences_durable_write(tmp_path, lease_conn) -> None:
    from magicite.storage import durable as durable_mod

    cp_lease = lease.CrossProcessLease(
        lock_path=tmp_path / "dream.lock", conn=lease_conn, holder="holder-a"
    )
    with cp_lease.acquire():
        lease_conn.execute(
            "UPDATE writer_lease SET holder = 'replacement', fencing_token = fencing_token + 1 "
            "WHERE id = 1"
        )
        with lease.writer_lease():
            with pytest.raises(BusyError):
                durable_mod.write_schema_meta(lease_conn, "fenced", "must-not-land")
    assert lease_conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'fenced'"
    ).fetchone() is None
