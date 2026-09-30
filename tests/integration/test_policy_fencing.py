"""Policy commit-boundary fencing and interrupted governance projection recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import approvals
from magicite.core import policy_store as ps
from magicite.errors import BusyError, InvalidInputError
from magicite.storage import db, lease


def _manifest(digest: str) -> ps.PolicyManifest:
    return ps.PolicyManifest(policy_id="dense-v1", policy_digest=digest,
                             policy_family="stable", config_digest="c",
                             calibration_digest=None, index_generation_id="g",
                             snapshot_id="s", selection="cosine_similarity")


def _prepared(tmp_path: Path) -> tuple[Config, str]:
    cfg = Config(project_root=tmp_path)
    ps.register_evaluated(cfg, _manifest("a"), evaluation_status="pass")
    return cfg, ps.approve(cfg, "a", actor="fixture")


def test_nested_scope_reuses_only_same_registry(tmp_path: Path) -> None:
    cfg, _ = _prepared(tmp_path / "one")
    other = Config(project_root=tmp_path / "two")
    conn = db.connect(cfg.db_path)
    cross = lease.CrossProcessLease(lock_path=cfg.dream_lock_path, conn=conn, holder="outer")
    try:
        with cross.acquire():
            ps.register_evaluated(cfg, _manifest("b"), evaluation_status="pass")
            cross.assert_owned()
            with pytest.raises(BusyError, match="different registry"):
                ps.register_evaluated(other, _manifest("b"), evaluation_status="pass")
            assert not ps.policy_store_path(other).exists()
            cross.assert_owned()
    finally:
        conn.close()


@pytest.mark.parametrize("domain", ["policy", "mirror"])
def test_token_loss_after_file_fsync_prevents_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str,
) -> None:
    cfg, approval = _prepared(tmp_path)
    before = ps.policy_store_path(cfg).read_bytes()
    conn = db.connect(cfg.db_path)
    cross = lease.CrossProcessLease(lock_path=cfg.dream_lock_path, conn=conn, holder="outer")
    real_fsync = os.fsync
    try:
        with cross.acquire():
            def lose_token(fd: int) -> None:
                real_fsync(fd)
                # The first file fsync is state for register, prepared mirror for activate.
                conn.execute("UPDATE writer_lease SET fencing_token = fencing_token + 1")
                conn.commit()
            monkeypatch.setattr(os, "fsync", lose_token)
            with pytest.raises(BusyError):
                if domain == "policy":
                    ps.register_evaluated(cfg, _manifest("b"), evaluation_status="pass")
                else:
                    ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
            assert ps.policy_store_path(cfg).read_bytes() == before
            assert not list(cfg.approvals_dir.glob("*.json"))
    finally:
        conn.close()


@pytest.mark.parametrize("boundary", ["before_state", "after_state", "after_mirror"])
def test_interrupted_policy_commit_is_recoverable_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    cfg, approval = _prepared(tmp_path)
    original_save = ps._save_raw
    original_finalize = approvals.finalize_policy_control_event
    def failing_save(config: Config, state: dict) -> None:
        if boundary == "before_state" and state.get("pending_control"):
            raise OSError("injected before authenticated commit")
        if boundary == "after_mirror" and state.get("active_digest") and not state.get("pending_control"):
            raise OSError("injected before pending clear")
        original_save(config, state)
    def failing_finalize(config: Config, expected: dict) -> None:
        if boundary == "after_state":
            raise OSError("injected before mirror finalization")
        original_finalize(config, expected)
    monkeypatch.setattr(ps, "_save_raw", failing_save)
    monkeypatch.setattr(approvals, "finalize_policy_control_event", failing_finalize)
    with pytest.raises(OSError):
        ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
    mirrors = list(cfg.approvals_dir.glob("*.json"))
    assert len(mirrors) == 1
    assert ps.prior_policy_governance_evidence(cfg)
    if boundary == "before_state":
        assert ps.status(cfg).active_digest is None
        assert json.loads(mirrors[0].read_text())["state"] == "approved"
    else:
        with pytest.raises(InvalidInputError, match="pending"):
            ps.get_active_manifest(cfg)
    monkeypatch.setattr(ps, "_save_raw", original_save)
    monkeypatch.setattr(approvals, "finalize_policy_control_event", original_finalize)
    assert ps.reconcile(cfg).active_digest == (None if boundary == "before_state" else "a")
    assert len(list(cfg.approvals_dir.glob("*.json"))) == 1
    if boundary != "before_state":
        assert json.loads(mirrors[0].read_text())["state"] == "succeeded"


def test_conflicting_pending_mirror_never_repaired_from_untrusted_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, approval = _prepared(tmp_path)
    original = approvals.finalize_policy_control_event
    def crash(*args: object) -> None:
        raise OSError("crash")
    monkeypatch.setattr(approvals, "finalize_policy_control_event", crash)
    with pytest.raises(OSError):
        ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
    monkeypatch.setattr(approvals, "finalize_policy_control_event", original)
    mirror = next(cfg.approvals_dir.glob("*.json"))
    bad = json.loads(mirror.read_text())
    bad["payload"]["policy_digest"] = "attacker"
    mirror.write_text(json.dumps(bad))
    before = ps.policy_store_path(cfg).read_bytes()
    with pytest.raises(InvalidInputError, match="conflicts"):
        ps.reconcile(cfg)
    assert ps.policy_store_path(cfg).read_bytes() == before
    with pytest.raises(InvalidInputError, match="pending"):
        ps.status(cfg)


def test_prepared_mirror_directory_is_durable_before_state_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stat
    cfg, approval = _prepared(tmp_path)
    before = ps.policy_store_path(cfg).read_bytes()
    real_fsync = os.fsync
    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory durability unavailable")
        real_fsync(fd)
    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="directory durability"):
        ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
    assert ps.policy_store_path(cfg).read_bytes() == before
    assert ps.status(cfg).active_digest is None


@pytest.mark.parametrize("mirror_state", ["valid", "missing", "conflicting"])
def test_public_cli_reconciliation_and_read_only_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mirror_state: str,
) -> None:
    from click.testing import CliRunner

    from magicite.__main__ import cli
    cfg, approval = _prepared(tmp_path)
    original = approvals.finalize_policy_control_event
    def crash(*args: object) -> None:
        raise OSError("crash before finalization")
    monkeypatch.setattr(approvals, "finalize_policy_control_event", crash)
    with pytest.raises(OSError):
        ps.activate(cfg, expected_current=None, candidate_digest="a", approval_id=approval)
    monkeypatch.setattr(approvals, "finalize_policy_control_event", original)
    mirror = next(cfg.approvals_dir.glob("*.json"))
    if mirror_state == "missing":
        mirror.unlink()
    elif mirror_state == "conflicting":
        raw = json.loads(mirror.read_text())
        raw["payload"]["policy_digest"] = "untrusted"
        mirror.write_text(json.dumps(raw))
    def snapshot() -> dict[str, bytes]:
        return {str(path.relative_to(cfg.data_dir)): path.read_bytes()
                for path in cfg.data_dir.rglob("*") if path.is_file()}
    before = snapshot()
    with pytest.raises(InvalidInputError, match="pending"):
        ps.get_active_manifest(cfg)
    runner = CliRunner()
    runner.invoke(cli, ["doctor", "--project-root", str(tmp_path)])
    assert snapshot() == before
    result = runner.invoke(cli, ["policy", "reconcile", "--project-root", str(tmp_path)])
    assert result.exit_code == (0 if mirror_state == "valid" else 1), result.output
    if mirror_state == "valid":
        assert json.loads(result.output)["active_digest"] == "a"
    else:
        assert ps.policy_store_path(cfg).read_bytes() == before["policy_store/state.json"]
        with pytest.raises(InvalidInputError, match="pending"):
            ps.status(cfg)
