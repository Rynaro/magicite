"""CLI mechanics under explicitly injected filesystem protection, not deployment proof."""

import hashlib
import json
import os

import pytest
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


@pytest.mark.parametrize("command", ["initialize-journal", "reconcile"])
def test_valid_profile_unavailable_service_has_no_local_writes(tmp_path, monkeypatch, command):
    from magicite.core import writer_guard
    from magicite.core.trust_custodian import CustodianError

    class Unavailable:
        def call(self, *args, **kwargs):
            raise CustodianError("unavailable")

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Unavailable()))
    result = CliRunner().invoke(cli, ["custody", command, "--project-root", str(tmp_path)])
    assert result.exit_code != 0
    assert not (tmp_path / ".magicite").exists()


def test_genesis_lost_lease_cannot_create_local_journal(tmp_path):
    from magicite.core.trust_custodian import CustodianError, CustodianStore
    from magicite.core.trust_journal import TrustJournal

    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    def lost():
        raise CustodianError("lost lease")

    try:
        with pytest.raises(CustodianError):
            TrustJournal(tmp_path / "local", "r", Adapter()).initialize_reviewed_genesis(assert_owned=lost)
        assert not (tmp_path / "local").exists()
    finally:
        store.close()


@pytest.mark.parametrize("lost_at", ["enroll", "read_current", "emit"])
def test_enrollment_lost_reply_guidance_preserves_one_effect(tmp_path, monkeypatch, lost_at):
    from magicite.core import custody_admin
    from magicite.core.trust_custodian import CustodianError, CustodianStore

    monkeypatch.setattr(custody_admin, "protected_path", lambda *args, **kwargs: None)
    directory = tmp_path / "custody"
    store = CustodianStore.create(directory)
    store.close()
    raw = json.dumps(default_policy().to_dict()).encode()
    policy = tmp_path / "policy.json"
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
    original_enroll = CustodianStore.enroll
    original_read = CustodianStore.read_current
    secret = "private-enrollment-diagnostic-canary"

    def lost_enroll(self, *args, **kwargs):
        original_enroll(self, *args, **kwargs)
        raise CustodianError(secret)

    def unavailable_read(self, *args, **kwargs):
        raise CustodianError(secret)

    def lost_emit(value):
        raise OSError(secret)

    with monkeypatch.context() as fault:
        if lost_at == "enroll":
            fault.setattr(CustodianStore, "enroll", lost_enroll)
        elif lost_at == "read_current":
            fault.setattr(CustodianStore, "read_current", unavailable_read)
        else:
            fault.setattr(custody_admin, "_emit", lost_emit)
        result = CliRunner().invoke(cli, arguments)
    assert result.exit_code != 0
    assert "reconciliation_required" in result.output
    assert "custody status" in result.output
    assert "read-only" in result.output
    assert "may already be committed" in result.output
    assert secret not in result.output and str(directory) not in result.output
    store = CustodianStore.open(directory)
    try:
        head = original_read(store, "r")
        history = store.committed_records("r")
        assert head["head_sequence"] == 1 and len(history) == 1
        retry = CliRunner().invoke(cli, arguments)
        assert retry.exit_code != 0 and "custody status" in retry.output
        assert original_read(store, "r") == head
        assert store.committed_records("r") == history
    finally:
        store.close()


def test_enrollment_retry_guidance_does_not_infer_genesis_from_current_policy(tmp_path, monkeypatch):
    from magicite.core import custody_admin
    from magicite.core.trust_custodian import CustodianStore

    monkeypatch.setattr(custody_admin, "protected_path", lambda *args, **kwargs: None)
    directory = tmp_path / "custody"
    store = CustodianStore.create(directory)
    policy_value = default_policy().to_dict()
    store.enroll("r", policy_value, actor="original-operator", reviewed=True)
    head = store.read_current("r")
    store.register_fence("r", predecessor=head, attempt_id="advanced", holder="holder", local_token=1)
    before = store.read_current("r")
    history = store.committed_records("r")
    store.close()
    policy = tmp_path / "policy.json"
    raw = json.dumps(policy_value).encode()
    policy.write_bytes(raw)
    result = CliRunner().invoke(
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
            "different-operator",
            "--reviewed-sha256",
            hashlib.sha256(raw).hexdigest(),
        ],
    )
    assert result.exit_code != 0
    assert "does not prove the original enrollment" in result.output
    assert "custody status" in result.output
    store = CustodianStore.open(directory)
    try:
        assert store.read_current("r") == before
        assert store.committed_records("r") == history
    finally:
        store.close()
