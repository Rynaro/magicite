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


def _stale_domain_writer(
    project_root: str,
    lock_path: str,
    domain: str,
    acquired: Any,
    attempt_write: Any,
    results: Any,
) -> None:
    """Hold a fenced lease via try_acquire (no heartbeat), then attempt a domain write."""
    from magicite.config import Config
    from magicite.core import approvals as approvals_mod
    from magicite.core import evidence as evidence_mod
    from magicite.core import fingerprint_key as fk
    from magicite.core import trust as trust_mod
    from magicite.errors import BusyError
    from magicite.storage import db as db_mod
    from magicite.storage import lease

    cfg = Config.load(Path(project_root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    conn = db_mod.connect(cfg.db_path)
    candidate = lease.CrossProcessLease(
        lock_path=lock_path,
        conn=conn,
        holder=f"stale-{domain}",
        ttl_s=0.35,
    )
    try:
        lease_result = candidate.try_acquire()
        acquired.put(lease_result.fencing_token)
        if not attempt_write.wait(timeout=10):
            results.put({"domain": domain, "status": "timeout"})
            return
        try:
            # Fence check first (same as schema_meta stale writer).
            candidate.assert_owned()
            if domain == "evidence":
                event = evidence_mod.EvidenceEvent(
                    event_id="ev_stale_fence",
                    decision_id="dec_stale_fence",
                    event_type="decision",
                    recorded_at="2026-09-29T12:00:00+00:00",
                    candidate_ids=("skill_a",),
                    chosen_action="skill_a",
                    behavior_policy_id="dense-v1",
                    behavior_policy_digest="digest_a",
                    propensity=1.0,
                    query_fingerprint="c" * 64,
                    fingerprint_scheme=fk.FINGERPRINT_SCHEME,
                    source_tier=0,
                    retention_class="operational",
                )
                # Without context-var lease, checkpoint acquires anew → BusyError
                # once replacement holds the row; assert_owned already failed above
                # when fenced, so this is defense-in-depth if assert is skipped.
                evidence_mod.checkpoint(cfg, conn, event)
            elif domain == "trust":
                trust_mod.save_policy(cfg, trust_mod.default_policy())
            elif domain == "approvals":
                with lease.writer_lease(holder="stale-approvals"):
                    lease.assert_single_writer()
                    approvals_mod.propose(
                        conn,
                        cfg,
                        op="nucleate",
                        target_name="stale-target",
                        payload={"note": "should-not-land"},
                        proposed_by="stale",
                    )
            else:
                results.put({"domain": domain, "status": "unknown-domain"})
                return
        except BusyError:
            results.put({"domain": domain, "status": "fenced"})
        else:
            results.put({"domain": domain, "status": "committed"})
    finally:
        candidate.release()
        conn.close()


@pytest.mark.acceptance
@pytest.mark.parametrize("domain", ["evidence", "trust", "approvals"])
def test_stale_writer_cannot_commit_domain_stores(tmp_path: Path, domain: str) -> None:
    """AC-S12-04: stale/killed lease holder cannot commit via evidence/trust/approvals.

    Policy-store writer is a forward for S07 (not merged on this integration base).
    """
    from magicite.config import Config
    from magicite.core import fingerprint_key as fk
    from magicite.storage import db as db_mod

    project = tmp_path / "proj"
    (project / ".magicite" / "engrams").mkdir(parents=True)
    (project / ".magicite" / "engrams" / "toy.egr.md").write_text(
        "---\n"
        "spec: engram/0.2\n"
        "name: lease-toy\n"
        "id: egr_lease0001\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: Fence stale writers\n"
        "  use_when: lease tests\n"
        "  not_when: skipping fence\n"
        "triggers:\n"
        "  positive: [lease]\n"
        "  negative: [race]\n"
        "---\n"
        "## Procedure\n"
        "1. Acquire lease.\n",
        encoding="utf-8",
    )
    cfg = Config.load(project, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg.ensure_dirs()
    fk.load_or_create_fingerprint_key(cfg)
    seed = db_mod.connect(cfg.db_path)
    seed.close()
    # trust domain only needs an initialized DB + lease row; no registry seed required.

    ctx = _spawn_context()
    acquired = ctx.Queue()
    attempt_write = ctx.Event()
    results = ctx.Queue()
    stale = ctx.Process(
        target=_stale_domain_writer,
        args=(
            str(project),
            str(tmp_path / f"stale-{domain}.lock"),
            domain,
            acquired,
            attempt_write,
            results,
        ),
    )
    stale.start()

    replacement_conn = None
    replacement = None
    try:
        stale_token = acquired.get(timeout=10)
        time.sleep(0.6)
        replacement_conn = db_mod.connect(cfg.db_path)
        replacement = lease.CrossProcessLease(
            lock_path=tmp_path / f"replacement-{domain}.lock",
            conn=replacement_conn,
            holder="replacement",
            ttl_s=5.0,
        )
        replacement_result = replacement.try_acquire()
        assert replacement_result.fencing_token == stale_token + 1

        attempt_write.set()
        outcome = results.get(timeout=15)
        assert outcome["domain"] == domain
        assert outcome["status"] == "fenced", outcome

        if domain == "approvals":
            assert not list(cfg.approvals_dir.glob("*.json"))
    finally:
        attempt_write.set()
        _join_cleanly([stale])
        if replacement is not None:
            replacement.release()
        if replacement_conn is not None:
            replacement_conn.close()
