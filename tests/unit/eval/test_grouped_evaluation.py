"""Synthetic statistical mechanics only; never empirical release evidence."""

import math
from copy import deepcopy

import pytest

from magicite.eval import grouped_evaluation as g
from magicite.eval.metrics import fixed_bin_ece, query_weighted_bootstrap


def protocol():
    return {
        "schema": g.PROTOCOL_SCHEMA,
        "experiment_id": "synthetic-controls",
        "estimand": "query-weighted-conditional-on-frozen-group-sizes",
        "group_unit": "related-source-component",
        "group_assumptions": {
            "independent_original_groups": True,
            "fixed_exogenous_group_sizes": True,
            "evidence_digests": ["a" * 64],
        },
        "seed": 7,
        "n_resamples": 10000,
        "confidence_level": 0.95,
        "weighting": "whole-group-ratio",
        "power_plan": None,
        "critical_slices": [],
        "abstention_method": "grouped-percentile-hoeffding",
        "probability_method": "isotonic-margin-top1/1",
        "ece_edges": g.EDGES,
        "comparison_budget": None,
        "paired_policy_ids": ["dense-v1", "experimental/sparse-v1"],
    }


def test_unequal_group_query_weighted():
    result = query_weighted_bootstrap(
        ["a", "a", "a", "b"], [1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0], seed=7
    )
    assert result["point"] == 0.5
    assert result["low"] == -1 and result["high"] == 1
    assert result["n_groups"] == 2 and result["n_queries"] == 4
    assert result["n_eff"] == 1.6
    assert result == query_weighted_bootstrap(
        ["a", "a", "a", "b"], [1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0], seed=7
    )


def test_transitive_aliases_and_singleton():
    rows = [
        {"query_id": "q1", "split": "final", "groups": ["task:a", "source:b"]},
        {"query_id": "q2", "split": "final", "groups": ["source:b", "session:c"]},
        {"query_id": "q3", "split": "final", "groups": ["session:c"]},
    ]
    projection = g.canonical_projection(rows, protocol())
    assert len({r["group_id"] for r in projection["rows"]}) == 1
    rows[-1]["split"] = "calibration"
    with pytest.raises(ValueError, match="leaks"):
        g.canonical_projection(rows, protocol())


@pytest.mark.parametrize(
    "field,value",
    [("seed", True), ("n_resamples", True), ("n_resamples", 100), ("ece_edges", [0, 1]), ("unknown", 1)],
)
def test_protocol_rejects(field, value):
    p = protocol()
    p[field] = value
    with pytest.raises(ValueError):
        g.validate_protocol(p)


def test_missing_groups_not_query_independence():
    with pytest.raises(ValueError):
        g.canonical_projection([{"query_id": "q", "split": "final", "groups": []}], protocol())


def test_critical_null_direction():
    assert g.critical_inferiority_p(0, [0.5, 0.5]) == 1
    assert g.critical_inferiority_p(-0.1, [0.5, 0.5]) == pytest.approx(math.exp(-(0.05**2)))


def test_target_bounds_retain_uncertainty():
    groups = [str(i) for i in range(40)]
    false = g.grouped_abstention(groups, [False] * 40, seed=7, upper=True)
    coverage = g.grouped_abstention(groups, [True] * 40, seed=7, upper=False)
    assert false["bound"] > 0 and coverage["bound"] < 1
    assert g.grouped_abstention([], [], seed=7, upper=True)["bound"] is None
    with pytest.raises(ValueError):
        g.grouped_abstention(["same"] * 2, [True] * 2, seed=7, upper=True, method="independent-wilson")


def test_no_power_support_from_boolean_receipt():
    p = protocol()
    p["power_plan"] = {
        "report": {
            "status": "supported_conditional_model",
            "selected_group_floor": 30,
            "n_resamples": 10000,
            "repetitions": 200,
            "diagnostic": False,
        },
        "sha256": "irrelevant",
    }
    assert g.support(p, 100)["status"] == "inconclusive"


def test_ece_frozen_bins():
    result = fixed_bin_ece([0.0, 0.1, 1.0, None], [False, True, True, False])
    assert result["n"] == 3 and result["excluded"] == 1
    assert result["ECE"] == pytest.approx(0.3)
    assert result["bins"][1]["count"] == 1 and result["bins"][9]["count"] == 1
    assert result["release_threshold"] is None


def test_development_power_diagnostic_and_rejections():
    rows = [
        {
            "query_id": str(i),
            "group_id": str(i),
            "family": "development",
            "candidate_hit": i % 3 != 0,
            "incumbent_hit": i % 3 == 0,
        }
        for i in range(30)
    ]
    kw = {
        "group_floors": [30, 40],
        "seed": 3,
        "repetitions": 2,
        "n_resamples": 10,
        "identities": {"source": "synthetic"},
        "assumptions": {
            "independent_original_groups": True,
            "development_representative": True,
            "evidence_digests": ["a" * 64],
        },
    }
    result = g.plan_grouped_power(rows, **kw)
    g.validate_power_report(result)
    assert result["diagnostic"] and result["selected_group_floor"] is None
    bad = deepcopy(rows)
    bad[0]["family"] = "final"
    with pytest.raises(ValueError):
        g.plan_grouped_power(bad, **kw)
    with pytest.raises(ValueError):
        g.plan_grouped_power(rows + [rows[0]], **kw)
    bad = deepcopy(result)
    bad["grid"][0]["simultaneous_lower"] = 1
    with pytest.raises(ValueError):
        g.validate_power_report(bad)


