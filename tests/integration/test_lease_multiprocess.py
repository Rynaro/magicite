"""OS-process verification for Dream lease fencing and heartbeats.

The unit suite exercises the same SQLite protocol with independent threads.
These tests deliberately use ``spawn`` processes and separate lock paths over
one database.  Separate paths model hosts/container mounts where ``flock`` is
not shared, leaving the database row and fencing token as the authoritative
cross-process guard.
"""

from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import pytest

from magicite.errors import BusyError
from magicite.storage import db as db_mod
from magicite.storage import lease


def _expired_lease_contender(
    db_path: str,
    lock_path: str,
    holder: str,
    ready: Any,
    start: Any,
    release_winner: Any,
    results: Any,
) -> None:
    conn = db_mod.connect(db_path)
    candidate = lease.CrossProcessLease(
        lock_path=lock_path,
        conn=conn,
        holder=holder,
        ttl_s=5.0,
    )
    try:
        ready.put(holder)
        if not start.wait(timeout=10):
            results.put({"holder": holder, "status": "timeout"})
            return
        try:
            acquired = candidate.try_acquire()
        except BusyError:
            results.put({"holder": holder, "status": "busy"})
            return
        results.put(
            {
                "holder": holder,
                "status": "acquired",
                "fencing_token": acquired.fencing_token,
            }
        )
        release_winner.wait(timeout=10)
    finally:
        candidate.release()
        conn.close()


def _heartbeat_holder(
    db_path: str,
    lock_path: str,
    ready: Any,
    release_holder: Any,
    results: Any,
) -> None:
    conn = db_mod.connect(db_path)
    candidate = lease.CrossProcessLease(
        lock_path=lock_path,
        conn=conn,
        holder="heartbeat-holder",
        ttl_s=0.6,
        heartbeat_interval_s=0.1,
    )
    try:
        with candidate.acquire() as acquired:
            ready.set()
            release_holder.wait(timeout=10)
            candidate.assert_owned()
            results.put({"status": "owned", "fencing_token": acquired.fencing_token})
    except BaseException as exc:
        results.put({"status": "error", "error": repr(exc)})
        raise
    finally:
        conn.close()


def _stale_writer(
    db_path: str,
    lock_path: str,
    acquired: Any,
    attempt_write: Any,
    results: Any,
) -> None:
    conn = db_mod.connect(db_path)
    candidate = lease.CrossProcessLease(
        lock_path=lock_path,
        conn=conn,
        holder="stale-holder",
        ttl_s=0.35,
    )
    try:
        lease_result = candidate.try_acquire()
        acquired.put(lease_result.fencing_token)
        if not attempt_write.wait(timeout=10):
            results.put({"status": "timeout"})
            return
        try:
            candidate.assert_owned()
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('stale-write', 'landed')"
            )
        except BusyError:
            results.put({"status": "fenced"})
        else:
            results.put({"status": "committed"})
    finally:
        candidate.release()
        conn.close()


def _spawn_context() -> Any:
    return mp.get_context("spawn")


def _join_cleanly(processes: list[mp.Process], *, timeout: float = 15.0) -> None:
    for process in processes:
        process.join(timeout=timeout)
    still_alive = [process for process in processes if process.is_alive()]
    for process in still_alive:
        process.terminate()
        process.join(timeout=5)
    assert not still_alive, "multiprocess lease fixture timed out"
    assert [process.exitcode for process in processes] == [0] * len(processes)


@pytest.mark.acceptance
def test_concurrent_expired_acquisition_has_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "lease.db"
    seed = db_mod.connect(db_path)
    seed.execute(
        "INSERT INTO writer_lease "
        "(id, holder, pid, acquired_at, heartbeat_at, expires_at, fencing_token) "
        "VALUES (1, 'expired', 1, '2000-01-01T00:00:00+00:00', "
        "'2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00', 4)"
    )
    seed.close()

    ctx = _spawn_context()
    ready = ctx.Queue()
    results = ctx.Queue()
    start = ctx.Event()
    release_winner = ctx.Event()
    processes = [
        ctx.Process(
            target=_expired_lease_contender,
            args=(
                str(db_path),
                str(tmp_path / f"dream-{holder}.lock"),
                holder,
                ready,
                start,
                release_winner,
                results,
            ),
        )
        for holder in ("process-a", "process-b")
    ]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {"process-a", "process-b"}
        start.set()
        outcomes = [results.get(timeout=10), results.get(timeout=10)]
        winners = [outcome for outcome in outcomes if outcome["status"] == "acquired"]
        losers = [outcome for outcome in outcomes if outcome["status"] == "busy"]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0]["fencing_token"] == 5
    finally:
        release_winner.set()
        _join_cleanly(processes)


@pytest.mark.acceptance
def test_periodic_heartbeat_preserves_ownership_across_ttl(tmp_path: Path) -> None:
    db_path = tmp_path / "lease.db"
    seed = db_mod.connect(db_path)
    seed.close()

    ctx = _spawn_context()
    ready = ctx.Event()
    release_holder = ctx.Event()
    results = ctx.Queue()
    process = ctx.Process(
        target=_heartbeat_holder,
        args=(str(db_path), str(tmp_path / "holder.lock"), ready, release_holder, results),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        time.sleep(0.9)
        contender_conn = db_mod.connect(db_path)
        contender = lease.CrossProcessLease(
            lock_path=tmp_path / "contender.lock",
            conn=contender_conn,
            holder="contender",
        )
        try:
            with pytest.raises(BusyError):
                contender.try_acquire()
        finally:
            contender.release()
            contender_conn.close()
        release_holder.set()
        assert results.get(timeout=10)["status"] == "owned"
    finally:
        release_holder.set()
        _join_cleanly([process])


@pytest.mark.acceptance
def test_ttl_overrun_fences_stale_writer(tmp_path: Path) -> None:
    db_path = tmp_path / "lease.db"
    seed = db_mod.connect(db_path)
    seed.close()

    ctx = _spawn_context()
    acquired = ctx.Queue()
    attempt_write = ctx.Event()
    results = ctx.Queue()
    stale = ctx.Process(
        target=_stale_writer,
        args=(str(db_path), str(tmp_path / "stale.lock"), acquired, attempt_write, results),
    )
    stale.start()

    replacement_conn = None
    replacement = None
    try:
        stale_token = acquired.get(timeout=10)
        time.sleep(0.6)
        replacement_conn = db_mod.connect(db_path)
        replacement = lease.CrossProcessLease(
            lock_path=tmp_path / "replacement.lock",
            conn=replacement_conn,
            holder="replacement",
            ttl_s=5.0,
        )
        replacement_result = replacement.try_acquire()
        assert replacement_result.fencing_token == stale_token + 1

        attempt_write.set()
        assert results.get(timeout=10)["status"] == "fenced"
        assert replacement_conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'stale-write'"
        ).fetchone() is None
    finally:
        attempt_write.set()
        _join_cleanly([stale])
        if replacement is not None:
            replacement.release()
        if replacement_conn is not None:
            replacement_conn.close()
