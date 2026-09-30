"""S14 scale / UNEVALUATED catalog / operator CLI / composition harness."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from magicite.core.composition import Plan, PlanDiagnostic
from magicite.eval import __main__ as eval_main
from magicite.eval.composition_eval import (
    evaluate_composition_corpus,
    evaluate_structural_case,
    load_composition_v1_corpus,
)
from magicite.eval.digests import sha256_path
from magicite.eval.external import load_offline_skillret_fixture, official_skillret_status
from magicite.eval.manifests import ExperimentManifest
from magicite.eval.runner import run_predictions
from magicite.eval.unevaluated import UNEVALUATED_CATALOG, unevaluated_catalog
from magicite.eval.verdicts import min_groups_required

ROOT = Path(__file__).resolve().parents[3]
TINY_CORPUS = ROOT / "docs/evaluation/v1/fixtures/skillret-tiny/corpus_manifest.json"
EVAL_SUBS = {
    "acquire-skillret",
    "run-retrieval",
    "run-paired-policies",
    "run-abstention-gate",
    "run-host-tasks",
}


def _plan(
    *,
    status: str,
    order: tuple[str, ...] = (),
    diagnostics: tuple[PlanDiagnostic, ...] = (),
    executable: bool = False,
) -> Plan:
    return Plan(
        status=status,  # type: ignore[arg-type]
        nodes=(),
        edges=(),
        topological_order=order,
        supplied_capabilities=(),
        missing_capabilities=(),
        diagnostics=diagnostics,
        snapshot_id="s",
        policy_id="dense-v1",
        policy_digest="d",
        executable=executable,
    )


def _sealed_experiment_for_tiny(tmp_path: Path) -> Path:
    corpus = load_offline_skillret_fixture()
    data = {
        "schema": "magicite-experiment-manifest/1",
        "experiment_id": "s14-fixture-offline/1",
        "hypothesis": "offline fixture harness wiring",
        "reversal_condition": "retain dense-v1",
        "source_commit": "a" * 40,
        "dirty_tree_digest": None,
        "runner_lock_sha256": "b" * 64,
        "dependency_lock_sha256": "c" * 64,
        "corpus_sha256": corpus.content_identity_sha256,
        "labels_sha256": corpus.content_identity_sha256,
        "split_sha256": "d" * 64,
        "primary_metric": "hit_at_1",
        "secondary_metrics": ["mrr"],
        "statistical_method": "paired_group_bootstrap_percentile_95",
        "thresholds": {"noninferiority_margin_hit_at_1": 0.02},
        "seeds": {"prediction": 0, "bootstrap": 0},
        "embedder_artifact_sha256": None,
        "policy_config_sha256": None,
        "label_provenance": {
            "origin": "independent",
            "production_expansion_used": False,
            "final_labels_opened": True,
        },
        "dataset_license": "fixture-only",
        "timestamp": "2026-09-29T00:00:00Z",
        "sample_power_plan": {
            "power": 0.8,
            "alpha_one_sided": 0.025,
            "margin": 0.02,
            "min_independent_groups": 40,
            "archived_before_final": True,
        },
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def test_unevaluated_catalog_has_operator_commands() -> None:
    """Every catalogued command resolves to a real subparser or script."""
    catalog = unevaluated_catalog()
    assert len(catalog) == len(UNEVALUATED_CATALOG)
    for item in catalog:
        assert item["status"] == "UNEVALUATED"
        assert item["operator_command"]
        assert item["manifest_or_digest"]
        assert item.get("passed") is not True

        # Split chained commands on &&
        for part in item["operator_command"].split("&&"):
            tokens = shlex.split(part.strip())
            assert tokens, f"empty command in {item['item_id']}"
            if tokens[:3] == ["python", "-m", "magicite.eval"]:
                assert len(tokens) >= 4
                sub = tokens[3]
                assert sub in EVAL_SUBS or sub.startswith("validate-"), (
                    f"{item['item_id']}: unknown eval subcommand {sub!r}"
                )
                # --help must exit 0 for the resolved subparser
                completed = subprocess.run(
                    [sys.executable, "-m", "magicite.eval", sub, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert completed.returncode == 0, completed.stderr
                # Every flag token starting with -- must appear in help text
                help_text = completed.stdout + completed.stderr
                for tok in tokens[4:]:
                    if tok.startswith("--"):
                        flag = tok.split("=")[0]
                        assert flag in help_text, (
                            f"{item['item_id']}: flag {flag} missing from {sub} --help"
                        )
            elif tokens[0] == "python" and tokens[1].endswith("run_benchmark_matrix.py"):
                script = ROOT / tokens[1]
                if not script.is_file():
                    # allow relative path form
                    script = ROOT / "scripts" / "run_benchmark_matrix.py"
                assert script.is_file(), f"missing script for {item['item_id']}"
                completed = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert completed.returncode == 0, completed.stderr
                help_text = completed.stdout + completed.stderr
                for tok in tokens[2:]:
                    if tok.startswith("--"):
                        flag = tok.split("=")[0]
                        assert flag in help_text, (
                            f"{item['item_id']}: flag {flag} missing from matrix --help"
                        )
            else:
                pytest.fail(f"{item['item_id']}: unresolvable operator command {part!r}")


def test_official_skillret_status_is_unevaluated() -> None:
    status = official_skillret_status()
    assert status["status"] == "UNEVALUATED"
    assert "arxiv" in status["source_url"]
    assert status["operator_command"]


def test_min_groups_required_never_lowers_floor() -> None:
    assert min_groups_required(None) == 30
    assert min_groups_required({}) == 30
    assert min_groups_required({"min_independent_groups": 10}) == 30
    assert min_groups_required({"min_independent_groups": 40}) == 40


def test_run_predictions_hard_fails_on_digest_mismatch() -> None:
    corpus = load_offline_skillret_fixture()
    experiment = ExperimentManifest(
        experiment_id="bad",
        hypothesis="h",
        reversal_condition="r",
        source_commit="a" * 40,
        dirty_tree_digest=None,
        runner_lock_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        corpus_sha256="0" * 64,
        labels_sha256="0" * 64,
        split_sha256="0" * 64,
        primary_metric="hit_at_1",
        secondary_metrics=(),
        statistical_method="paired_group_bootstrap_percentile_95",
        thresholds={},
        seeds={"prediction": 0},
        embedder_artifact_sha256=None,
        policy_config_sha256=None,
        label_provenance={"final_labels_opened": True},
        dataset_license="x",
        timestamp="2026-09-29T00:00:00Z",
    )
    with pytest.raises(ValueError, match="does not match experiment.corpus_sha256"):
        run_predictions(experiment, corpus)


def test_acquire_skillret_offline_e2e(tmp_path: Path) -> None:
    archive = tmp_path / "tiny.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(TINY_CORPUS, arcname="corpus_manifest.json")
    digest = sha256_path(archive)
    out = tmp_path / "acquired.json"
    rc = eval_main.main(
        [
            "acquire-skillret",
            "--archive",
            str(archive),
            "--expected-sha256",
            digest,
            "--license",
            "fixture-only",
            "--revision",
            "offline-fixture",
            "--corpus-json",
            str(TINY_CORPUS),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["evidence_status"] == "UNEVALUATED"
    assert payload["acquisition"]["archive_sha256"] == digest

    # Mismatch fails closed.
    rc_bad = eval_main.main(
        [
            "acquire-skillret",
            "--archive",
            str(archive),
            "--expected-sha256",
            "0" * 64,
            "--license",
            "fixture-only",
            "--revision",
            "x",
            "--corpus-json",
            str(TINY_CORPUS),
            "--output",
            str(tmp_path / "bad.json"),
        ]
    )
    assert rc_bad == 1


def test_run_retrieval_offline_e2e(tmp_path: Path) -> None:
    exp = _sealed_experiment_for_tiny(tmp_path)
    out = tmp_path / "retrieval-out"
    rc = eval_main.main(
        [
            "run-retrieval",
            "--experiment",
            str(exp),
            "--corpus",
            str(TINY_CORPUS),
            "--split",
            "final",
            "--provider",
            "hashing",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    result = json.loads((out / "result_manifest.json").read_text(encoding="utf-8"))
    assert result["aggregates"]["evidence_status"] == "UNEVALUATED"
    assert (out / "predictions.json").is_file()


def test_run_paired_policies_offline_e2e(tmp_path: Path) -> None:
    exp = _sealed_experiment_for_tiny(tmp_path)
    out = tmp_path / "paired.json"
    rc = eval_main.main(
        [
            "run-paired-policies",
            "--incumbent",
            "dense-v1",
            "--candidate",
            "hybrid-rrf-v1",
            "--experiment",
            str(exp),
            "--corpus",
            str(TINY_CORPUS),
            "--n-resamples",
            "50",
            "--seed",
            "0",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "UNEVALUATED"
    assert payload["policy_activation"] == "not_called"
    assert payload["default_remains_simple_incumbent"] is True
    assert payload["overall_promotion"]["status"] != "pass"


def test_run_abstention_gate_offline_e2e(tmp_path: Path) -> None:
    exp = _sealed_experiment_for_tiny(tmp_path)
    out = tmp_path / "abstention.json"
    rc = eval_main.main(
        [
            "run-abstention-gate",
            "--calibration-split",
            "development",
            "--final-split",
            "final",
            "--experiment",
            str(exp),
            "--corpus",
            str(TINY_CORPUS),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "UNEVALUATED"
    assert "verdict" in payload


def test_run_host_tasks_offline_e2e(tmp_path: Path) -> None:
    arms = {
        "arms": [
            {
                "task_id": "t1",
                "group_id": "g1",
                "arm": "no_skill",
                "outcome": "fail",
                "verifier_id": "echo",
                "verifier_artifact_digest": "a" * 64,
            },
            {
                "task_id": "t1",
                "group_id": "g1",
                "arm": "composed_plan",
                "outcome": "pass",
                "verifier_id": "echo",
                "verifier_artifact_digest": "a" * 64,
            },
        ]
    }
    corpus = tmp_path / "host.json"
    corpus.write_text(json.dumps(arms), encoding="utf-8")
    out = tmp_path / "host-out.json"
    rc = eval_main.main(
        [
            "run-host-tasks",
            "--corpus",
            str(corpus),
            "--arms",
            "no_skill,composed_plan",
            "--n-resamples",
            "50",
            "--seed",
            "0",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "UNEVALUATED"
    assert payload["evidence_class"] == "host-task"


def test_matrix_synthetic_vs_manifest_corpus(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "run_benchmark_matrix.py"
    syn_out = tmp_path / "syn.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--profile",
            "ci-smoke",
            "--provider",
            "hashing",
            "--envelope-mode",
            "completeness",
            "--project-root-for-lock",
            str(ROOT),
            "--output",
            str(syn_out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    syn = json.loads(syn_out.read_text(encoding="utf-8"))
    assert syn["corpus"]["kind"] == "synthetic"
    assert syn["corpus"]["ga_eligible"] is False

    man_out = tmp_path / "man.json"
    completed2 = subprocess.run(
        [
            sys.executable,
            str(script),
            "--profile",
            "ci-smoke",
            "--provider",
            "hashing",
            "--envelope-mode",
            "completeness",
            "--corpus-manifest",
            str(TINY_CORPUS),
            "--project-root-for-lock",
            str(ROOT),
            "--output",
            str(man_out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed2.returncode == 0, completed2.stderr + completed2.stdout
    man = json.loads(man_out.read_text(encoding="utf-8"))
    assert man["corpus"]["kind"] == "manifest"
    assert man["corpus"]["content_identity_sha256"]
    assert man["corpus"]["n_candidates"] >= 1
    assert man["measurements"]["size"] == man["corpus"]["n_candidates"]


def test_composition_v1_structural_loader() -> None:
    path = Path("tests/fixtures/composition-v1/structural-corpus.json")
    corpus = load_composition_v1_corpus(path)
    assert corpus["corpus_id"] == "composition-v1-structural"
    assert len(corpus["cases"]) >= 5


def test_structural_case_valid_and_invalid() -> None:
    valid_plan = _plan(
        status="valid",
        order=("egr_a0000001", "egr_b0000001"),
        executable=True,
    )
    case = {
        "id": "producer-before-consumer",
        "accepted_orders": [["egr_a0000001", "egr_b0000001"]],
    }
    outcome = evaluate_structural_case(case, valid_plan)
    assert outcome.order_accepted is True

    invalid_plan = _plan(
        status="invalid",
        diagnostics=(PlanDiagnostic(code="cycle", message="cycle"),),
    )
    invalid_case = {
        "id": "cycle-no-executable-prefix",
        "expect_status": "invalid",
        "expect_codes": ["cycle"],
        "expect_empty_order": True,
    }
    inv = evaluate_structural_case(invalid_case, invalid_plan)
    assert inv.codes_match is True
    assert inv.actual_status == "invalid"


def test_structural_report_forbids_efficacy_claim() -> None:
    corpus = {
        "corpus_id": "toy",
        "cases": [{"id": "c1", "accepted_orders": [["a"]]}],
    }
    report = evaluate_composition_corpus(
        corpus, {"c1": _plan(status="valid", order=("a",), executable=True)}
    )
    assert report.evidence_class == "structural"
    assert report.to_dict()["structural_efficacy_claim_allowed"] is False
    assert report.n_pass == 1
