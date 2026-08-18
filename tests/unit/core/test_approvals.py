"""``core/approvals.py``: the docs/06 approval state machine (spec §5.2),
AC-027 (R3 requires approval by default), and AC-030 (auditable resume)."""

from __future__ import annotations

import json

import pytest

from magicite.core import approvals as approvals_mod
from magicite.core import registry as registry_mod
from magicite.errors import ErrorCode
from magicite.mcp import bind_lifecycle
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import ArchiveInput

PROTON = "proton-ge-proton-downgrade"


# ── the approval state machine itself ────────────────────────────────────


def test_propose_writes_the_db_row_and_the_json_mirror(cfg, db_conn) -> None:
    record = approvals_mod.propose(
        db_conn, cfg, op="archive", target_name=PROTON, payload={"reason": "test"}, proposed_by="tester"
    )
    assert record.state == "proposed"

    row = db_conn.execute("SELECT * FROM approval WHERE id = ?", (record.id,)).fetchone()
    assert row is not None
    assert row["state"] == "proposed"
    assert row["op"] == "archive"

    mirror_path = cfg.approvals_dir / f"{record.id}.json"
    assert mirror_path.is_file()
    mirrored = json.loads(mirror_path.read_text(encoding="utf-8"))
    assert mirrored["state"] == "proposed"
    assert mirrored["payload"] == {"reason": "test"}


def test_decide_approve_then_execute_then_outcome_legal_sequence(cfg, db_conn) -> None:
    record = approvals_mod.propose(
        db_conn, cfg, op="promote", target_name=PROTON, payload={}, proposed_by="tester"
    )
    approved = approvals_mod.decide(db_conn, cfg, approval_id=record.id, approve=True, decided_by="a-human")
    assert approved.state == "approved"
    assert approved.decided_by == "a-human"

    executed = approvals_mod.mark_executed(db_conn, cfg, approval_id=record.id)
    assert executed.state == "executed"

    outcome = approvals_mod.mark_outcome(db_conn, cfg, approval_id=record.id, succeeded=True)
    assert outcome.state == "succeeded"


def test_decide_and_resume_transitions(cfg, db_conn) -> None:
    """AC-030: approve/deny/resume are durable, actor-attributed operations."""
    denied_record = approvals_mod.propose(
        db_conn, cfg, op="archive", target_name="reject-me", payload={}, proposed_by="requester"
    )
    denied = approvals_mod.decide(
        db_conn,
        cfg,
        approval_id=denied_record.id,
        approve=False,
        decided_by="reviewer",
        reason="insufficient evidence",
    )
    assert denied.state == "rejected"
    assert [event.operation for event in denied.audit_log] == ["propose", "deny"]
    assert denied.audit_log[-1].actor == "reviewer"

    approved_record = approvals_mod.propose(
        db_conn,
        cfg,
        op="archive",
        target_name="resume-me",
        payload={"reason": "retired"},
        proposed_by="requester",
    )
    approved = approvals_mod.decide(
        db_conn, cfg, approval_id=approved_record.id, approve=True, decided_by="reviewer"
    )

    executed: list[str] = []
    resumed = approvals_mod.resume(
        db_conn,
        cfg,
        approval_id=approved.id,
        resumed_by="operator",
        executor=lambda proposal: executed.append(proposal.target_name),
    )

    assert executed == ["resume-me"]
    assert resumed.state == "succeeded"
    assert [event.operation for event in resumed.audit_log] == [
        "propose",
        "approve",
        "resume",
        "succeed",
    ]
    assert resumed.audit_log[-2].actor == "operator"

    mirror_path = cfg.approvals_dir / f"{approved.id}.json"
    mirrored = json.loads(mirror_path.read_text(encoding="utf-8"))
    assert [event["operation"] for event in mirrored["audit_log"]] == [
        "propose",
        "approve",
        "resume",
        "succeed",
    ]

    # The DB row is independently queryable and retains the same append-only
    # audit sequence; it is not reconstructed from the latest state alone.
    restored = approvals_mod.get(db_conn, approved.id)
    assert restored is not None
    assert restored.audit_log == resumed.audit_log


def test_resume_records_executor_failure_and_cannot_repeat(cfg, db_conn) -> None:
    record = approvals_mod.propose(
        db_conn, cfg, op="sharpen", target_name=PROTON, payload={}, proposed_by="requester"
    )
    approvals_mod.decide(db_conn, cfg, approval_id=record.id, approve=True, decided_by="reviewer")

    def fail(_proposal):  # noqa: ANN001, ANN202
        raise RuntimeError("controlled executor failure")

    failed = approvals_mod.resume(db_conn, cfg, approval_id=record.id, resumed_by="operator", executor=fail)
    assert failed.state == "failed"
    assert failed.reason == "controlled executor failure"
    assert [event.operation for event in failed.audit_log][-2:] == ["resume", "fail"]

    with pytest.raises(ValueError, match="is not legal"):
        approvals_mod.resume(
            db_conn,
            cfg,
            approval_id=record.id,
            resumed_by="operator",
            executor=lambda _proposal: None,
        )


