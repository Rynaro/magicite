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
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_migration_readers_create_nothing(tmp_path: Path) -> None:
    before = _tree(tmp_path)
    _run(tmp_path, "migration", "preview")
    _run(tmp_path, "migration", "status", "--operation-id", "missing", success=False)
    assert _tree(tmp_path) == before
    assert not (tmp_path / ".magicite").exists()


def test_cli_upgrade_status_restore_and_tamper_gate(legacy_migration_case, tmp_path: Path) -> None:
    from magicite.core import trust_legacy

    cfg, plan, backup, _ = legacy_migration_case(tmp_path)
    root = cfg.project_root
    original = {p.name: p.read_bytes() for p in cfg.registry_dir.glob("*.egr.md")}
    digest = trust_legacy.digest(plan)
    args = ["--reviewed-sha256", digest, "--backup-path", str(backup)]
    _run(root, "migration", "preview")
    applied = _run(root, "migration", "apply", *args)
    assert applied["state"] == "completed"
    before = _tree(cfg.data_dir)
    assert (
        _run(root, "migration", "status", "--operation-id", applied["operation_id"])["state"] == "completed"
    )
    assert _tree(cfg.data_dir) == before
    stage = tmp_path.parent / (tmp_path.name + "-operator-stage")
    staged = _run(root, "migration", "restore", *args, "--staging-path", str(stage))
    assert staged["state"] == "reconciliation_required"
    assert {p.name: p.read_bytes() for p in (stage / "files/engrams").glob("*.egr.md")} == original
    assert _tree(cfg.data_dir) == before
    # A forged backup manifest must never authorize even restricted materialization.
    (backup / "manifest.json").write_text("{}")
    staged_before = _tree(stage)
    _run(root, "migration", "restore", *args, "--staging-path", str(stage), success=False)
    assert _tree(cfg.data_dir) == before
    assert _tree(stage) == staged_before


def test_cli_resumes_real_interrupted_migration(legacy_migration_case, tmp_path: Path) -> None:
    from magicite.core import trust_legacy

    cfg, plan, backup, _ = legacy_migration_case(tmp_path)
    digest = trust_legacy.digest(plan)
    operation_id = "legacy-" + digest[:32]

    def crash(boundary: str) -> None:
        if boundary == "target_published":
            raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError):
        migration.apply(cfg, reviewed_sha256=digest, backup_path=backup, fault_hook=crash)
    args = ["--operation-id", operation_id, "--reviewed-sha256", digest, "--backup-path", str(backup)]
    result = _run(cfg.project_root, "migration", "resume", *args)
    assert result["state"] == "completed"
    retry = _run(cfg.project_root, "migration", "resume", *args)
    assert retry["state"] == "completed" and retry["duplicate_noop"]


def test_policy_registration_review_and_cas_are_public(custody_for, tmp_path: Path) -> None:
    custody_for(Config(project_root=tmp_path))
    manifest = policy_store.PolicyManifest(
        policy_id="dense-v1",
        policy_digest="a" * 64,
        policy_family="stable",
        config_digest="c",
        calibration_digest=None,
        index_generation_id="g",
        snapshot_id="s",
        selection="cosine_similarity",
    )
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(manifest.to_dict()))
    _run(
        tmp_path,
        "policy",
        "register-evaluated",
        "--manifest",
        str(path),
        "--evaluation-status",
        "inconclusive",
        "--evidence",
        "fixture-only",
    )
    approval = _run(
        tmp_path, "policy", "approve", "--policy-digest", manifest.policy_digest, "--actor", "fixture"
    )["approval_id"]
    _run(
        tmp_path,
        "policy",
        "activate",
        "--candidate-digest",
        manifest.policy_digest,
        "--approval-id",
        approval,
    )
    _run(
        tmp_path,
        "policy",
        "activate",
        "--candidate-digest",
        manifest.policy_digest,
        "--approval-id",
        approval,
        "--expected-current",
        "stale",
        success=False,
    )


