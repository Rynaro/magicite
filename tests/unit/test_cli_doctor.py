"""``magicite doctor`` CLI wiring (spec M7): the command must actually run
(not raise ``ClickException`` the way it did pre-M7) and emit the
``obs/doctor.py`` report as JSON on stdout."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from magicite.__main__ import cli


def test_doctor_cli_emits_json_report(project_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor", "--project-root", str(project_root)],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )
    report = json.loads(result.stdout)
    assert result.exit_code == (0 if report["healthy"] else 1), result.output
    assert "warnings" in report
    assert "cold_start" in report
    assert "filesystem" in report
    assert "registry" in report
    assert "embedding" in report


def test_doctor_cli_unhealthy_exits_nonzero(project_root: Path, monkeypatch) -> None:
    from magicite.mcp import bind_ops

    monkeypatch.setattr(
        bind_ops,
        "doctor_report",
        lambda _root: {"healthy": False, "warnings": ["registry unreadable"]},
    )
    result = CliRunner().invoke(cli, ["doctor", "--project-root", str(project_root)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["healthy"] is False
    assert "WARNING: registry unreadable" in result.stderr
