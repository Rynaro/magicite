"""Owned worker supervision and immutable binding controls, without model queries."""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from magicite.eval import quality
from magicite.eval.digests import sha256_json
from magicite.eval.skillret import canonical

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "official_quality_runner", ROOT / "scripts/qualify_official_quality.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def result(qid="q"):
    return {
        "query_id": qid,
        "status": "selected",
        "candidate_ids": ["gold"],
        "scores": [0.5],
        "selected_ids": ["gold"],
        "abstained": False,
        "confidence": None,
        "query_elapsed_seconds": 2.0,
    }


def test_durable_completed_row_recovers_checkpoint_without_reexecution():
    binding = {"source": "fixed"}
    row = result()
    row["binding_sha256"] = sha256_json(binding)
    state = {
        "binding": binding,
        "completed": 0,
        "spent_seconds": 0.0,
        "pending": {"query_id": "q", "allocated_seconds": 120},
        "row_file_sha256": None,
    }
    raw = canonical(row)
    recovered = quality.reconcile_checkpoint(state, raw, binding, [])
    assert recovered["completed"] == 1 and recovered["pending"] is None
    assert recovered["spent_seconds"] == 2
    assert quality.resume_rows([row], ["q", "next"]) == {"next"}


def test_cumulative_budget_reset_rejected():
    row = result()
    raw = canonical(row)
    binding = {"source": "fixed"}
    state = {
        "binding": binding,
        "completed": 1,
        "spent_seconds": 0.0,
        "pending": None,
        "row_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    with pytest.raises(ValueError, match="budget"):
        quality.reconcile_checkpoint(state, raw, binding, [])


def test_changed_bindings_and_torn_append_retained_rejected():
    state = {
        "binding": {"source": "old"},
        "completed": 0,
        "spent_seconds": 0.0,
        "pending": None,
        "row_file_sha256": None,
    }
    with pytest.raises(ValueError, match="binding"):
        quality.reconcile_checkpoint(state, b"", {"source": "new"}, [])
    with pytest.raises(ValueError, match="partial"):
        quality.reconcile_checkpoint(state, b'{"query_id":', state["binding"], [])


def test_interrupted_charge_is_separate_from_measured_rows():
    binding = {"source": "fixed"}
    row = result()
    raw = canonical(row)
    state = {
        "binding": binding,
        "completed": 1,
        "spent_seconds": 122.0,
        "pending": None,
        "row_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    interruptions = [{"conservative_budget_charge_seconds": 120, "elapsed_measurement": "UNKNOWN"}]
    assert quality.reconcile_checkpoint(state, raw, binding, interruptions)["spent_seconds"] == 122


def test_channel_partial_line_has_real_bounded_deadline():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys,time;sys.stdout.write('{');sys.stdout.flush();time.sleep(2)"],
        stdout=subprocess.PIPE,
    )
    channel = runner.Channel(process)
    try:
        with pytest.raises(TimeoutError):
            channel.receive(0.1, lambda: None)
    finally:
        process.kill()
        process.wait()
        channel.close()


def test_bound_file_tamper_and_frozen_contract_change_rejected(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(canonical({"input": 1}))
    bound = runner.bound(path)
    path.write_bytes(canonical({"input": 2}))
    with pytest.raises(ValueError, match="input changed"):
        runner.read_bound(bound)
    freeze = tmp_path / "run-freeze.json"
    freeze.write_bytes(canonical({"source": "fixed"}))
    (tmp_path / "run-freeze.sha256").write_text(runner.sha256(freeze))
    assert runner.frozen_contract(tmp_path)["source"] == "fixed"
    freeze.write_bytes(canonical({"source": "different"}))
    with pytest.raises(ValueError, match="freeze changed"):
        runner.frozen_contract(tmp_path)


def test_full_test_rejects_unbound_authorization(tmp_path):
    runner.write(tmp_path / "run-freeze.json", {"source_commit": "fixed"})
    runner.write(tmp_path / "smoke-report.json", {})
    runner.write(tmp_path / "test-snapshot.json", {})
    runner.write(
        tmp_path / "authorization.json", {"checker": "vigil", "verdict": "ACCEPTED", "source_commit": "wrong"}
    )
    with pytest.raises(ValueError, match="authorization"):
        runner.authorization(tmp_path, tmp_path / "authorization.json")


def test_runtime_projection_does_not_accept_gold_annotations():
    query = {"query_id": "q", "query_text": "original text", "compatibility_context": {}}
    assert runner.runtime_queries([query]) == [query]
    with pytest.raises(ValueError, match="only query"):
        runner.runtime_queries([{**query, "gold_ids": ["gold"]}])


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_model_manifest_sha256", "wrong"),
        ("pool_snapshot_sha256", "wrong"),
        ("config_digest", "wrong"),
        ("binding_sha256", "wrong"),
        ("candidate_ids", ["foreign"]),
    ],
)
def test_resume_domain_drift_rejected_before_further_queries(field, value):
    binding = {
        "snapshot_sha256": "snapshot",
        "experiment_sha256": "experiment",
        "runtime_sha256": "runtime",
        "policy_id": "dense-v1",
    }
    contract = {
        "model_manifest": {"sha256": "model"},
        "policies": {"dense-v1": {"policy_digest": "policy", "config_digest": "config"}},
    }
    snapshot = {"admitted": [{"id": "gold"}]}
    row = result()
    row.update(
        ordinal=0,
        binding_sha256=sha256_json(binding),
        policy_id="dense-v1",
        pool_snapshot_sha256="snapshot",
        declared_pool_count=10123,
        expected_model_manifest_sha256="model",
        policy_digest="policy",
        config_digest="config",
    )
    row[field] = value
    with pytest.raises(ValueError):
        runner.validate_rows([row], binding, contract, snapshot, "smoke", "dense-v1")


def test_fresh_snapshot_clone_rejects_vector_state_drift(tmp_path):
    source = tmp_path / "base"
    source.mkdir()
    (source / "vectors.bin").write_bytes(b"actual-vector")
    manifest = {"files": runner.snapshot_files(source)}
    runner.copy_snapshot(source, tmp_path / "first", manifest)
    (source / "vectors.bin").write_bytes(b"changed-vector")
    with pytest.raises(ValueError, match="snapshot changed"):
        runner.copy_snapshot(source, tmp_path / "second", manifest)


def test_supported_review_retains_pre_review_status_and_actual_decisions(cfg, db_conn, embedder, tmp_path):
    staging = cfg.project_root / "review-staging"
    staging.mkdir()
    source = next(cfg.registry_dir.glob("*.egr.md"))
    (staging / source.name).write_bytes(source.read_bytes())
    runner.registry.register(cfg, db_conn, embedder, path="review-staging")
    evidence = runner.review_with_evidence(cfg, db_conn, tmp_path / "review-evidence")
    before = runner.read_bound(evidence["before"])
    after = runner.read_bound(evidence["outcomes"])
    assert before and {row["id"] for row in before} == {row["id"] for row in after}
    assert all(row["verification_status"] != "verified" for row in before)
    assert all("lint_issues" in row and "injection_scan" in row for row in before)
    assert all(row["verification_status"] == "verified" for row in after)
    assert all(
        row["decision"]["decision"] == "admit" and row["decision"]["content_digest"] == row["content_sha256"]
        for row in after
    )