def test_cli_checkpoint_is_durable_idempotent_self_report(custody_for, tmp_path: Path) -> None:
    from magicite.core import evidence

    cfg = Config(project_root=tmp_path)
    custody_for(cfg)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    decision = evidence.EvidenceEvent(
        event_id="ev_decision",
        decision_id="dec_fixture",
        event_type="decision",
        recorded_at="2026-09-30T00:00:00Z",
        chosen_action="skill_a",
        candidate_ids=("skill_a",),
        candidate_revisions=("digest_a",),
        behavior_policy_id="dense-v1",
        behavior_policy_digest="policy_digest",
        source_tier=0,
    )
    evidence.checkpoint(cfg, conn, decision)
    conn.close()
    args = (
        "evidence",
        "checkpoint",
        "--decision-event-id",
        "ev_decision",
        "--event-id",
        "ev_fixture",
        "--outcome",
        "success",
    )
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
    _run(
        tmp_path,
        "evidence",
        "checkpoint",
        "--decision-event-id",
        "missing",
        "--event-id",
        "ev_new",
        "--outcome",
        "success",
        success=False,
    )
    assert not list((tmp_path / ".magicite/evidence/segments").glob("*.events.jsonl"))


@pytest.mark.parametrize("unexpected", [False, True])
def test_cli_exception_details_never_disclose_canaries(tmp_path: Path, monkeypatch, unexpected) -> None:
    from magicite.errors import InvalidInputError
    from magicite.mcp import bind_ops

    def fail(*args, **kwargs):
        if unexpected:
            raise RuntimeError("raw-query-canary /private/tmp/secret")
        raise InvalidInputError(
            "raw-query-canary /private/tmp/secret",
            details={"code": "detail-secret-canary", "nested": {"code": "nested-secret-canary"}},
        )

    monkeypatch.setattr(bind_ops, "migration_preview", fail)
    result = CliRunner().invoke(cli, ["migration", "preview", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    for canary in ("raw-query-canary", "detail-secret-canary", "nested-secret-canary", "/private/tmp/secret"):
        assert canary not in result.output
    assert json.loads(result.output)["code"]


def test_explicit_route_checkpoint_replays_original_and_conflicts(
    cfg, project_root: Path, monkeypatch
) -> None:
    from magicite.core import registry
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.mcp import bind_ops

    monkeypatch.setenv("MAGICITE_EMBEDDING_PROVIDER", "hashing")
    cfg = Config(project_root=project_root)
    conn = db.connect(cfg.db_path)
    registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    from tests.conftest import TOY_ENGRAM_NAMES
    from tests.support.custody_adapter import review_toy_sources

    review_toy_sources(cfg, conn, names=TOY_ENGRAM_NAMES)
    conn.close()
    request = {"query": "force game onto nvidia gpu", "k": 1}
    first = bind_ops.evidence_route_checkpoint(project_root, request=request, event_id="ev_real")
    second = bind_ops.evidence_route_checkpoint(project_root, request=request, event_id="ev_real")
    assert first["decision_id"] == second["decision_id"]
    assert first["selected_content_digests"] == second["selected_content_digests"]
    assert second["checkpoint"]["replayed"]
    from magicite.errors import IdempotencyKeyConflictError

    with pytest.raises(IdempotencyKeyConflictError):
        bind_ops.evidence_route_checkpoint(project_root, request={"query": "different"}, event_id="ev_real")
    _run(
        project_root,
        "evidence",
        "checkpoint",
        "--decision-event-id",
        "ev_real",
        "--event-id",
        "ev_feedback",
        "--outcome",
        "success",
    )
    _run(
        project_root,
        "evidence",
        "checkpoint",
        "--decision-event-id",
        "ev_real",
        "--event-id",
        "ev_feedback",
        "--outcome",
        "failure",
        success=False,
    )
    ledger = "".join(p.read_text() for p in (cfg.data_dir / "evidence/segments").glob("*.jsonl"))
    assert request["query"] not in ledger


def test_checkpoint_rejects_unknown_schema_before_creating_state(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "RouteInput/future", "query": "private-query"}))
    result = CliRunner().invoke(
        cli,
        [
            "evidence",
            "checkpoint",
            "--project-root",
            str(tmp_path),
            "--route-request",
            str(request),
            "--event-id",
            "unsupported",
        ],
    )
    assert result.exit_code != 0
    assert "private-query" not in result.output
    assert not (tmp_path / ".magicite").exists()
