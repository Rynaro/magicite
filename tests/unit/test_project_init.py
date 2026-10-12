"""Frozen project-init criteria: host-file ownership, preflight and explicit consent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from magicite import project_init as init
from magicite.__main__ import cli

COMMAND = Path("/installed/venv/bin/magicite")


@pytest.mark.parametrize(
    "raw", ["[]", "{", '{"mcpServers": []}', '{"x":1,"x":2}', '{"mcpServers":{"other":{"x":1,"x":2}}}']
)
def test_invalid_json_changes_nothing(tmp_path, raw):
    (tmp_path / ".mcp.json").write_text(raw)
    with pytest.raises(click.ClickException):
        init.prepare(tmp_path, COMMAND)
    assert (tmp_path / ".mcp.json").read_text() == raw
    assert not (tmp_path / "CLAUDE.md").exists()


@pytest.mark.parametrize("text", [init.START, init.END, init.END + init.START, init.BLOCK + init.BLOCK])
def test_malformed_markers_changes_nothing(tmp_path, text):
    (tmp_path / "CLAUDE.md").write_text(text)
    with pytest.raises(click.ClickException):
        init.prepare(tmp_path, COMMAND)
    assert (tmp_path / "CLAUDE.md").read_text() == text
    assert not (tmp_path / ".mcp.json").exists()


def test_preservation_modes_private_backup_and_idempotence(tmp_path):
    mcp = tmp_path / ".mcp.json"
    original = b'{"mcpServers":{"other":{"command":"private","env":{"SECRET":"hidden"}}},"extra":3}'
    mcp.write_bytes(original)
    mcp.chmod(0o640)
    before = b"External instructions\r\nKeep this exactly.\r\n"
    after = b"\r\nOutside suffix\r\n"
    (tmp_path / "CLAUDE.md").write_bytes(before + init.BLOCK.encode() + after)
    txn = init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND))
    txn.apply()
    config = json.loads(mcp.read_bytes())
    assert config["mcpServers"]["other"]["env"]["SECRET"] == "hidden"
    assert config["extra"] == 3
    assert config["mcpServers"]["magicite"]["args"] == ["serve", "--project-root", str(tmp_path)]
    assert (tmp_path / "CLAUDE.md").read_bytes() == before + init.BLOCK.encode() + after
    assert mcp.stat().st_mode & 0o777 == 0o640
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original and backups[0].stat().st_mode & 0o777 == 0o600
    snapshot = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND)).apply()
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == snapshot


def test_conflicting_server(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers":{"magicite":{"command":"other"}}}')
    with pytest.raises(click.ClickException, match="Conflicting"):
        init.prepare(tmp_path, COMMAND)


@pytest.mark.parametrize("target", [".mcp.json", "CLAUDE.md"])
def test_symlink_and_directory_targets(tmp_path, target):
    outside = tmp_path / "outside"
    outside.write_text("external")
    path = tmp_path / target
    path.symlink_to(outside)
    with pytest.raises(click.ClickException):
        init.prepare(tmp_path, COMMAND)
    assert outside.read_text() == "external"
    path.unlink()
    path.mkdir()
    with pytest.raises(click.ClickException):
        init.prepare(tmp_path, COMMAND)


@pytest.mark.parametrize("existing", [True, False])
def test_second_write_failure_recovers_only_owned_files(tmp_path, monkeypatch, existing):
    first = tmp_path / ".mcp.json"
    if existing:
        first.write_text("{}")
    before = first.read_bytes() if existing else None
    txn = init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND))
    replace = init._replace

    def fail_second(path, data, mode, expected):
        if path.name == "CLAUDE.md":
            raise OSError("injected write failure")
        return replace(path, data, mode, expected)

    monkeypatch.setattr(init, "_replace", fail_second)
    with pytest.raises(OSError):
        txn.apply()
    assert txn.rollback() == []
    assert first.read_bytes() == before if existing else not first.exists()
    assert not (tmp_path / "CLAUDE.md").exists()


@pytest.mark.parametrize("created", [True, False])
def test_concurrent_edit_is_preserved_during_recovery(tmp_path, created):
    path = tmp_path / ".mcp.json"
    if not created:
        path.write_text("{}")
    txn = init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND))
    txn.apply()
    path.write_text("concurrent external edit")
    assert txn.rollback() == [str(path)]
    assert path.read_text() == "concurrent external edit"
    assert not (tmp_path / "CLAUDE.md").exists()


def test_identity_change_same_bytes_prevents_rollback(tmp_path):
    txn = init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND))
    txn.apply()
    path = tmp_path / ".mcp.json"
    data = path.read_bytes()
    path.unlink()
    path.write_bytes(data)
    assert txn.rollback() == [str(path)]
    assert path.read_bytes() == data


def test_concurrent_edit_before_apply_and_backup_collision(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("{}")
    changes = init.prepare(tmp_path, COMMAND)
    path.write_text('{"external":true}')
    with pytest.raises(click.ClickException, match="Concurrent"):
        init.ConfigurationTransaction(changes).apply()
    path.write_text("{}")
    backup = path.with_name(f"{path.name}.magicite-{hashlib.sha256(b'{}').hexdigest()}.bak")
    backup.write_text("unexpected backup")
    with pytest.raises(click.ClickException, match="backup collision"):
        init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND)).apply()
    assert path.read_text() == "{}" and backup.read_text() == "unexpected backup"


@pytest.fixture
def gated(tmp_path, monkeypatch):
    from magicite.core import writer_guard
    from magicite.obs import doctor

    monkeypatch.setattr(init, "executable", lambda: COMMAND)
    monkeypatch.setattr(writer_guard, "preflight_custody", lambda cfg: None)
    monkeypatch.setattr(doctor, "custody_check", lambda cfg: {"status": "ok"})
    monkeypatch.setattr(init, "probe_model", lambda: None)
    monkeypatch.setattr(init, "verify_connection", lambda command, root: None)
    monkeypatch.setattr(init, "inventory", lambda cfg: [])
    monkeypatch.setenv("MAGICITE_EMBEDDING_PROVIDER", "fastembed")
    return tmp_path


def invoke(root, *args):
    return CliRunner().invoke(cli, ["init", "--host", "claude", "--project-root", str(root), *args])


def test_missing_custody_precedes_model_and_project_writes(gated, monkeypatch):
    from magicite.core import writer_guard

    def unavailable(cfg):
        raise ValueError("secret internal profile")

    monkeypatch.setattr(writer_guard, "preflight_custody", unavailable)
    monkeypatch.setattr(init, "probe_model", lambda: pytest.fail("model probe before custody"))
    result = invoke(gated)
    assert result.exit_code != 0 and "Protected custody" in result.output
    assert "secret internal" not in result.output and list(gated.iterdir()) == []


def test_integrity_failure_and_invalid_root_have_no_effects(gated, monkeypatch):
    from magicite.obs import doctor

    monkeypatch.setattr(doctor, "custody_check", lambda cfg: {"status": "fail"})
    assert invoke(gated).exit_code != 0
    assert list(gated.iterdir()) == []
    missing = gated / "missing"
    assert invoke(missing).exit_code != 0 and not missing.exists()


def test_unsupported_host_backend_and_skills_path(gated, monkeypatch):
    assert invoke(gated, "--host", "other").exit_code != 0
    monkeypatch.setenv("MAGICITE_EMBEDDING_PROVIDER", "hashing")
    assert invoke(gated).exit_code != 0
    monkeypatch.setenv("MAGICITE_EMBEDDING_PROVIDER", "fastembed")
    assert invoke(gated, "--skills", "../").exit_code != 0
    assert list(gated.iterdir()) == []


def test_cold_model_without_consent_no_download_or_files(gated, monkeypatch):
    from magicite.embeddings import fastembed_provider

    def unavailable():
        raise click.ClickException("Offline model unavailable")

    monkeypatch.setattr(init, "probe_model", unavailable)
    monkeypatch.setattr(fastembed_provider, "fetch_model", lambda: pytest.fail("unauthorized download"))
    result = invoke(gated)
    assert result.exit_code != 0 and list(gated.iterdir()) == []


def test_fetch_consent_reprobes_never_imports_or_approves(gated, monkeypatch):
    from magicite.embeddings import fastembed_provider
    from magicite.mcp import bind_ops

    probes, downloads = [], []

    def probe():
        probes.append(1)
        if len(probes) == 1:
            raise click.ClickException("unavailable")

    monkeypatch.setattr(init, "probe_model", probe)
    monkeypatch.setattr(fastembed_provider, "fetch_model", lambda: downloads.append(1))
    monkeypatch.setattr(bind_ops, "trust_approve", lambda *a, **kw: pytest.fail("implicit approval"))
    result = invoke(gated, "--fetch-model")
    assert result.exit_code == 0 and len(probes) == 2 and len(downloads) == 1
    assert "Connected; registration" in result.output and '"approved_non_draft": 0' in result.output


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_fetch_failure_reports_cache_effects_without_configuration(gated, monkeypatch, failure):
    from magicite.embeddings import fastembed_provider

    def unavailable():
        raise click.ClickException("unavailable")

    def download():
        raise failure()

    monkeypatch.setattr(init, "probe_model", unavailable)
    monkeypatch.setattr(fastembed_provider, "fetch_model", download)
    result = invoke(gated, "--fetch-model")
    assert result.exit_code != 0 and "Partial model-cache effects" in result.output
    assert list(gated.iterdir()) == []


def test_offline_probe_runs_inference_and_deadline(monkeypatch):
    import subprocess

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(init.subprocess, "run", run)
    with pytest.raises(click.ClickException):
        init.probe_model()
    argv, kwargs = calls[0]
    assert "offline=True" in argv[-1] and ".embed(" in argv[-1]
    assert kwargs["timeout"] == init.MODEL_TIMEOUT

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("probe", 60)

    monkeypatch.setattr(init.subprocess, "run", timeout)
    with pytest.raises(click.ClickException, match="timed out"):
        init.probe_model()


def test_handshake_failure_recovers_host_files(gated, monkeypatch):
    (gated / ".mcp.json").write_bytes(b"{}")

    def fail(command, root):
        raise click.ClickException("connection unavailable")

    monkeypatch.setattr(init, "verify_connection", fail)
    result = invoke(gated)
    assert result.exit_code != 0 and "not rolled back" in result.output
    assert (gated / ".mcp.json").read_bytes() == b"{}"
    assert not (gated / "CLAUDE.md").exists()


def test_readiness_reports_independent_counts(gated, monkeypatch):
    views = [
        {"admitted": False, "quarantined": True, "lifecycle_status": "nascent"},
        {"admitted": True, "quarantined": False, "lifecycle_status": "draft"},
        {"admitted": True, "quarantined": False, "lifecycle_status": "nascent"},
    ]
    monkeypatch.setattr(init, "inventory", lambda cfg: views)
    result = invoke(gated)
    assert result.exit_code == 0 and '"approved_non_draft": 1' in result.output
    assert '"draft": 1' in result.output and '"quarantined": 1' in result.output
    assert "policy and context eligibility" in result.output


def test_partial_import_failure_keeps_verified_config(gated, monkeypatch):
    monkeypatch.setattr(init, "onboarding", lambda *args: False)
    result = invoke(gated)
    assert result.exit_code != 0 and "Partial import/approval failure" in result.output
    assert (gated / ".mcp.json").is_file() and (gated / "CLAUDE.md").is_file()


@pytest.mark.parametrize("rollback", [False, True])
def test_temp_fsync_concurrent_edit_never_overwritten(tmp_path, monkeypatch, rollback):
    path = tmp_path / ".mcp.json"
    path.write_text("{}")
    txn = init.ConfigurationTransaction(init.prepare(tmp_path, COMMAND))
    if rollback:
        txn.apply()
    real_fsync = init.os.fsync

    def inject(fd):
        real_fsync(fd)
        # Backup fsync precedes replacement; inject only into the replacement-temp window.
        if __import__("os").fstat(fd).st_size != 2:
            path.write_text("concurrent external edit")
        elif rollback:
            path.write_text("concurrent external edit")

    monkeypatch.setattr(init.os, "fsync", inject)
    if rollback:
        assert txn.rollback() == [str(path)]
    else:
        with pytest.raises(click.ClickException, match="Concurrent"):
            txn.apply()
    assert path.read_text() == "concurrent external edit"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_rejected(tmp_path, constant):
    path = tmp_path / ".mcp.json"
    original = '{"extra":' + constant + "}"
    path.write_text(original)
    with pytest.raises(click.ClickException, match="Invalid"):
        init.prepare(tmp_path, COMMAND)
    assert path.read_text() == original
    assert not (tmp_path / "CLAUDE.md").exists()
