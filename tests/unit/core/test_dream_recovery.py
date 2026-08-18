"""Frozen AC-001/AC-003 recovery and fencing anchors."""

from __future__ import annotations

import uuid

import pytest

from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
from magicite.errors import BusyError
from magicite.storage import lease as lease_mod


class _ProcessDeath(BaseException):
    """Bypass Dream's ordinary Exception handler like abrupt process exit."""


@pytest.fixture
def registered(cfg, db_conn, embedder) -> None:
    assert registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams").ingested == 7


@pytest.mark.parametrize(
    "committed_phase,next_phase",
    [
        ("replay", "potentiate"),
        ("potentiate", "decay"),
        ("decay", "renormalise"),
        ("renormalise", "distill"),
        ("distill", "audit"),
        ("audit", "checkpoint"),
    ],
)
def test_resume_skips_every_committed_phase(
    cfg, db_conn, registered, committed_phase: str, next_phase: str
) -> None:
    signals_mod.signal_use(
        cfg,
        db_conn,
        skill_ids=["proton-ge-proton-downgrade"],
        session_id=f"resume-{committed_phase}",
    )
    signals_mod.signal_outcome(
        cfg,
        db_conn,
        valence=0.9,
        salience=0.9,
        skill_ids=["proton-ge-proton-downgrade"],
        session_id=f"resume-{committed_phase}",
    )
    queued = dream_mod.enqueue(db_conn, cfg, trigger="test")
    assert queued.consolidation_id is not None

    def stop(label: str) -> None:
        if label == f"phase:{committed_phase}:committed":
            raise _ProcessDeath

    with pytest.raises(_ProcessDeath):
        dream_mod.run(cfg, db_conn, trigger="test", fault_hook=stop)

    interrupted = db_conn.execute(
        "SELECT state, phase FROM consolidation_run WHERE id = ?",
        (queued.consolidation_id,),
    ).fetchone()
    assert (interrupted["state"], interrupted["phase"]) == ("running", next_phase)

    resumed = dream_mod.run(cfg, db_conn, trigger="test")
    assert resumed.run_id == queued.consolidation_id
    assert resumed.state == "succeeded"
    consumed = db_conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag WHERE consumed_run_id = ?",
        (queued.consolidation_id,),
    ).fetchone()["n"]
    assert consumed == 1


def test_failed_phase_resumes_the_same_consolidation(cfg, db_conn, registered) -> None:
    queued = dream_mod.enqueue(db_conn, cfg, trigger="test")

    def stop(label: str) -> None:
        if label == "phase:renormalise:after-write":
            raise RuntimeError("phase fault")

    with pytest.raises(RuntimeError, match="phase fault"):
        dream_mod.run(cfg, db_conn, trigger="test", fault_hook=stop)

    failed = db_conn.execute(
        "SELECT state, phase FROM consolidation_run WHERE id = ?", (queued.consolidation_id,)
    ).fetchone()
    assert (failed["state"], failed["phase"]) == ("failed", "renormalise")
    assert dream_mod.run(cfg, db_conn, trigger="test").run_id == queued.consolidation_id


def test_lost_lease_fences_writes(cfg, db_conn) -> None:
    lease = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path,
        conn=db_conn,
        holder=f"test:{uuid.uuid4().hex}",
    )
    with lease.acquire():
        db_conn.execute("UPDATE writer_lease SET fencing_token = fencing_token + 1 WHERE id = 1")
        with pytest.raises(BusyError, match="ownership was lost"):
            dream_mod._guarded_execute(
                lease,
                db_conn,
                "INSERT INTO consolidation_run (id, trigger, state) VALUES ('forbidden', 'test', 'queued')",
            )
    assert db_conn.execute("SELECT 1 FROM consolidation_run WHERE id = 'forbidden'").fetchone() is None
