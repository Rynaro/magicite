"""AC-S01-01..03: evidence integrity and reproducible evaluation manifests."""

from __future__ import annotations

import copy

import pytest

from magicite.eval.digests import SCHEMA_CLAIM, SCHEMA_EXPERIMENT, sha256_json
from magicite.eval.external import load_offline_skillret_fixture
from magicite.eval.gold import load_structural_gold
from magicite.eval.manifests import (
    Claim,
    ExperimentManifest,
    Prediction,
    QueryRecord,
    ResultManifest,
)
from magicite.eval.metrics import (
    PLAN_F1_DEPRECATED_DIAGNOSTIC,
    abstention_report,
    paired_bootstrap_ci,
)
from magicite.eval.runner import build_result_manifest, prediction_digest_vector, run_predictions
from magicite.eval.validate import (
    validate_claim_integrity,
    validate_corpus_manifest_data,
)


def _hex(n: int = 1) -> str:
    return f"{n:064x}"


def _experiment(*, corpus_sha256: str, labels_sha256: str, seed: int = 7) -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="exp-s01-local/1",
        hypothesis="identical pins reproduce predictions",
        reversal_condition="any digest drift or label change",
        source_commit="deadbeef" * 5,
        dirty_tree_digest=None,
        runner_lock_sha256=_hex(2),
        dependency_lock_sha256=_hex(3),
        corpus_sha256=corpus_sha256,
        labels_sha256=labels_sha256,
        split_sha256=_hex(4),
        primary_metric="hit_at_1",
        secondary_metrics=("mrr", "recall_at_5"),
        statistical_method="paired_group_bootstrap_percentile_95",
        thresholds={"noninferiority_margin_hit_at_1": 0.02},
        seeds={"prediction": seed, "bootstrap": 11},
        embedder_artifact_sha256=_hex(5),
        policy_config_sha256=_hex(6),
        label_provenance={"origin": "author_created", "production_expansion_used": False},
        dataset_license="Apache-2.0",
        timestamp="2026-09-29T00:00:00Z",
        schema=SCHEMA_EXPERIMENT,
    )


def _corpus_from_offline():
    return load_offline_skillret_fixture()


def test_reproducible_predictions() -> None:
    """AC-S01-01: identical pins → identical per-query semantic predictions."""
    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
        seed=42,
    )
    first = run_predictions(experiment, corpus, policy_id="dense-v1")
    second = run_predictions(experiment, corpus, policy_id="dense-v1")
    assert prediction_digest_vector(first) == prediction_digest_vector(second)
    assert [p.to_dict() for p in first] == [p.to_dict() for p in second]
    assert all(p.candidate_ids for p in first)


def test_leakage_rejected() -> None:
    """AC-S01-02: train/test overlap or duplicate query IDs are rejected."""
    corpus = _corpus_from_offline()
    data = corpus.to_dict()

    leaked = copy.deepcopy(data)
    leaked["queries"].append(
        {
            **leaked["queries"][0],
            "query_id": "skillret-tiny-leak",
            "split": "final",
            # same normalized text as development query → leakage
        }
    )
    errors = validate_corpus_manifest_data(leaked)
    assert any("leakage" in error for error in errors)

    dup = copy.deepcopy(data)
    dup["queries"].append(copy.deepcopy(dup["queries"][0]))
    errors = validate_corpus_manifest_data(dup)
    assert any("duplicate query_id" in error for error in errors)

    group_leak = copy.deepcopy(data)
    group_leak["queries"][1]["group_id"] = group_leak["queries"][0]["group_id"]
    # query 0 is development, query 1 is final — shared group_id is leakage
    errors = validate_corpus_manifest_data(group_leak)
    assert any("group_id leakage" in error for error in errors)


