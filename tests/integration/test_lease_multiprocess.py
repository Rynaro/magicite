"""OS-process verification for Dream lease fencing and heartbeats.

The unit suite exercises the same SQLite protocol with independent threads.
These tests deliberately use ``spawn`` processes and separate lock paths over
one database.  Separate paths model hosts/container mounts where ``flock`` is
not shared, leaving the database row and fencing token as the authoritative
cross-process guard.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
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
            conn.execute("INSERT INTO schema_meta (key, value) VALUES ('stale-write', 'landed')")
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
        assert (
            replacement_conn.execute("SELECT value FROM schema_meta WHERE key = 'stale-write'").fetchone()
            is None
        )
    finally:
        attempt_write.set()
        _join_cleanly([stale])
        if replacement is not None:
            replacement.release()
        if replacement_conn is not None:
            replacement_conn.close()


def _stale_domain_writer(
    project_root, lock_path, domain, acquired, attempt_write, results, directory, registry_id
):
    from tests.support.custody_adapter import attach_fixture

    with attach_fixture(Path(project_root), Path(directory), registry_id):
        _stale_domain_attached(project_root, lock_path, domain, acquired, attempt_write, results)


def _stale_domain_attached(
    project_root: str,
    lock_path: str,
    domain: str,
    acquired: Any,
    attempt_write: Any,
    results: Any,
) -> None:
    """Hold a fenced lease via try_acquire, then attempt a REAL domain write."""
    from magicite.config import Config
    from magicite.core import approvals as approvals_mod
    from magicite.core import evidence as evidence_mod
    from magicite.core import fingerprint_key as fk
    from magicite.core import policy_store as ps
    from magicite.core import trust as trust_mod
    from magicite.errors import BusyError
    from magicite.storage import db as db_mod
    from magicite.storage import lease

    cfg = Config.load(Path(project_root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    conn = db_mod.connect(cfg.db_path)
    from magicite.core.writer_guard import registry_writer_lease

    candidate = registry_writer_lease(
        cfg,
        conn,
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
            # Real domain APIs (not bare assert_owned) — context var is set by try_acquire.
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
                evidence_mod.checkpoint(cfg, conn, event)
            elif domain == "policy":
                ps.register_evaluated(cfg, _policy_manifest("stale"), evaluation_status="pass")
            elif domain == "trust":
                trust_mod.save_policy(cfg, trust_mod.default_policy())
            elif domain == "approvals":
                with lease.writer_lease(holder="stale-approvals"):
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


def _killed_holder(db_path: str, lock_path: str, token_path: str) -> None:
    """Acquire DB lease, write fencing token to a file, then spin until SIGKILL."""
    from magicite.storage import db as db_mod
    from magicite.storage import lease

    conn = db_mod.connect(db_path)
    candidate = lease.CrossProcessLease(
        lock_path=lock_path,
        conn=conn,
        holder="kill-holder",
        ttl_s=0.4,
    )
    acquired = candidate.try_acquire()
    Path(token_path).write_text(str(acquired.fencing_token), encoding="utf-8")
    while True:
        time.sleep(0.05)


@pytest.mark.acceptance
def test_killed_holder_lease_reclaimed(tmp_path: Path) -> None:
    """B6: SIGKILL a lease holder; after TTL the replacement reclaims the fence.

    Also verifies a resumed stale fencing token cannot commit through the real
    approvals domain API (kill + stale-token resume for at least one domain).
    """
    from magicite.config import Config
    from magicite.core import approvals as approvals_mod
    from magicite.errors import BusyError

    project = tmp_path / "kill-proj"
    project.mkdir()
    cfg = Config.load(project, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg.ensure_dirs()
    db_path = cfg.db_path
    token_path = tmp_path / "token.txt"
    seed = db_mod.connect(db_path)
    seed.close()

    ctx = _spawn_context()
    holder = ctx.Process(
        target=_killed_holder,
        args=(str(db_path), str(tmp_path / "kill.lock"), str(token_path)),
    )
    holder.start()
    replacement_conn = None
    replacement = None
    stale_token_ctx = None
    try:
        for _ in range(100):
            if token_path.is_file() and token_path.stat().st_size > 0:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("holder never published fencing token")
        token = int(token_path.read_text(encoding="utf-8"))
        assert holder.pid is not None
        os.kill(holder.pid, signal.SIGKILL)
        holder.join(timeout=5)
        assert not holder.is_alive()
        time.sleep(0.55)
        replacement_conn = db_mod.connect(db_path)
        replacement = lease.CrossProcessLease(
            lock_path=tmp_path / "repl.lock",
            conn=replacement_conn,
            holder="replacement",
            ttl_s=5.0,
        )
        result = replacement.try_acquire()
        assert result.fencing_token == token + 1
        assert result.stolen is True

        # Resume the killed holder's stale token and attempt a real approvals write.
        stale = lease.CrossProcessLease(
            lock_path=tmp_path / "stale-resume.lock",
            conn=replacement_conn,
            holder="kill-holder",
            ttl_s=5.0,
        )
        stale._held = True
        stale._fencing_token = token
        stale_token_ctx = lease._CROSS_PROCESS_LEASE.set(stale)  # noqa: SLF001
        with pytest.raises(BusyError):
            with lease.writer_lease(holder="stale-resume"):
                approvals_mod.propose(
                    replacement_conn,
                    cfg,
                    op="nucleate",
                    target_name="after-kill",
                    payload={"note": "must-not-land"},
                    proposed_by="stale",
                )
        assert not list(cfg.approvals_dir.glob("*.json"))
    finally:
        if stale_token_ctx is not None:
            lease._CROSS_PROCESS_LEASE.reset(stale_token_ctx)  # noqa: SLF001
        if holder.is_alive():
            try:
                os.kill(holder.pid, signal.SIGKILL)  # type: ignore[arg-type]
            except OSError:
                pass
            holder.join(timeout=5)
        if replacement is not None:
            replacement.release()
        if replacement_conn is not None:
            replacement_conn.close()


@pytest.mark.acceptance
@pytest.mark.parametrize("domain", ["evidence", "trust", "approvals", "policy"])
def test_stale_writer_cannot_commit_domain_stores(custody_for, tmp_path: Path, domain: str) -> None:
    """AC-S12-04: stale/killed lease holder cannot commit via evidence/trust/approvals.

    Includes the stable policy store after S07 integration.
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
    provider = custody_for(cfg)
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
            str(provider.store.directory),
            provider.registry_id,
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


