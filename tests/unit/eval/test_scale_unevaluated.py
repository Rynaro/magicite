"""S14 scale / UNEVALUATED catalog / operator CLI / composition harness."""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

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
                        assert flag in help_text, f"{item['item_id']}: flag {flag} missing from {sub} --help"
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
                        assert flag in help_text, f"{item['item_id']}: flag {flag} missing from matrix --help"
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


def test_run_host_tasks_demotes_nested_pass(tmp_path: Path) -> None:
    rows = []
    for i in range(40):
        for arm, outcome in (("no_skill", "fail"), ("selected_skill", "fail"), ("composed_plan", "pass")):
            rows.append(
                {
                    "task_id": f"t{i}",
                    "group_id": f"g{i}",
                    "arm": arm,
                    "outcome": outcome,
                    "verifier_id": "echo",
                    "verifier_artifact_digest": "a" * 64,
                }
            )
    corpus = tmp_path / "host.json"
    corpus.write_text(json.dumps({"arms": rows}), encoding="utf-8")
    out = tmp_path / "host-out.json"
    rc = eval_main.main(
        [
            "run-host-tasks",
            "--corpus",
            str(corpus),
            "--arms",
            "no_skill,selected_skill,composed_plan",
            "--n-resamples",
            "200",
            "--seed",
            "0",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["harness_computed_usefulness_status"] == "pass"
    assert payload["usefulness_status"] == "unevaluated"


def test_run_abstention_gate_demotes_nested_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from magicite.eval import operator_cli
    from magicite.eval.verdicts import Verdict

    monkeypatch.setattr(
        operator_cli,
        "abstention_verdict",
        lambda **_: Verdict(gate="abstention", status="pass", reason="forced", details={}),
    )
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
    verdict = json.loads(out.read_text(encoding="utf-8"))["verdict"]
    assert verdict["status"] == "unevaluated"
    assert verdict["harness_computed_status"] == "pass"


def test_demote_pass_rewrites_only_pass() -> None:
    from magicite.eval.operator_cli import _demote_pass

    demoted = _demote_pass({"gate": "abstention", "status": "pass", "reason": "ok"})
    assert demoted["status"] == "unevaluated"
    assert demoted["harness_computed_status"] == "pass"
    for status in ("fail", "inconclusive", "unevaluated"):
        verdict = {"gate": "abstention", "status": status, "reason": "r"}
        assert _demote_pass(verdict) == verdict


def test_paired_policies_labels_candidate_arm_and_never_nests_pass(tmp_path: Path) -> None:
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
    assert payload["candidate_arm_kind"] == "harness_seed_perturbation"
    for key in ("noninferiority", "critical_slices", "abstention", "overall_promotion"):
        assert payload[key]["status"] != "pass", key


def _experiment_with(tmp_path: Path, **overrides: Any) -> Path:
    path = _sealed_experiment_for_tiny(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(overrides)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "label_provenance": {
                "origin": "independent",
                "production_expansion_used": False,
                "final_labels_opened": False,
            }
        },
        {"corpus_sha256": "0" * 64},
    ],
    ids=["unsealed-final", "corpus-digest-mismatch"],
)
def test_cli_refuses_unsealed_or_mismatched_experiment(tmp_path: Path, overrides: dict[str, Any]) -> None:
    exp = _experiment_with(tmp_path, **overrides)
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
            str(tmp_path / "out"),
        ]
    )
    assert rc != 0
    assert not (tmp_path / "out" / "result_manifest.json").exists()


def test_verify_acquired_corpus_rejects_content_identity_mismatch(tmp_path: Path) -> None:
    from magicite.eval.external import verify_acquired_corpus_manifest

    data = json.loads(TINY_CORPUS.read_text(encoding="utf-8"))
    data["queries"][0]["query_text"] = data["queries"][0]["query_text"] + " tampered"
    tampered = tmp_path / "corpus_manifest.json"
    tampered.write_text(json.dumps(data), encoding="utf-8")
    _, errors = verify_acquired_corpus_manifest(tampered)
    assert any("content_identity" in e for e in errors), errors