def test_claim_digest_failure() -> None:
    """AC-S01-03: changed labels or missing prediction bytes fail claim integrity."""
    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    predictions = run_predictions(experiment, corpus)
    result = build_result_manifest(
        result_id="result-s01-local/1",
        experiment=experiment,
        predictions=predictions,
        aggregates={"hit_at_1": 0.5, "mrr": 0.4},
    )
    claim = Claim(
        claim_id="claim-hit-at-1",
        text_location="docs/evaluation/v1/README.md",
        metric="hit_at_1",
        value=0.5,
        unit="fraction",
        population_split="final",
        result_digest=result.digest(),
        confidence_interval=(0.1, 0.9),
        evidence_class="retrieval",
        status="supported",
        limitations="local fixture only",
        schema=SCHEMA_CLAIM,
    )
    assert (
        validate_claim_integrity(
            claim,
            result=result,
            predictions=predictions,
            experiment=experiment,
            current_labels_sha256=experiment.labels_sha256,
        )
        == []
    )

    # Missing prediction bytes
    missing = validate_claim_integrity(
        claim,
        result=result,
        predictions=[],
        experiment=experiment,
        current_labels_sha256=experiment.labels_sha256,
    )
    assert any("missing prediction bytes" in error for error in missing)

    # Changed labels digest
    changed = validate_claim_integrity(
        claim,
        result=result,
        predictions=predictions,
        experiment=experiment,
        current_labels_sha256=_hex(99),
    )
    assert any("labels digest changed" in error for error in changed)

    # Mismatched published number
    bad_value = Claim(
        claim_id="claim-hit-at-1-bad",
        text_location="docs/evaluation/v1/README.md",
        metric="hit_at_1",
        value=0.99,
        unit="fraction",
        population_split="final",
        result_digest=result.digest(),
        confidence_interval=None,
        evidence_class="retrieval",
        status="supported",
        limitations="local fixture only",
    )
    mismatched = validate_claim_integrity(
        bad_value,
        result=result,
        predictions=predictions,
        experiment=experiment,
        current_labels_sha256=experiment.labels_sha256,
    )
    assert any("does not match result.aggregates" in error for error in mismatched)


def test_structural_to_efficacy_relabel_rejected() -> None:
    claim = {
        "schema": SCHEMA_CLAIM,
        "claim_id": "bad-structural-efficacy",
        "text_location": "docs/evaluation/v0.3-results.json",
        "metric": "hit_at_1",
        "value": 1.0,
        "unit": "fraction",
        "population_split": "structural",
        "result_digest": _hex(1),
        "confidence_interval": None,
        "evidence_class": "structural",
        "status": "supported",
        "limitations": "none",
    }
    from magicite.eval.validate import validate_claim_data

    errors = validate_claim_data(claim)
    assert any("structural evidence cannot support" in error for error in errors)


