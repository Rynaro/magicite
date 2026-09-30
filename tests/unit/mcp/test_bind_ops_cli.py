"""S11 CLI wrappers for trust/policy/evidence/backup/doctor."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.core import registry as registry_mod
from magicite.mcp import bind_ops


def test_doctor_cli_reports_reconciliation_field(project_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor", "--project-root", str(project_root)],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["kind"] == "doctor/1" or "reconciliation_required" in report
    assert "reconciliation_required" in report


def test_trust_list_cli_empty(project_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["trust", "list", "--project-root", str(project_root)],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["decisions"] == []


def test_trust_approve_requires_actor(project_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "trust",
            "approve",
            "--project-root",
            str(project_root),
            "--engram-id",
            "x",
            "--expected-digest",
            "0" * 64,
        ],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    assert result.exit_code != 0


def test_policy_status_cli(project_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["policy", "status", "--project-root", str(project_root)],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "active_digest" in payload
    assert "records" in payload


def test_evidence_retention_status_carries_copy_notice(project_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["evidence", "retention-status", "--project-root", str(project_root)],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "privacy_deletion_notice" in payload
    assert "cannot be followed" in payload["privacy_deletion_notice"].lower() or (
        "cannot" in payload["privacy_deletion_notice"].lower()
    )


def test_backup_status_surfaces_reconciliation(project_root: Path) -> None:
    status = bind_ops.backup_status(project_root)
    assert "reconciliation_required" in status
    assert status["reconciliation_required"] is False


def test_bind_ops_trust_review_note_does_not_imply_approval(
    cfg, db_conn, embedder
) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    row = db_conn.execute("SELECT id FROM engram LIMIT 1").fetchone()
    view = bind_ops.trust_review(cfg.project_root, engram_id=row["id"])
    assert "explicit operator" in view["note"].lower()
    assert view["admitted"] in {True, False}
