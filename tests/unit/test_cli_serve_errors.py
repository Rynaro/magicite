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
