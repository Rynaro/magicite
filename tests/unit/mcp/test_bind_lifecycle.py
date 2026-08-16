"""``mcp/bind_lifecycle.py``: ``sharpen``/``promote``/``archive`` tool
bodies (spec §3.3 tools 14-16, §5.1 FSM, §5.4 sharpen execution)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from magicite.core import approvals as approvals_mod
from magicite.core import registry as registry_mod
from magicite.errors import ErrorCode, MagiciteError
from magicite.mcp import bind_lifecycle
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import PromoteInput, SharpenChanges, SharpenInput

PROTON = "proton-ge-proton-downgrade"


def _ctx(cfg, conn, embedder) -> ToolContext:
    return ToolContext(cfg=cfg, conn=conn, embedder=embedder)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── promote(): nascent -> probation (the rubric/injection-scan branch) ──


def test_promote_nascent_to_probation_review_mode_creates_a_proposal(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = _ctx(cfg, db_conn, embedder)

    result = bind_lifecycle.promote(ctx, PromoteInput(name=PROTON))

    assert result.promoted is False
    assert result.requires_approval is True
    assert result.state == "proposed"
    assert result.evidence["rubric_score"] == 12  # the toy fixture is full-marks (see test_fitness.py)

    row = db_conn.execute("SELECT status FROM engram WHERE name = ?", (PROTON,)).fetchone()
    assert row["status"] == "nascent"  # unchanged


def test_promote_nascent_to_probation_autonomous_mode_executes(cfg, db_conn, embedder) -> None:
    cfg.autonomous = True
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = _ctx(cfg, db_conn, embedder)

    result = bind_lifecycle.promote(ctx, PromoteInput(name=PROTON))

    assert result.promoted is True
    assert result.new_status == "probation"
    assert result.state == "succeeded"

    row = db_conn.execute("SELECT status FROM engram WHERE name = ?", (PROTON,)).fetchone()
    assert row["status"] == "probation"
    journal = db_conn.execute(
        "SELECT event FROM engram_journal WHERE engram_id = "
        "(SELECT id FROM engram WHERE name = ?) ORDER BY ts DESC LIMIT 1",
        (PROTON,),
    ).fetchone()
    assert journal["event"] == "promoted"


# ── promote(): AC-016, the evidence-bar denial ───────────────────────────


def test_promote_denied_below_evidence_bar_end_to_end(cfg, db_conn, embedder) -> None:
    """AC-016, exercised through the real tool body (not just the pure
    lifecycle.apply() slice tests/unit/core/test_lifecycle.py already
    covers): S=0.4, pass_rate=0.8 -- both below the probation->consolidated
    bar -- must deny, naming the unmet guards, leaving status unchanged."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    db_conn.execute(
        "UPDATE engram SET status = 'probation', storage_strength = 0.4, peak_storage_strength = 0.4, "
        "success_count = 4, failure_count = 1 WHERE id = ?",
        (engram_id,),
    )
    ctx = _ctx(cfg, db_conn, embedder)

    with pytest.raises(MagiciteError) as excinfo:
        bind_lifecycle.promote(ctx, PromoteInput(name=PROTON))
    assert excinfo.value.code == ErrorCode.TRANSITION_DENIED
    assert any("storage_strength" in u for u in excinfo.value.unmet)
    assert any("pass_rate" in u for u in excinfo.value.unmet)

    row = db_conn.execute("SELECT status FROM engram WHERE id = ?", (engram_id,)).fetchone()
    assert row["status"] == "probation"
    assert db_conn.execute("SELECT COUNT(*) AS n FROM approval").fetchone()["n"] == 0


def test_promote_probation_to_consolidated_when_evidence_clears(cfg, db_conn, embedder) -> None:
    cfg.autonomous = True
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    db_conn.execute(
        "UPDATE engram SET status = 'probation', storage_strength = 0.7, peak_storage_strength = 0.7, "
        "success_count = 9, failure_count = 1 WHERE id = ?",
        (engram_id,),
    )
    now = datetime.now(UTC)
    for i, sid in enumerate(["s1", "s2", "s3"]):
        db_conn.execute(
            "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, payload_json) "
            "VALUES (?, ?, 'signal_outcome', 1, ?, '{}')",
            (_iso(now - timedelta(minutes=i)), sid, engram_id),
        )
    ctx = _ctx(cfg, db_conn, embedder)

    result = bind_lifecycle.promote(ctx, PromoteInput(name=PROTON))

    assert result.promoted is True
    assert result.new_status == "consolidated"


# ── promote(): quarantine (any -> quarantined outranks the status guard) ──


def test_promote_quarantines_on_injection_scan_hit(cfg, db_conn, embedder, tmp_path) -> None:
    skills_dir = cfg.project_root / "skills" / "risky"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: risky\ndescription: |\n  Does a risky thing.\n---\n\n"
        "## Procedure\n1. Run:\n\n```bash\nrm -rf /\n```\n"
    )
    outcome = registry_mod.register(cfg, db_conn, embedder, path="skills", fmt="skill")
    assert outcome.registered[0].verification_status == "quarantined"

    # Even a caller who did not yet know about the quarantine (e.g. a
    # stale client) gets QuarantinedError, not a bare transition_denied,
    # when they try to promote it -- and the DB row already reflects it.
    ctx = _ctx(cfg, db_conn, embedder)
    with pytest.raises(MagiciteError) as excinfo:
        bind_lifecycle.promote(ctx, PromoteInput(name="risky"))
    assert excinfo.value.code == ErrorCode.QUARANTINED

    row = db_conn.execute("SELECT verification_status FROM engram WHERE name = 'risky'").fetchone()
    assert row["verification_status"] == "quarantined"