@pytest.fixture(scope="module")
def supported_power():
    # Complete synthetic raw-arm/projection mechanics; no execution/data attestation.
    from magicite.core.comparison_budget import ComparisonBudget
    from magicite.eval.digests import sha256_json

    budget = ComparisonBudget(10, 5, 20, 5, 1)
    frame = protocol()
    frame["comparison_budget"] = budget.to_dict()
    frame["critical_slices"] = [{"name": "critical", "query_ids": ["f" + str(i) for i in range(40)]}]
    groups = g.canonical_projection(
        [{"query_id": str(i), "split": "development", "groups": ["source:" + str(i)]} for i in range(30)]
        + [
            {"query_id": "f" + str(i), "split": "final", "groups": ["final-source:" + str(i)]}
            for i in range(40)
        ],
        frame,
    )["rows"]
    groups = [row for row in groups if row["family"] == "development"]
    labels = [{"query_id": str(i), "relevance": {"correct": 1}} for i in range(30)]
    binding = {
        "source_inputs": {"synthetic.py": "a" * 64},
        "model_manifest": {"synthetic_model": True},
        "runtime": {
            "config_digest": "a" * 64,
            "comparison_config_digest": "a" * 64,
            "registry_digest": "a" * 64,
            "generation": "a" * 64,
            "snapshot": "a" * 64,
            "rank_depth": 5,
        },
    }
    identities = {
        "source_digest": sha256_json(binding["source_inputs"]),
        "model_digest": sha256_json(binding["model_manifest"]),
        "config_digest": "a" * 64,
        "comparison_budget_digest": budget.digest,
        "candidate_policy_id": "experimental/sparse-v1",
        "incumbent_policy_id": "dense-v1",
        "development_group_projection_digest": sha256_json(groups),
        "protocol_frame_digest": sha256_json(frame),
    }
    arms = {}
    for arm in ("candidate", "incumbent"):
        arms[arm] = [
            {
                "query_id": str(i),
                "source_digest": identities["source_digest"],
                "model_manifest_digest": identities["model_digest"],
                "config_digest": "a" * 64,
                "comparison_config_digest": "a" * 64,
                "comparison_budget_digest": budget.digest,
                "policy_id": identities[arm + "_policy_id"],
                "registry_digest": "a" * 64,
                "index_generation_id": "a" * 64,
                "snapshot_id": "a" * 64,
                "rank_depth": 5,
                "status": "selected",
                "raw_candidate_ids": ["correct" if arm == "candidate" or i % 4 == 0 else "incorrect"],
                "raw_scores": [2.0],
            }
            for i in range(30)
        ]
    rows = [
        {
            "query_id": str(i),
            "group_id": next(r["group_id"] for r in groups if r["query_id"] == str(i)),
            "family": "development",
            "candidate_hit": True,
            "incumbent_hit": i % 4 == 0,
        }
        for i in sorted(range(30), key=str)
    ]
    assumptions = {
        "independent_original_groups": True,
        "development_representative": True,
        "evidence_digests": ["b" * 64],
    }
    body = {
        "schema": "magicite/development-power-input/1",
        "classification": "nonqualifying_local_evaluation",
        "qualifying": False,
        "binding": binding,
        "protocol_frame": frame,
        "development_groups": groups,
        "development_labels": labels,
        "predictions": arms,
        "development": rows,
        "identities": identities,
        "power_assumptions": {"development_representative": True, "evidence_digests": ["b" * 64]},
    }
    envelope = {"identity": sha256_json(body), "input": body}
    return g.plan_grouped_power(
        rows,
        group_floors=[30, 40],
        seed=7,
        repetitions=200,
        identities=identities,
        assumptions=assumptions,
        bound_input=envelope,
    )


def test_supported_simulation_runs_actual_primary(supported_power):
    g.validate_power_report(supported_power)
    assert supported_power["selected_group_floor"] == 30
    assert supported_power["n_resamples"] == 10000 and supported_power["repetitions"] == 200
    assert not supported_power["diagnostic"]
    assert len(supported_power["grid"]) == 2
    assert all(len(row["seeds"]) == 200 for row in supported_power["grid"])
    assert supported_power["n_original_development_groups"] == 30


