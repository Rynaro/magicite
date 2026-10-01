"""Startup errors must not put non-protocol bytes on the MCP stdout stream."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.config import Config
from magicite.errors import MagiciteError


@pytest.mark.parametrize("exception", [PermissionError, RuntimeError, MagiciteError])
def test_serve_startup_error_is_redacted_stderr(monkeypatch, exception):
    secret = "/private/operator/SECRET_SENTINEL"

    def fail_load(*args, **kwargs):
        raise exception(secret)

    monkeypatch.setattr(Config, "load", fail_load)
    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "startup failed" in result.stderr
    assert secret not in result.stderr
    assert "Traceback" not in result.stderr
    if exception is PermissionError:
        assert "PermissionError" in result.stderr


def test_operator_error_remains_json_stdout(monkeypatch):
    def fail_load(*args, **kwargs):
        raise PermissionError("/private/SECRET_SENTINEL")

    monkeypatch.setattr(Config, "load", fail_load)
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "internal"
    assert result.stderr == ""
    assert "SECRET_SENTINEL" not in result.stdout


@pytest.mark.parametrize(
    "command", [["serve"], ["sync"], ["dream", "--once"], ["export", "--out-dir", "out"], ["trust", "list"]]
)
def test_stateful_entrypoint_missing_custody_is_zero_write(tmp_path, command):
    result = CliRunner().invoke(cli, [*command, "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert list(tmp_path.iterdir()) == []


def test_build_state_unavailable_custody_is_zero_write(tmp_path, monkeypatch):
    from magicite.core import writer_guard
    from magicite.core.trust_custodian import CustodianError
    from magicite.mcp.app import build_state

    class Unavailable:
        def call(self, operation, **arguments):
            raise CustodianError("unavailable custody")

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Unavailable()))
    with pytest.raises(CustodianError):
        build_state(Config(project_root=tmp_path))
    assert list(tmp_path.iterdir()) == []


def _all_command_paths(group=cli, prefix=()):
    paths = []
    for name, command in sorted(group.commands.items()):
        path = (*prefix, name)
        paths.append(path)
        if hasattr(command, "commands"):
            paths.extend(_all_command_paths(command, path))
    return paths


@pytest.mark.parametrize("path", _all_command_paths(), ids=" ".join)
def test_help_exits_zero_without_internal_error(path):
    result = CliRunner().invoke(cli, [*path, "--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "internal command error" not in result.output