# ── promote(): revival is manual, always ─────────────────────────────────


def test_promote_revival_never_auto_executes_even_under_autonomous_mode(cfg, db_conn, embedder) -> None:
    cfg.autonomous = True
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    db_conn.execute("UPDATE engram SET status = 'archived' WHERE id = ?", (engram_id,))
    now = datetime.now(UTC)
    for i, sid in enumerate(["s1", "s2", "s3"]):
        db_conn.execute(
            "INSERT INTO eph_event (ts, session_id, tool, signal_tier, engram_id, payload_json) "
            "VALUES (?, ?, 'route', 0, ?, '{}')",
            (_iso(now + timedelta(minutes=i)), sid, engram_id),
        )
    ctx = _ctx(cfg, db_conn, embedder)

    result = bind_lifecycle.promote(ctx, PromoteInput(name=PROTON))

    assert result.promoted is False
    assert result.requires_approval is True
    assert result.state == "proposed"  # NOT auto-approved despite cfg.autonomous
    row = db_conn.execute("SELECT status FROM engram WHERE id = ?", (engram_id,)).fetchone()
    assert row["status"] == "archived"


def test_promote_not_found(cfg, db_conn, embedder) -> None:
    ctx = _ctx(cfg, db_conn, embedder)
    with pytest.raises(MagiciteError) as excinfo:
        bind_lifecycle.promote(ctx, PromoteInput(name="does-not-exist"))
    assert excinfo.value.code == ErrorCode.NOT_FOUND


# ── sharpen() ─────────────────────────────────────────────────────────────


def test_sharpen_review_mode_creates_a_proposal_and_does_not_touch_the_file(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    file_path = cfg.registry_dir / f"{PROTON}.egr.md"
    original = file_path.read_text(encoding="utf-8")
    ctx = _ctx(cfg, db_conn, embedder)

    result = bind_lifecycle.sharpen(
        ctx, SharpenInput(name=PROTON, proposed_changes=SharpenChanges(triggers=["a new trigger phrase"]))
    )

    assert result.sharpened is False
    assert result.requires_approval is True
    assert result.state == "proposed"
    assert file_path.read_text(encoding="utf-8") == original


def test_sharpen_autonomous_mode_applies_the_patch_and_bumps_version(cfg, db_conn, embedder) -> None:
    cfg.autonomous = True
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = _ctx(cfg, db_conn, embedder)

    result = bind_lifecycle.sharpen(
        ctx,
        SharpenInput(
            name=PROTON,
            proposed_changes=SharpenChanges(
                triggers=["a genuinely new trigger phrase"], pitfalls=["a newly observed pitfall"]
            ),
        ),
    )

    assert result.sharpened is True
    assert result.version_bumped == "2"
    assert result.state == "succeeded"

    row = db_conn.execute("SELECT version FROM engram WHERE name = ?", (PROTON,)).fetchone()
    assert row["version"] == 2

    file_path = cfg.registry_dir / f"{PROTON}.egr.md"
    text = file_path.read_text(encoding="utf-8")
    assert "a genuinely new trigger phrase" in text
    assert "a newly observed pitfall" in text
    assert "version: 2" in text


def test_sharpen_lint_failure_leaves_the_file_untouched(cfg, db_conn, embedder) -> None:
    """spec §5.4: "fail -> approval state 'failed', file untouched...
    nothing is half-applied". A SKILL.md-derived (import-profile, still
    routable-gapped) engram is not the easiest way to force a strict
    failure post-patch since sharpen keeps the same profile the file
    already had; instead this proves the *contract* by monkeypatching
    the lint call to fail deterministically -- exercising the failure
    path without depending on a specific patch shape happening to break
    strict lint."""
    cfg.autonomous = True
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    file_path = cfg.registry_dir / f"{PROTON}.egr.md"
    original = file_path.read_text(encoding="utf-8")

    from magicite.core import lifecycle as lifecycle_mod
    from magicite.engram import lint as lint_mod

    def _always_fail(engram, profile="strict"):  # noqa: ANN001, ANN201
        result = lint_mod.lint(engram, profile=profile)
        result.issues.append(lint_mod.LintIssue("forced", "forced failure for the test", "error"))
        return result

    orig_lint = lint_mod.lint
    lifecycle_mod.lint_mod.lint = _always_fail  # type: ignore[attr-defined]
    try:
        ctx = _ctx(cfg, db_conn, embedder)
        result = bind_lifecycle.sharpen(
            ctx, SharpenInput(name=PROTON, proposed_changes=SharpenChanges(triggers=["irrelevant"]))
        )
    finally:
        lifecycle_mod.lint_mod.lint = orig_lint

    assert result.sharpened is False
    assert result.state == "failed"
    assert file_path.read_text(encoding="utf-8") == original

    approval = approvals_mod.get(db_conn, result.approval_id)
    assert approval is not None
    assert approval.state == "failed"