def _policy_manifest(digest: str) -> Any:
    from magicite.core.policy_store import PolicyManifest

    return PolicyManifest(
        policy_id="dense-v1",
        policy_digest=digest,
        policy_family="stable",
        config_digest="config",
        calibration_digest=None,
        index_generation_id="g",
        snapshot_id="s",
        selection="cosine_similarity",
    )


def _policy_lock_holder(project_root: str, ready: Any, release: Any) -> None:
    from magicite.config import Config

    cfg = Config(project_root=Path(project_root))
    conn = db_mod.connect(cfg.db_path)
    try:
        with lease.CrossProcessLease(
            lock_path=cfg.dream_lock_path, conn=conn, holder="policy-blocker"
        ).acquire():
            ready.set()
            release.wait(timeout=15)
    finally:
        conn.close()


@pytest.mark.parametrize("operation", ["register", "approve", "activate", "rollback"])
def test_policy_mutators_obey_other_process_lease(custody_for, tmp_path: Path, operation: str) -> None:
    from magicite.config import Config
    from magicite.core import policy_store as ps

    cfg = Config(project_root=tmp_path)
    custody_for(cfg)
    cfg.ensure_dirs()
    db_mod.connect(cfg.db_path).close()
    ps.register_evaluated(cfg, _policy_manifest("a"), evaluation_status="pass")
    approval = ps.approve(cfg, "a", actor="fixture")
    before = ps.policy_store_path(cfg).read_bytes()
    ctx = _spawn_context()
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_policy_lock_holder, args=(str(tmp_path), ready, release))
    child.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(BusyError):
            if operation == "register":
                ps.register_evaluated(cfg, _policy_manifest("b"), evaluation_status="pass")
            elif operation == "approve":
                ps.approve(cfg, "a", actor="blocked")
            elif operation == "activate":
                ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
            else:
                ps.rollback(cfg, expected_current=None, prior_digest="a")
        assert ps.policy_store_path(cfg).read_bytes() == before
        assert not list(cfg.approvals_dir.glob("*.json"))
    finally:
        release.set()
        _join_cleanly([child])
    ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
    assert ps.status(cfg).active_digest == "a"


def _policy_cas_contender(project_root, digest, approval, ready, start, results, directory, registry_id):
    from tests.support.custody_adapter import attach_fixture

    with attach_fixture(Path(project_root), Path(directory), registry_id):
        _policy_cas_attached(project_root, digest, approval, ready, start, results)


def _policy_cas_attached(
    project_root: str, digest: str, approval: str, ready: Any, start: Any, results: Any
) -> None:
    from magicite.config import Config
    from magicite.core import policy_store as ps
    from magicite.errors import InvalidInputError

    cfg = Config(project_root=Path(project_root))
    ready.put(digest)
    start.wait(timeout=10)
    try:
        ps.activate(cfg, expected_current=None, candidate_digest=digest, approval_id=approval)
    except (BusyError, InvalidInputError):
        results.put((digest, "rejected"))
    else:
        results.put((digest, "committed"))


def test_policy_concurrent_cas_has_one_winner(custody_for, tmp_path: Path) -> None:
    from magicite.config import Config
    from magicite.core import policy_store as ps
    from magicite.errors import InvalidInputError

    cfg = Config(project_root=tmp_path)
    provider = custody_for(cfg)
    approvals = {}
    for digest in ("a", "b"):
        ps.register_evaluated(cfg, _policy_manifest(digest), evaluation_status="pass")
        approvals[digest] = ps.approve(cfg, digest, actor="fixture")
    ctx = _spawn_context()
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    children = [
        ctx.Process(
            target=_policy_cas_contender,
            args=(
                str(tmp_path),
                d,
                approvals[d],
                ready,
                start,
                results,
                str(provider.store.directory),
                provider.registry_id,
            ),
        )
        for d in approvals
    ]
    for child in children:
        child.start()
    try:
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {"a", "b"}
        start.set()
        outcomes = dict([results.get(timeout=10), results.get(timeout=10)])
        assert sorted(outcomes.values()) == ["committed", "rejected"]
        winner = next(d for d, outcome in outcomes.items() if outcome == "committed")
        loser = next(d for d, outcome in outcomes.items() if outcome == "rejected")
        assert ps.status(cfg).active_digest == winner
        with pytest.raises(InvalidInputError, match="stale expected_current"):
            ps.activate(cfg, expected_current=None, candidate_digest=loser, approval_id=approvals[loser])
    finally:
        start.set()
        _join_cleanly(children)
