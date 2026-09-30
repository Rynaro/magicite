"""Public CLI completeness for existing fenced migration/policy/evidence domains."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.config import Config
from magicite.core import migration, policy_store
from magicite.storage import db


def _run(root: Path, *args: str, success: bool = True) -> dict:
    result = CliRunner().invoke(cli, [*args, "--project-root", str(root)])
    assert (result.exit_code == 0) == success, (result.output, result.exception)
    return json.loads(result.output) if result.exit_code == 0 else {}


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_migration_readers_create_nothing(tmp_path: Path) -> None:
    before = _tree(tmp_path)
    _run(tmp_path, "migration", "preview")
    _run(tmp_path, "migration", "status", "--operation-id", "missing", success=False)
    assert _tree(tmp_path) == before
    assert not (tmp_path / ".magicite").exists()


def test_cli_upgrade_status_restore_and_tamper_gate(project_root: Path) -> None:
    cfg = Config(project_root=project_root)
    original = {p.name: p.read_bytes() for p in cfg.registry_dir.glob("*.egr.md")}
    _run(project_root, "migration", "preview")
    applied = _run(project_root, "migration", "apply", "--operation-id", "cli-upgrade")
    assert applied["state"] == "completed"
    before = _tree(cfg.data_dir)
    assert _run(project_root, "migration", "status", "--operation-id", "cli-upgrade")["state"] == "completed"
    assert _tree(cfg.data_dir) == before
    backup = cfg.data_dir / applied["backup_relpath"]
    alias = project_root / "backup-alias"
    alias.symlink_to(backup, target_is_directory=True)
    _run(project_root, "migration", "restore", "--backup-path", str(alias))
    assert {p.name: p.read_bytes() for p in cfg.registry_dir.glob("*.egr.md")} == original
    # A forged backup manifest must never authorize restore.
    manifest = backup.parent / "manifest.json"
    manifest.write_text('{}')
    before = _tree(cfg.registry_dir)
    _run(project_root, "migration", "restore", "--backup-path", str(backup), success=False)
    assert _tree(cfg.registry_dir) == before


def test_cli_resumes_real_interrupted_migration(project_root: Path) -> None:
    cfg = Config(project_root=project_root)
    def crash(boundary: str) -> None:
        if boundary == "boundary:backup_committed":
            raise RuntimeError("injected interruption")
    with pytest.raises(RuntimeError):
        migration.apply(cfg, operation_id="cli-resume", fault_hook=crash)
    result = _run(project_root, "migration", "resume", "--operation-id", "cli-resume")
    assert result["state"] == "completed"
    assert _run(project_root, "migration", "resume", "--operation-id", "cli-resume")["state"] == "completed"


def test_policy_registration_review_and_cas_are_public(tmp_path: Path) -> None:
    manifest = policy_store.PolicyManifest(policy_id="dense-v1", policy_digest="a" * 64,
        policy_family="stable", config_digest="c", calibration_digest=None,
        index_generation_id="g", snapshot_id="s", selection="cosine_similarity")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(manifest.to_dict()))
    _run(tmp_path, "policy", "register-evaluated", "--manifest", str(path),
         "--evaluation-status", "inconclusive", "--evidence", "fixture-only")
    approval = _run(tmp_path, "policy", "approve", "--policy-digest", manifest.policy_digest,
                    "--actor", "fixture")["approval_id"]
    _run(tmp_path, "policy", "activate", "--candidate-digest", manifest.policy_digest,
         "--approval-id", approval)
    _run(tmp_path, "policy", "activate", "--candidate-digest", manifest.policy_digest,
         "--approval-id", approval, "--expected-current", "stale", success=False)


def test_cli_checkpoint_is_durable_idempotent_self_report(tmp_path: Path) -> None:
    from magicite.core import evidence
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    decision = evidence.EvidenceEvent(event_id="ev_decision", decision_id="dec_fixture",
        event_type="decision", recorded_at="2026-09-30T00:00:00Z", chosen_action="skill_a",
        candidate_ids=("skill_a",), candidate_revisions=("digest_a",),
        behavior_policy_id="dense-v1", behavior_policy_digest="policy_digest", source_tier=0)
    evidence.checkpoint(cfg, conn, decision)
    conn.close()
    args = ("evidence", "checkpoint", "--decision-event-id", "ev_decision", "--event-id", "ev_fixture",
            "--outcome", "success")
    first = _run(tmp_path, *args)
    second = _run(tmp_path, *args)
    assert first["sequence"] == second["sequence"]
    cfg = Config(project_root=tmp_path)
    segment = next((cfg.data_dir / "evidence/segments").glob("*.events.jsonl"))
    data = json.loads(segment.read_text().splitlines()[-1])
    assert data["event"]["source_tier"] == 1
    assert data["event"]["candidate_revisions"] == ["digest_a"]
    assert data["event"]["behavior_policy_digest"] == "policy_digest"
    assert data["event"]["verifier"]["type"] == "self_reported"
    for flag, value in [("--source-tier", "2"), ("--verifier", "host_verified"), ("--query", "raw-canary")]:
        _run(tmp_path, *args, flag, value, success=False)
    assert "raw-canary" not in segment.read_text()
    conn = db.connect(cfg.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM evidence_event_projection").fetchone()[0] == 2
    finally:
        conn.close()


def test_checkpoint_rejects_missing_original_decision(tmp_path: Path) -> None:
    _run(tmp_path, "evidence", "checkpoint", "--decision-event-id", "missing",
         "--event-id", "ev_new", "--outcome", "success", success=False)
    assert not list((tmp_path / ".magicite/evidence/segments").glob("*.events.jsonl"))


@pytest.mark.parametrize("unexpected", [False, True])
def test_cli_exception_details_never_disclose_canaries(tmp_path: Path, monkeypatch, unexpected) -> None:
    from magicite.errors import InvalidInputError
    from magicite.mcp import bind_ops
    def fail(*args, **kwargs):
        if unexpected:
            raise RuntimeError("raw-query-canary /private/tmp/secret")
        raise InvalidInputError("raw-query-canary /private/tmp/secret",
            details={"code":"detail-secret-canary", "nested":{"code":"nested-secret-canary"}})
    monkeypatch.setattr(bind_ops, "migration_preview", fail)
    result = CliRunner().invoke(cli, ["migration", "preview", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    for canary in ("raw-query-canary", "detail-secret-canary", "nested-secret-canary", "/private/tmp/secret"):
        assert canary not in result.output
    assert json.loads(result.output)["code"]


def test_explicit_route_checkpoint_replays_original_and_conflicts(project_root: Path, monkeypatch) -> None:
    from magicite.core import registry
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.mcp import bind_ops
    monkeypatch.setenv("MAGICITE_EMBEDDING_PROVIDER", "hashing")
    cfg = Config(project_root=project_root)
    conn = db.connect(cfg.db_path)
    registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    conn.close()
    request = {"query":"force game onto nvidia gpu", "k":1}
    first = bind_ops.evidence_route_checkpoint(project_root, request=request, event_id="ev_real")
    second = bind_ops.evidence_route_checkpoint(project_root, request=request, event_id="ev_real")
    assert first["decision_id"] == second["decision_id"]
    assert first["selected_content_digests"] == second["selected_content_digests"]
    assert second["checkpoint"]["replayed"]
    from magicite.errors import IdempotencyKeyConflictError
    with pytest.raises(IdempotencyKeyConflictError):
        bind_ops.evidence_route_checkpoint(project_root, request={"query":"different"}, event_id="ev_real")
    _run(project_root, "evidence", "checkpoint", "--decision-event-id", "ev_real",
         "--event-id", "ev_feedback", "--outcome", "success")
    _run(project_root, "evidence", "checkpoint", "--decision-event-id", "ev_real",
         "--event-id", "ev_feedback", "--outcome", "failure", success=False)
    ledger = "".join(p.read_text() for p in (cfg.data_dir / "evidence/segments").glob("*.jsonl"))
    assert request["query"] not in ledger


def test_checkpoint_rejects_unknown_schema_before_creating_state(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "RouteInput/future", "query": "private-query"}))
    result = CliRunner().invoke(cli, ["evidence", "checkpoint", "--project-root", str(tmp_path),
        "--route-request", str(request), "--event-id", "unsupported"])
    assert result.exit_code != 0
    assert "private-query" not in result.output
    assert not (tmp_path / ".magicite").exists()