def test_paired_bootstrap_and_abstention_helpers() -> None:
    interval = paired_bootstrap_ci(
        ["g1", "g1", "g2", "g2"],
        [1.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
        n_resamples=200,
        seed=0,
    )
    assert interval.n_groups == 2
    assert interval.point_estimate == pytest.approx(0.5)
    assert interval.low <= interval.point_estimate <= interval.high

    report = abstention_report(
        answerable_selected=[True, True, False, True],
        no_match_selected=[False, False, True, False],
    )
    assert report.coverage == pytest.approx(0.75)
    assert report.false_selection_rate == pytest.approx(0.25)
    assert report.coverage_wilson_low is not None
    assert report.false_selection_wilson_high is not None


def test_offline_skillret_fixture_loads() -> None:
    corpus = load_offline_skillret_fixture()
    assert corpus.corpus_id.startswith("skillret-tiny")
    assert validate_corpus_manifest_data(corpus.to_dict()) == []


def test_plan_f1_deprecated_constant_stable() -> None:
    assert PLAN_F1_DEPRECATED_DIAGNOSTIC == "deprecated_diagnostic_circular_gold"


def test_result_manifest_prediction_binding() -> None:
    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    predictions = run_predictions(experiment, corpus)
    result = build_result_manifest(
        result_id="r1",
        experiment=experiment,
        predictions=predictions,
        aggregates={"hit_at_1": 0.0},
    )
    assert isinstance(result, ResultManifest)
    assert result.predictions_sha256 == sha256_json([p.to_dict() for p in predictions])
    assert len(result.prediction_digests) == len(predictions)
    assert all(isinstance(p, Prediction) for p in predictions)


def test_query_record_roundtrip_fields() -> None:
    q = QueryRecord(
        query_id="q1",
        query_text="hello",
        split="development",
        relevance={"a": 1.0},
        group_id="g",
        provenance={"production_expansion_used": False},
        accepted_plans=(("a", "b"),),
        partial_order=(("a", "b"),),
    )
    assert q.to_dict()["accepted_plans"] == [["a", "b"]]


def test_composition_corpus_loads_as_structural_gold() -> None:
    gold = load_structural_gold("docs/evaluation/composition-v0.3.json")
    assert gold.corpus_id
    assert gold.label_policy["production_expansion_used"] is False
    plans = gold.expected_plans_by_case_id()
    assert plans["compose-001"] == ["build-package", "run-unit-tests", "publish-package"]


def test_historical_supported_claim_fails_new_run_gate() -> None:
    """ATLAS #1: historical evidence cannot satisfy status=supported."""
    from magicite.eval.validate import claim_eligible_for_new_run_gate

    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    # Seal final labels so corpus binding is not the failure mode under test.
    object.__setattr__(
        experiment,
        "label_provenance",
        {**experiment.label_provenance, "final_labels_opened": True},
    )
    predictions = run_predictions(experiment, corpus)
    result = build_result_manifest(
        result_id="hist-result/1",
        experiment=experiment,
        predictions=predictions,
        aggregates={"hit_at_1": 0.5},
    )
    claim = Claim(
        claim_id="historical-supported",
        text_location="docs/evaluation/v0.3-results.json",
        metric="hit_at_1",
        value=0.5,
        unit="fraction",
        population_split="final",
        result_digest=result.digest(),
        confidence_interval=None,
        evidence_class="historical",
        status="supported",
        limitations="carried-forward",
        schema=SCHEMA_CLAIM,
    )
    gate = claim_eligible_for_new_run_gate(claim)
    assert any("historical evidence cannot satisfy a new-run gate" in e for e in gate)
    errors = validate_claim_integrity(
        claim,
        result=result,
        predictions=predictions,
        experiment=experiment,
        current_labels_sha256=experiment.labels_sha256,
        corpus=corpus,
    )
    assert any("historical evidence cannot satisfy a new-run gate" in e for e in errors)


def test_omitted_predictions_fail_when_result_present() -> None:
    """ATLAS #2: predictions=None must fail when a result is provided."""
    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    predictions = run_predictions(experiment, corpus)
    result = build_result_manifest(
        result_id="omit-pred/1",
        experiment=experiment,
        predictions=predictions,
        aggregates={"hit_at_1": 0.5},
    )
    claim = Claim(
        claim_id="omit-preds",
        text_location="docs/evaluation/v1/README.md",
        metric="hit_at_1",
        value=0.5,
        unit="fraction",
        population_split="final",
        result_digest=result.digest(),
        confidence_interval=None,
        evidence_class="retrieval",
        status="supported",
        limitations="fixture",
        schema=SCHEMA_CLAIM,
    )
    errors = validate_claim_integrity(
        claim,
        result=result,
        predictions=None,
        experiment=experiment,
        current_labels_sha256=experiment.labels_sha256,
    )
    assert any("missing prediction bytes" in e for e in errors)


def test_claim_binds_experiment_and_corpus_digests() -> None:
    """ATLAS #3: result must bind experiment digest; corpus must match pin."""
    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    object.__setattr__(
        experiment,
        "label_provenance",
        {**experiment.label_provenance, "final_labels_opened": True},
    )
    predictions = run_predictions(experiment, corpus)
    result = build_result_manifest(
        result_id="bind/1",
        experiment=experiment,
        predictions=predictions,
        aggregates={"hit_at_1": 0.5},
    )
    claim = Claim(
        claim_id="bind-ok",
        text_location="docs/evaluation/v1/README.md",
        metric="hit_at_1",
        value=0.5,
        unit="fraction",
        population_split="final",
        result_digest=result.digest(),
        confidence_interval=None,
        evidence_class="retrieval",
        status="supported",
        limitations="fixture",
        schema=SCHEMA_CLAIM,
    )
    assert (
        validate_claim_integrity(
            claim,
            result=result,
            predictions=predictions,
            experiment=experiment,
            current_labels_sha256=experiment.labels_sha256,
            corpus=corpus,
        )
        == []
    )

    # Same labels_sha256, different corpus_sha256 → must fail.
    drifted = _experiment(
        corpus_sha256=_hex(77),
        labels_sha256=experiment.labels_sha256,
    )
    object.__setattr__(
        drifted,
        "label_provenance",
        {**drifted.label_provenance, "final_labels_opened": True},
    )
    # Rebuild result against drifted experiment so labels match but corpus pin differs
    # from supplied corpus bytes.
    bad = validate_claim_integrity(
        claim,
        result=result,
        predictions=predictions,
        experiment=drifted,
        current_labels_sha256=drifted.labels_sha256,
        corpus=corpus,
    )
    assert any("experiment_sha256 does not match" in e for e in bad)
    assert any("corpus digest does not match experiment.corpus_sha256" in e for e in bad)


def test_circular_plan_f1_cannot_be_supported() -> None:
    """ATLAS #4: deprecated circular Plan F1 is ineligible for status=supported."""
    from magicite.eval.validate import claim_eligible_for_new_run_gate

    claim = {
        "schema": SCHEMA_CLAIM,
        "claim_id": "circular-plan",
        "text_location": "bench",
        "metric": "plan_f1",
        "value": 1.0,
        "unit": "fraction",
        "population_split": "development",
        "result_digest": _hex(1),
        "confidence_interval": None,
        "evidence_class": "structural",
        "status": "supported",
        "limitations": "diagnostic",
        "plan_f1_status": PLAN_F1_DEPRECATED_DIAGNOSTIC,
    }
    errors = claim_eligible_for_new_run_gate(claim)
    assert any("deprecated_diagnostic_circular_gold" in e for e in errors)


def test_expand_gold_requires_explicit_diagnostic_flag(cfg, db_conn, embedder) -> None:
    """ATLAS #4: default path refuses circular expand()-as-gold."""
    from magicite.core import registry as registry_mod
    from magicite.eval import bench as bench_mod

    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    queries = [
        bench_mod.LabelledQuery(
            query="rollback proton for a steam game",
            expected_top1="proton-ge-proton-downgrade",
        )
    ]
    with pytest.raises(ValueError, match="allow_circular_diagnostic_gold"):
        bench_mod.run_baseline(cfg, db_conn, embedder, "d", queries)


def test_final_holdout_requires_seal() -> None:
    """ATLAS #5: unsealed final/holdout queries are rejected."""
    from magicite.eval.validate import validate_experiment_corpus_seal

    corpus = _corpus_from_offline()
    experiment = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    assert any(q.split in {"final", "holdout"} for q in corpus.queries)
    errors = validate_experiment_corpus_seal(experiment, corpus)
    assert any("final_labels_opened=true" in e for e in errors)

    sealed = _experiment(
        corpus_sha256=corpus.content_identity_sha256,
        labels_sha256=corpus.content_identity_sha256,
    )
    object.__setattr__(
        sealed,
        "label_provenance",
        {**sealed.label_provenance, "final_labels_opened": True},
    )
    assert validate_experiment_corpus_seal(sealed, corpus) == []


def test_v03_checker_rejects_non_historical_evidence_class(tmp_path) -> None:
    """ATLAS #6: superseding v0.3 results must not use non-historical classes."""
    import json
    from pathlib import Path

    from scripts import check_evaluation_results as checker

    src = Path("docs/evaluation/v0.3-results.json")
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["evidence_class"] = "retrieval"
    forged = tmp_path / "forged-v03.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    # Point corpus path still relative to repo root via checker.ROOT
    errors = checker.check_historical_v03(forged, payload)
    assert any("evidence_class to historical" in e for e in errors)