def paired_fixture(power):
    from magicite.core.comparison_budget import ComparisonBudget
    from magicite.eval.digests import sha256_json

    p = protocol()
    b = ComparisonBudget(10, 5, 20, 5, 1)
    p["comparison_budget"] = b.to_dict()
    p["power_plan"] = {"report": power, "sha256": sha256_json(power)}
    p["critical_slices"] = [{"name": "critical", "query_ids": ["f" + str(i) for i in range(40)]}]
    projection = g.canonical_projection(
        [{"query_id": "f" + str(i), "split": "final", "groups": ["source:" + str(i)]} for i in range(40)], p
    )
    development = g.canonical_projection(
        [{"query_id": str(i), "split": "development", "groups": ["source:" + str(i)]} for i in range(30)]
        + [
            {"query_id": "f" + str(i), "split": "final", "groups": ["final-source:" + str(i)]}
            for i in range(40)
        ],
        p,
    )
    projection = development
    base = {
        k: "a" * 64
        for k in (
            "config_digest",
            "comparison_config_digest",
            "model_digest",
            "model_manifest_digest",
            "registry_digest",
            "index_generation_id",
            "snapshot_id",
            "eligibility_digest",
            "body_availability_digest",
            "query_context_digest",
            "source_digest",
            "custody_digest",
            "iteration_order_digest",
        )
    }
    base["comparison_budget_digest"] = b.digest
    base["source_digest"] = power["identities"]["source_digest"]
    base["model_manifest_digest"] = power["identities"]["model_digest"]
    candidate = [
        {
            **base,
            "query_id": "f" + str(i),
            "policy_id": "experimental/sparse-v1",
            "status": "selected",
            "hit": True,
        }
        for i in range(40)
    ]
    incumbent = [
        {**base, "query_id": "f" + str(i), "policy_id": "dense-v1", "status": "selected", "hit": False}
        for i in range(40)
    ]
    return projection, candidate, incumbent


def test_complete_paired_family_and_timeout_denominator(supported_power):
    projection, candidate, incumbent = paired_fixture(supported_power)
    result = g.paired_report(
        projection,
        candidate,
        incumbent,
        identities={"source_digest": supported_power["identities"]["source_digest"]},
    )
    assert result["primary"]["point"] == 1
    assert result["primary"]["noninferiority"] == "pass"
    assert result["holm"]["status"] == "pass"
    assert result["qualifying"] is False and result["E2"] == result["E3"] == "UNEVALUATED"
    candidate[0]["status"] = "timeout"
    result = g.paired_report(
        projection,
        candidate,
        incumbent,
        identities={"source_digest": supported_power["identities"]["source_digest"]},
    )
    assert result["primary"]["point"] == 39 / 40
    assert result["primary"]["n_queries"] == 40


@pytest.mark.parametrize("mutation", ["budget", "snapshot", "source", "missing", "hit-type"])
def test_comparison_substitution_rejected(supported_power, mutation):
    projection, candidate, incumbent = paired_fixture(supported_power)
    if mutation == "missing":
        candidate.pop()
    elif mutation == "budget":
        candidate[0]["comparison_budget_digest"] = "wrong"
    elif mutation == "snapshot":
        candidate[0]["snapshot_id"] = "wrong"
    elif mutation == "source":
        candidate[0]["source_digest"] = "wrong"
    else:
        candidate[0]["hit"] = 1
    with pytest.raises(ValueError):
        g.paired_report(
            projection,
            candidate,
            incumbent,
            identities={"source_digest": supported_power["identities"]["source_digest"]},
        )


def test_foreign_power_binding_remains_inconclusive(supported_power):
    projection, candidate, incumbent = paired_fixture(supported_power)
    from magicite.eval.digests import sha256_json

    power = deepcopy(supported_power)
    power["identities"]["source_digest"] = "f" * 64
    projection["protocol"]["power_plan"] = {"report": power, "sha256": sha256_json(power)}
    report = g.paired_report(
        projection,
        candidate,
        incumbent,
        identities={"source_digest": supported_power["identities"]["source_digest"]},
    )
    assert report["primary"]["noninferiority"] == "inconclusive"
    assert report["primary"]["support"]["power_identity_matches"] is False
    assert report["holm"]["status"] == "inconclusive"


@pytest.mark.parametrize("mutation", ["duplicate", "split", "group", "membership"])
def test_projection_substitution_does_not_inflate(supported_power, mutation):
    projection, candidate, incumbent = paired_fixture(supported_power)
    if mutation == "duplicate":
        projection["rows"].append(deepcopy(projection["rows"][0]))
    elif mutation == "split":
        projection["rows"][0]["split"] = "unknown"
    elif mutation == "group":
        projection["rows"][0]["group_id"] = "query-is-not-independent"
    else:
        next(r for r in projection["rows"] if r["family"] == "final")["critical_slices"] = []
    with pytest.raises(ValueError):
        g.paired_report(
            projection,
            candidate,
            incumbent,
            identities={"source_digest": supported_power["identities"]["source_digest"]},
        )


def test_unrelated_power_policy_pair_inconclusive(supported_power):
    projection, candidate, incumbent = paired_fixture(supported_power)
    from magicite.eval.digests import sha256_json

    report = deepcopy(supported_power)
    report["identities"]["candidate_policy_id"] = "experimental/trigger-v1"
    projection["protocol"]["power_plan"] = {"report": report, "sha256": sha256_json(report)}
    result = g.paired_report(
        projection,
        candidate,
        incumbent,
        identities={"source_digest": supported_power["identities"]["source_digest"]},
    )
    assert result["primary"]["noninferiority"] == "inconclusive"
