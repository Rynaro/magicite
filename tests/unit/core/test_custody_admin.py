"""CLI mechanics under explicitly injected filesystem protection, not deployment proof."""

import hashlib
import json
import os

from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.core.trust import default_policy


def test_custody_admin_init_enroll_profile_explicit_only(tmp_path, monkeypatch):
    from magicite.core import custody_admin

    monkeypatch.setattr(custody_admin, "protected_path", lambda *args, **kwargs: None)
    runner = CliRunner()
    directory = tmp_path / "custody"
    result = runner.invoke(cli, ["custody", "init", "--directory", str(directory)])
    assert result.exit_code == 0, result.output
    policy = tmp_path / "policy.json"
    raw = json.dumps(default_policy().to_dict()).encode()
    policy.write_bytes(raw)
    arguments = [
        "custody",
        "enroll",
        "--directory",
        str(directory),
        "--registry-id",
        "r",
        "--policy",
        str(policy),
        "--actor",
        "operator",
        "--reviewed-sha256",
        hashlib.sha256(raw).hexdigest(),
    ]
    result = runner.invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert runner.invoke(cli, arguments).exit_code != 0  # never reset enrollment
    profile = tmp_path / "profile.json"
    result = runner.invoke(
        cli,
        [
            "custody",
            "profile",
            "--directory",
            str(directory),
            "--registry-id",
            "r",
            "--project-root",
            str(tmp_path),
            "--client-uid",
            str(os.getuid() + 10000),
            "--socket-path",
            str(tmp_path / "custody.sock"),
            "--profile-path",
            str(profile),
        ],
    )
    assert result.exit_code == 0, result.output
    descriptor = json.loads(result.output)
    assert descriptor["schema"] == "CustodyEnrollment/1"
    assert descriptor["registry_id"] == "r"
    assert json.loads(profile.read_bytes())["state"] == "ACTIVE"
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path / ".magicite").exists()  # does not mutate application or enroll implicitly


def test_custody_enroll_rejects_unreviewed_bytes(tmp_path, monkeypatch):
    from magicite.core import custody_admin

    monkeypatch.setattr(custody_admin, "protected_path", lambda *args, **kwargs: None)
    runner = CliRunner()
    directory = tmp_path / "custody"
    assert runner.invoke(cli, ["custody", "init", "--directory", str(directory)]).exit_code == 0
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(default_policy().to_dict()))
    result = runner.invoke(
        cli,
        [
            "custody",
            "enroll",
            "--directory",
            str(directory),
            "--registry-id",
            "r",
            "--policy",
            str(policy),
            "--actor",
            "operator",
            "--reviewed-sha256",
            "0" * 64,
        ],
    )
    assert result.exit_code != 0
    import pytest

    from magicite.core.trust_custodian import CustodianError, CustodianStore

    store = CustodianStore.open(directory)
    try:
        with pytest.raises(CustodianError):
            store.read_current("r")
    finally:
        store.close()


def test_normal_custody_status_is_failclosed_without_enrollment(tmp_path):
    result = CliRunner().invoke(cli, ["custody", "status", "--project-root", str(tmp_path)])
    assert result.exit_code != 0
    assert "reconciliation_required" in result.output
    assert not (tmp_path / ".magicite").exists()


def test_explicit_initialize_and_reconcile_use_authenticated_service_interface(tmp_path, monkeypatch):
    from magicite.config import Config
    from magicite.core import writer_guard
    from magicite.core.trust_custodian import CustodianStore

    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Adapter()))
    runner = CliRunner()
    try:
        result = runner.invoke(cli, ["custody", "initialize-journal", "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["state"] == "INITIALIZED"
        assert (
            runner.invoke(cli, ["custody", "initialize-journal", "--project-root", str(tmp_path)]).exit_code
            != 0
        )
        cfg = Config.load(tmp_path)
        (cfg.data_dir / "trust/authority/head.json").unlink()
        result = runner.invoke(cli, ["custody", "reconcile", "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["head_sequence"] == 1
    finally:
        store.close()