def test_decide_reject_is_terminal(cfg, db_conn) -> None:
    record = approvals_mod.propose(
        db_conn, cfg, op="archive", target_name=PROTON, payload={}, proposed_by="tester"
    )
    rejected = approvals_mod.decide(
        db_conn, cfg, approval_id=record.id, approve=False, decided_by="a-human", reason="not yet"
    )
    assert rejected.state == "rejected"
    assert rejected.reason == "not yet"

    with pytest.raises(ValueError):
        approvals_mod.mark_executed(db_conn, cfg, approval_id=record.id)


def test_reload_from_mirror_repopulates_a_rebuilt_db(cfg, db_conn) -> None:
    """spec §5.2: "durable outside the rebuildable DB... reloaded on
    sync()" -- simulates the DB-deleted-then-rebuilt scenario AC-009 tests
    for engram/edge state, but for approvals."""
    record = approvals_mod.propose(
        db_conn, cfg, op="sharpen", target_name=PROTON, payload={"x": 1}, proposed_by="tester"
    )
    # Simulate a fresh (rebuilt) DB: the approval table has no row for
    # this id, even though the JSON mirror still does.
    db_conn.execute("DELETE FROM approval WHERE id = ?", (record.id,))
    assert approvals_mod.get(db_conn, record.id) is None

    reloaded = approvals_mod.reload_from_mirror(cfg, db_conn)
    assert reloaded >= 1
    restored = approvals_mod.get(db_conn, record.id)
    assert restored is not None
    assert restored.state == "proposed"
    assert restored.payload == {"x": 1}


# ── AC-027 ────────────────────────────────────────────────────────────────


def test_r3_requires_approval_by_default(cfg, db_conn, embedder) -> None:
    """AC-027: GIVEN review mode (the default) WHEN archive(name=...) is
    called THEN the tool SHALL create an approval in state proposed
    without mutating the engram."""
    assert cfg.autonomous is False  # review mode is the default (docs/06)
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    before = db_conn.execute("SELECT status FROM engram WHERE name = ?", (PROTON,)).fetchone()

    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    result = bind_lifecycle.archive(ctx, ArchiveInput(name=PROTON, reason="testing AC-027"))

    assert result.archived is False
    assert result.requires_approval is True
    assert result.state == "proposed"
    assert result.approval_id is not None

    approval = approvals_mod.get(db_conn, result.approval_id)
    assert approval is not None
    assert approval.state == "proposed"
    assert approval.op == "archive"
    assert approval.target_name == PROTON

    after = db_conn.execute("SELECT status FROM engram WHERE name = ?", (PROTON,)).fetchone()
    assert after["status"] == before["status"], "the engram must not be mutated in review mode"

    file_path = cfg.registry_dir / f"{PROTON}.egr.md"
    assert file_path.is_file(), "the file must not have been moved in review mode"


def test_reviewed_archive_can_be_resumed_without_reproposing(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    proposed = bind_lifecycle.archive(ctx, ArchiveInput(name=PROTON, reason="reviewed retirement"))
    assert proposed.approval_id is not None
    approvals_mod.decide(
        db_conn,
        cfg,
        approval_id=proposed.approval_id,
        approve=True,
        decided_by="reviewer",
    )

    resumed = bind_lifecycle.resume_approved(ctx, approval_id=proposed.approval_id, resumed_by="operator")

    assert resumed.state == "succeeded"
    assert [event.operation for event in resumed.audit_log] == [
        "propose",
        "approve",
        "resume",
        "succeed",
    ]
    row = db_conn.execute("SELECT status FROM engram WHERE name = ?", (PROTON,)).fetchone()
    assert row["status"] == "archived"
    assert db_conn.execute("SELECT COUNT(*) AS n FROM approval").fetchone()["n"] == 1


def test_r3_autonomous_mode_auto_executes_and_records_the_actor(cfg, db_conn, embedder) -> None:
    """docs/06 §Autonomous Mode: "R3 ops execute directly without review...
    Audit trail: All ops logged with actor (human, dream-worker,
    autonomous-mode)". Opt-in only -- `cfg.autonomous` defaults False."""
    cfg.autonomous = True
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    result = bind_lifecycle.archive(ctx, ArchiveInput(name=PROTON, reason="autonomous test"))

    assert result.archived is True
    assert result.requires_approval is False
    assert result.state == "succeeded"

    approval = approvals_mod.get(db_conn, result.approval_id)
    assert approval is not None
    assert approval.state == "succeeded"
    assert approval.decided_by == approvals_mod.AUTONOMOUS_ACTOR

    row = db_conn.execute("SELECT status FROM engram WHERE name = ?", (PROTON,)).fetchone()
    assert row["status"] == "archived"


def test_archive_not_found_never_creates_a_proposal(cfg, db_conn, embedder) -> None:
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    result = bind_lifecycle.archive
    from magicite.errors import MagiciteError

    with pytest.raises(MagiciteError) as excinfo:
        result(ctx, ArchiveInput(name="does-not-exist"))
    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert db_conn.execute("SELECT COUNT(*) AS n FROM approval").fetchone()["n"] == 0