def _acquire_args(archive: Path, digest: str, out: Path, corpus_json: Path) -> list[str]:
    return [
        "acquire-skillret",
        "--archive",
        str(archive),
        "--expected-sha256",
        digest,
        "--license",
        "fixture-only",
        "--revision",
        "x",
        "--corpus-json",
        str(corpus_json),
        "--output",
        str(out),
    ]


def test_acquire_rejects_malformed_expected_sha256(tmp_path: Path) -> None:
    archive = tmp_path / "tiny.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(TINY_CORPUS, arcname="corpus_manifest.json")
    for bad in ("abc", "z" * 64, sha256_path(archive) + "0"):
        out = tmp_path / "out.json"
        assert eval_main.main(_acquire_args(archive, bad, out, TINY_CORPUS)) == 1
        assert not out.exists()


@pytest.mark.parametrize("member_name", ["../escape.json", "/tmp/magicite-abs-escape.json"])
def test_acquire_refuses_path_traversal_archive(tmp_path: Path, member_name: str) -> None:
    archive = tmp_path / "evil.tar"
    payload = TINY_CORPUS.read_bytes()
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    out = tmp_path / "out.json"
    missing_json = tmp_path / "work" / "corpus_manifest.json"
    rc = eval_main.main(_acquire_args(archive, sha256_path(archive), out, missing_json))
    assert rc == 1
    assert not out.exists()
    assert not (tmp_path / "escape.json").exists()
    assert not Path("/tmp/magicite-abs-escape.json").exists()


def test_acquire_refuses_symlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("corpus_manifest.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    out = tmp_path / "out.json"
    missing_json = tmp_path / "work" / "corpus_manifest.json"
    rc = eval_main.main(_acquire_args(archive, sha256_path(archive), out, missing_json))
    assert rc == 1
    assert not out.exists()


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
    assert syn["corpus"]["actual_artifacts"] is True
    assert len(syn["corpus"]["artifact_inventory_sha256"]) == 64
    assert syn["measured_queries"] == 8
    assert syn["warmup_queries"] == 2
    assert syn["process_id"] > 0
    assert len(syn["repetition_results"]) == 1
    assert len(syn["measurements"]["warm_durations_s"]) == 8
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
    assert completed2.returncode == 2
    man = json.loads(man_out.read_text(encoding="utf-8"))
    assert man["status"] == "unavailable"
    assert "role=skill" in man["error"]
    assert man["corpus"]["ga_eligible"] is False
    assert syn["corpus"]["ga_ineligible_reasons"]


_GA_OK: dict[str, Any] = {
    "provider": "production",
    "profile_ga_support_claim": True,
    "corpus_kind": "manifest",
    "envelope_mode": "budget",
    "envelope_budget_ok": True,
    "corpus_path": "/data/licensed/skills-10k/corpus_manifest.json",
    "corpus_license": "CC-BY-4.0",
    "n_candidates": 10_000,
    "profile_corpus_artifacts": 10_000,
}


def test_ga_eligibility_requires_every_condition() -> None:
    from magicite.eval.envelopes import compute_ga_eligibility

    assert compute_ga_eligibility(**_GA_OK)[0] is False  # Assertions alone are not evidence.

    failing: list[dict[str, Any]] = [
        {"provider": "hashing"},
        {"profile_ga_support_claim": False},
        {"corpus_kind": "synthetic"},
        {"envelope_mode": "completeness"},
        {"envelope_budget_ok": False},
        {"envelope_budget_ok": None},
        {"corpus_path": None},
        {"corpus_path": "/repo/docs/evaluation/v1/fixtures/skillret-tiny/corpus_manifest.json"},
        {"corpus_license": None},
        {"corpus_license": "fixture-only"},
        {"n_candidates": None},
        {"n_candidates": 9_999},
    ]
    for override in failing:
        eligible, reasons = compute_ga_eligibility(**{**_GA_OK, **override})
        assert eligible is False, override
        assert reasons, override


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
    report = evaluate_composition_corpus(corpus, {"c1": _plan(status="valid", order=("a",), executable=True)})
    assert report.evidence_class == "structural"
    assert report.to_dict()["structural_efficacy_claim_allowed"] is False
    assert report.n_pass == 1
