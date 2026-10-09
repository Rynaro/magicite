"""Frozen grouped inference. Numeric diagnostics never authorize release."""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

from magicite.eval.digests import sha256_json
from magicite.eval.metrics import query_weighted_bootstrap, wilson_interval

EDGES = [i / 10 for i in range(11)]
PROTOCOL_SCHEMA = "magicite/grouped-evaluation-protocol/1"


def validate_protocol(value: dict) -> dict:
    fields = {
        "schema",
        "experiment_id",
        "estimand",
        "group_unit",
        "group_assumptions",
        "seed",
        "n_resamples",
        "confidence_level",
        "weighting",
        "power_plan",
        "critical_slices",
        "abstention_method",
        "probability_method",
        "ece_edges",
        "comparison_budget",
        "paired_policy_ids",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("protocol fields differ from frozen schema")
    fixed = {
        "schema": PROTOCOL_SCHEMA,
        "estimand": "query-weighted-conditional-on-frozen-group-sizes",
        "group_unit": "related-source-component",
        "n_resamples": 10000,
        "confidence_level": 0.95,
        "weighting": "whole-group-ratio",
        "probability_method": "isotonic-margin-top1/1",
        "ece_edges": EDGES,
    }
    if any(value[k] != v for k, v in fixed.items()) or type(value["n_resamples"]) is not int:
        raise ValueError("unsupported statistical protocol")
    if (
        not isinstance(value["experiment_id"], str)
        or not value["experiment_id"]
        or type(value["seed"]) is not int
        or value["seed"] < 0
    ):
        raise ValueError("invalid protocol identity/seed")
    if any(type(v) not in (int, float) for v in value["ece_edges"]):
        raise ValueError("ECE edges must be numeric")
    assumptions = value["group_assumptions"]
    if (
        not isinstance(assumptions, dict)
        or set(assumptions)
        != {"independent_original_groups", "fixed_exogenous_group_sizes", "evidence_digests"}
        or any(
            type(assumptions[k]) is not bool
            for k in ("independent_original_groups", "fixed_exogenous_group_sizes")
        )
    ):
        raise ValueError("invalid group assumptions")
    import re

    if (
        not isinstance(assumptions["evidence_digests"], list)
        or not assumptions["evidence_digests"]
        or any(
            not isinstance(d, str) or not re.fullmatch("[0-9a-f]{64}", d)
            for d in assumptions["evidence_digests"]
        )
    ):
        raise ValueError("assumption evidence digests required")
    slices = value["critical_slices"]
    if not isinstance(slices, list) or len({s.get("name") for s in slices if isinstance(s, dict)}) != len(
        slices
    ):
        raise ValueError("duplicate/invalid critical slices")
    for s in slices:
        if (
            not isinstance(s, dict)
            or set(s) != {"name", "query_ids"}
            or not isinstance(s["name"], str)
            or not s["name"]
            or not isinstance(s["query_ids"], list)
            or not s["query_ids"]
            or any(not isinstance(q, str) or not q for q in s["query_ids"])
            or len(set(s["query_ids"])) != len(s["query_ids"])
        ):
            raise ValueError("invalid frozen slice membership")
    policies = value["paired_policy_ids"]
    if (
        not isinstance(policies, list)
        or len(policies) < 2
        or len(set(policies)) != len(policies)
        or any(not isinstance(p, str) or not p for p in policies)
    ):
        raise ValueError("paired policies required")
    if value["abstention_method"] not in {"independent-wilson", "grouped-percentile-hoeffding"}:
        raise ValueError("unsupported abstention method")
    if value["comparison_budget"] is not None:
        from magicite.core.comparison_budget import ComparisonBudget

        ComparisonBudget.from_dict(value["comparison_budget"])
    power = value["power_plan"]
    if power is not None and (
        not isinstance(power, dict)
        or set(power) != {"report", "sha256"}
        or sha256_json(power["report"]) != power["sha256"]
    ):
        raise ValueError("power report integrity mismatch")
    sha256_json(value)  # finite JSON only
    return value


def canonical_projection(rows: list[dict], protocol: dict) -> dict:
    validate_protocol(protocol)
    if any(
        not isinstance(r, dict)
        or not isinstance(r.get("query_id"), str)
        or not r["query_id"]
        or not isinstance(r.get("split"), str)
        for r in rows
    ):
        raise ValueError("invalid projection query identity/split")
    parent: dict[str, str] = {}

    def find(k: str) -> str:
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for row in rows:
        keys = row.get("groups")
        if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not k for k in keys):
            raise ValueError("grouping unavailable; query identity is not independence")
        find(keys[0])
        for key in keys[1:]:
            parent[find(key)] = find(keys[0])
    components: dict[str, list[str]] = {}
    for key in sorted(parent):
        components.setdefault(find(key), []).append(key)
    result = []
    ids: set[str] = set()
    owners: dict[str, str] = {}
    from magicite.eval.data_readiness import FAMILIES

    for row in sorted(rows, key=lambda r: r["query_id"]):
        qid = row["query_id"]
        if qid in ids or row["split"] not in FAMILIES:
            raise ValueError("duplicate query or unknown split")
        ids.add(qid)
        component = sha256_json(components[find(row["groups"][0])])
        family = FAMILIES[row["split"]]
        if component in owners and owners[component] != family:
            raise ValueError("connected group leaks partitions")
        owners[component] = family
        result.append(
            {
                "query_id": qid,
                "split": row["split"],
                "family": family,
                "group_id": component,
                "critical_slices": [s["name"] for s in protocol["critical_slices"] if qid in s["query_ids"]],
            }
        )
    if any(not set(s["query_ids"]).issubset(ids) for s in protocol["critical_slices"]):
        raise ValueError("slice contains unknown query")
    return {
        "schema": "magicite/statistical-group-projection/1",
        "rows": result,
        "protocol": protocol,
        "limitations": ["Group declarations and evidence hashes do not prove independence."],
    }


def support(protocol: dict, groups: int, *, n_resamples: int = 10000, identities: dict | None = None) -> dict:
    a = protocol["group_assumptions"]
    power = protocol["power_plan"]
    report = power["report"] if power else {}
    try:
        validate_power_report(report)
    except (ValueError, TypeError, KeyError):
        report = {}
    binding_keys = (
        "source_digest",
        "model_digest",
        "config_digest",
        "comparison_budget_digest",
        "candidate_policy_id",
        "incumbent_policy_id",
        "protocol_frame_digest",
        "development_group_projection_digest",
    )
    binding_matches = identities is not None and all(
        k in identities and k in report.get("identities", {}) and report["identities"][k] == identities[k]
        for k in binding_keys
    )
    floor = report.get("selected_group_floor")
    ok = (
        binding_matches
        and a["independent_original_groups"]
        and a["fixed_exogenous_group_sizes"]
        and groups >= 30
        and type(floor) is int
        and groups >= floor
        and report.get("status") == "supported_conditional_model"
        and report.get("n_resamples") == 10000
        and report.get("repetitions", 0) >= 200
        and not report.get("diagnostic", True)
        and n_resamples == 10000
    )
    return {
        "status": "supported_assumptions" if ok else "inconclusive",
        "n_groups": groups,
        "required_floor": floor,
        "independence_proven": False,
        "power_identity_matches": binding_matches,
    }


def critical_inferiority_p(point: float, weights: list[float], margin: float = -0.05) -> float:
    return 1.0 if point >= margin else math.exp(-((margin - point) ** 2) / (2 * sum(w * w for w in weights)))


def grouped_abstention(
    groups: list[str],
    selected: list[bool],
    *,
    seed: int,
    upper: bool,
    method: str = "grouped-percentile-hoeffding",
) -> dict:
    if not groups:
        return {"status": "inconclusive", "n_queries": 0, "n_groups": 0, "bound": None}
    if any(type(v) is not bool for v in selected):
        raise ValueError("boolean outcomes required")
    report = query_weighted_bootstrap(groups, [float(v) for v in selected], [0.0] * len(selected), seed=seed)
    if method == "independent-wilson":
        if len(set(groups)) != len(groups):
            raise ValueError("Wilson requires one independent case per group")
        lo, hi = wilson_interval(
            sum(selected), len(selected), z=1.6448536269514722 if upper else 1.959963984540054
        )
        report.update({"bound": hi if upper else lo, "method": method})
    else:
        s = sum(w * w for w in report["weights"])
        guard = (
            min(1.0, report["point"] + math.sqrt(s * math.log(20) / 2))
            if upper
            else max(0.0, report["point"] - math.sqrt(s * math.log(40) / 2))
        )
        boot = report["upper_one_sided"] if upper else report["low"]
        report.update(
            {
                "bootstrap_bound": boot,
                "support_guard": guard,
                "bound": max(boot, guard) if upper else min(boot, guard),
            }
        )
    return report


def paired_report(
    projection: dict, candidate: list[dict], incumbent: list[dict], *, identities: dict
) -> dict:
    if (
        not isinstance(projection, dict)
        or set(projection) != {"schema", "rows", "protocol", "limitations"}
        or projection["schema"] != "magicite/statistical-group-projection/1"
        or not isinstance(projection["rows"], list)
    ):
        raise ValueError("canonical statistical projection required")
    protocol = validate_protocol(projection["protocol"])
    import re

    from magicite.eval.data_readiness import FAMILIES

    projection_ids = set()
    for row in projection["rows"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"query_id", "split", "family", "group_id", "critical_slices"}
            or not isinstance(row["query_id"], str)
            or not row["query_id"]
            or row["query_id"] in projection_ids
            or not isinstance(row["split"], str)
            or FAMILIES.get(row["split"]) != row["family"]
            or not isinstance(row["group_id"], str)
            or not re.fullmatch("[0-9a-f]{64}", row["group_id"])
        ):
            raise ValueError("invalid/duplicate projection row")
        projection_ids.add(row["query_id"])
        memberships = [sl["name"] for sl in protocol["critical_slices"] if row["query_id"] in sl["query_ids"]]
        if row["critical_slices"] != memberships:
            raise ValueError("projection critical membership mismatch")
    final = [r for r in projection["rows"] if r["family"] == "final"]
    expected = {r["query_id"] for r in final}
    arms = []
    keys = (
        "comparison_config_digest",
        "model_digest",
        "model_manifest_digest",
        "registry_digest",
        "index_generation_id",
        "snapshot_id",
        "comparison_budget_digest",
        "eligibility_digest",
        "body_availability_digest",
        "query_context_digest",
        "source_digest",
        "custody_digest",
        "iteration_order_digest",
    )
    for rows in (candidate, incumbent):
        mapped = {r["query_id"]: r for r in rows}
        if len(mapped) != len(rows) or set(mapped) != expected:
            raise ValueError("complete exact paired query membership required")
        arms.append(mapped)
    if (
        any(len({r["policy_id"] for r in rows}) != 1 for rows in (candidate, incumbent))
        or incumbent[0]["policy_id"] != "dense-v1"
    ):
        raise ValueError("each actual arm must have one frozen policy, incumbent dense-v1")
    for qid in expected:
        a, b = arms[0][qid], arms[1][qid]
        from magicite.core.comparison_budget import ComparisonBudget

        frozen_budget = protocol["comparison_budget"]
        if (
            frozen_budget is None
            or a.get("comparison_budget_digest") != ComparisonBudget.from_dict(frozen_budget).digest
        ):
            raise ValueError("rows do not use frozen common allowance")
        if a.get("source_digest") != identities.get("source_digest"):
            raise ValueError("unbound actual source identity")
        for row in (a, b):
            if type(row.get("hit")) is not bool or row.get("status") not in {
                "selected",
                "abstained",
                "error",
                "timeout",
            }:
                raise ValueError("invalid paired outcome")
        if any(k not in a or k not in b or a[k] is None or a[k] != b[k] for k in keys):
            raise ValueError("comparison environment/budget identity mismatch")
        if a["policy_id"] == b["policy_id"] or {a["policy_id"], b["policy_id"]} - set(
            protocol["paired_policy_ids"]
        ):
            raise ValueError("paired policy identity mismatch")

    def interval(subset: list[dict]) -> dict:
        return query_weighted_bootstrap(
            [r["group_id"] for r in subset],
            [
                float(arms[0][r["query_id"]]["hit"] and arms[0][r["query_id"]]["status"] == "selected")
                for r in subset
            ],
            [
                float(arms[1][r["query_id"]]["hit"] and arms[1][r["query_id"]]["status"] == "selected")
                for r in subset
            ],
            seed=protocol["seed"],
        )

    primary = interval(final)
    power_subject = {
        "source_digest": identities.get("source_digest"),
        "config_digest": candidate[0].get("comparison_config_digest"),
        "model_digest": candidate[0].get("model_manifest_digest"),
        "comparison_budget_digest": candidate[0].get("comparison_budget_digest"),
        "candidate_policy_id": candidate[0]["policy_id"],
        "incumbent_policy_id": incumbent[0]["policy_id"],
        "protocol_frame_digest": sha256_json({**protocol, "power_plan": None}),
        "development_group_projection_digest": sha256_json(
            [r for r in projection["rows"] if r["family"] == "development"]
        ),
    }
    primary["support"] = support(protocol, primary["n_groups"], identities=power_subject)
    primary["noninferiority"] = (
        "inconclusive"
        if primary["support"]["status"] != "supported_assumptions"
        else "pass"
        if primary["low"] >= -0.02
        else "fail"
    )
    primary["improvement"] = (
        "inconclusive"
        if primary["support"]["status"] != "supported_assumptions"
        else "pass"
        if primary["low"] > 0 and primary["point"] >= 0.01
        else "fail"
    )
    slices = []
    for s in protocol["critical_slices"]:
        subset = [r for r in final if r["query_id"] in s["query_ids"]]
        if len(subset) != len(s["query_ids"]):
            raise ValueError("critical family missing final observations")
        ci = interval(subset)
        ci.update(
            {
                "name": s["name"],
                "p_value": critical_inferiority_p(ci["point"], ci["weights"]),
                "support": support(protocol, ci["n_groups"], identities=power_subject),
            }
        )
        slices.append(ci)
    from magicite.eval.metrics import BootstrapInterval
    from magicite.eval.verdicts import holm_critical_slice_family

    holm = holm_critical_slice_family(
        [
            (
                s["name"],
                BootstrapInterval(s["point"], s["low"], s["high"], s["n_groups"], 10000, protocol["seed"]),
            )
            for s in slices
        ],
        min_groups=30,
        inferiority_p_values={s["name"]: s["p_value"] for s in slices},
    )
    numeric_holm = holm.to_dict()
    if any(s["support"]["status"] != "supported_assumptions" for s in slices):
        from magicite.eval.verdicts import Verdict

        holm = Verdict(
            "critical_slice_holm",
            "inconclusive",
            "complete supported critical family required",
            {"numeric_diagnostic": numeric_holm},
        )
    for s in slices:
        s["adjusted_p_value"] = numeric_holm["details"].get("adjusted_p_values", {}).get(s["name"])
        s["status"] = "inconclusive" if s["support"]["status"] != "supported_assumptions" else holm.status
    return {
        "schema": "magicite/grouped-calibration-evaluation/1",
        "classification": "nonqualifying_local_evaluation",
        "qualifying": False,
        "E2": "UNEVALUATED",
        "E3": "UNEVALUATED",
        "primary": primary,
        "critical_slices": slices,
        "holm": holm.to_dict(),
        "protocol_digest": sha256_json(protocol),
        "group_projection_digest": sha256_json(projection),
        "identities": identities,
        "raw_rows": {"candidate": candidate, "incumbent": incumbent},
    }


def plan_grouped_power(
    development: list[dict],
    *,
    group_floors: list[int],
    seed: int,
    repetitions: int,
    identities: dict,
    assumptions: dict,
    n_resamples: int = 10000,
    bound_input: dict | None = None,
) -> dict:
    """Conditional empirical-development model; simulation draws are not real groups."""
    import numpy as np

    if not development or any(r.get("family") != "development" for r in development):
        raise ValueError("development only; no calibration/final labels")
    if (
        not group_floors
        or any(type(g) is not int or g < 30 for g in group_floors)
        or group_floors != sorted(set(group_floors))
        or type(repetitions) is not int
        or repetitions < 1
        or type(seed) is not int
        or seed < 0
    ):
        raise ValueError("freeze increasing group grid and simulation controls")
    if any(
        not isinstance(r.get("query_id"), str)
        or not r["query_id"]
        or not isinstance(r.get("group_id"), str)
        or not r["group_id"]
        or any(type(r.get(k)) is not bool for k in ("candidate_hit", "incumbent_hit"))
        for r in development
    ) or len({r["query_id"] for r in development}) != len(development):
        raise ValueError("invalid or duplicate development observation")
    if (
        type(assumptions.get("independent_original_groups")) is not bool
        or type(assumptions.get("development_representative")) is not bool
    ):
        raise ValueError("typed power assumptions required")
    buckets: dict[str, list[dict]] = {}
    for r in development:
        buckets.setdefault(r["group_id"], []).append(r)
    if (
        len(buckets) < 30
        or not assumptions.get("independent_original_groups")
        or not assumptions.get("development_representative")
        or not assumptions.get("evidence_digests")
        or not identities
    ):
        raise ValueError("development grouping/model support missing")
    blocks = [buckets[g] for g in sorted(buckets)]
    effects = [sum(r["candidate_hit"] - r["incumbent_hit"] for r in block) / len(block) for block in blocks]
    if np.var(effects) == 0:
        raise ValueError("zero development variance cannot support power model")
    rng = np.random.default_rng(seed)
    z = NormalDist().inv_cdf(1 - 0.05 / len(group_floors))
    grid: list[dict[str, Any]] = []
    input_identity = None
    if bound_input is not None:
        validate_development_power_envelope(bound_input)
        if (
            set(bound_input) != {"identity", "input"}
            or sha256_json(bound_input["input"]) != bound_input["identity"]
            or bound_input["input"].get("schema") != "magicite/development-power-input/1"
            or bound_input["input"].get("development") != development
            or bound_input["input"].get("identities") != identities
        ):
            raise ValueError("development power envelope/observations binding mismatch")
        expected_assumptions = {
            "independent_original_groups": bound_input["input"]["protocol_frame"]["group_assumptions"][
                "independent_original_groups"
            ],
            "development_representative": bound_input["input"]["power_assumptions"][
                "development_representative"
            ],
            "evidence_digests": bound_input["input"]["power_assumptions"]["evidence_digests"],
        }
        if assumptions != expected_assumptions:
            raise ValueError("power assumptions differ from frozen input")
        input_identity = bound_input["identity"]
    diagnostic = repetitions < 200 or n_resamples != 10000 or input_identity is None
    for floor in group_floors:
        passed = 0
        seeds = []
        for _ in range(repetitions):
            simulation_seed = int(rng.integers(0, 2**32))
            bootstrap_seed = int(rng.integers(0, 2**32))
            draws = np.random.default_rng(simulation_seed).integers(0, len(blocks), size=floor)
            group_ids, c, i = [], [], []
            for j, draw in enumerate(draws):
                for row in blocks[int(draw)]:
                    group_ids.append(str(j))
                    c.append(float(row["candidate_hit"]))
                    i.append(float(row["incumbent_hit"]))
            passed += (
                query_weighted_bootstrap(group_ids, c, i, n_resamples=n_resamples, seed=bootstrap_seed)["low"]
                >= -0.02
            )
            seeds.append({"simulation": simulation_seed, "bootstrap": bootstrap_seed})
        lower, _ = wilson_interval(passed, repetitions, z=z)
        grid.append(
            {
                "group_floor": floor,
                "passed": passed,
                "repetitions": repetitions,
                "power_estimate": passed / repetitions,
                "simultaneous_lower": lower,
                "seeds": seeds,
            }
        )
    selected = (
        next((r["group_floor"] for r in grid if r["simultaneous_lower"] >= 0.8), None)
        if not diagnostic
        else None
    )
    return {
        "schema": "magicite/grouped-power-plan/1",
        "method": "development-empirical-group-bootstrap-power/1",
        "status": "supported_conditional_model" if selected else "inconclusive",
        "selected_group_floor": selected,
        "diagnostic": diagnostic,
        "n_resamples": n_resamples,
        "repetitions": repetitions,
        "seed": seed,
        "grid": grid,
        "n_original_development_groups": len(blocks),
        "development_digest": sha256_json(development),
        "bound_input_identity": input_identity,
        "identities": identities,
        "assumptions": assumptions,
        "group_effect_variance": float(np.var(effects)),
        "observed_effect": sum(r["candidate_hit"] - r["incumbent_hit"] for r in development)
        / len(development),
        "limitations": [
            "Simulation draws are conditional model draws, not additional authentic independent groups.",
            "Evidence declarations do not establish real-world power.",
        ],
    }


def validate_power_report(report: dict) -> None:
    required = {
        "schema",
        "method",
        "status",
        "selected_group_floor",
        "diagnostic",
        "n_resamples",
        "repetitions",
        "seed",
        "grid",
        "n_original_development_groups",
        "development_digest",
        "bound_input_identity",
        "identities",
        "assumptions",
        "group_effect_variance",
        "observed_effect",
        "limitations",
    }
    if (
        not isinstance(report, dict)
        or set(report) != required
        or report["schema"] != "magicite/grouped-power-plan/1"
        or report["method"] != "development-empirical-group-bootstrap-power/1"
    ):
        raise ValueError("complete generated power report required")
    import re

    if (
        not isinstance(report["development_digest"], str)
        or not re.fullmatch("[0-9a-f]{64}", report["development_digest"])
        or not isinstance(report["identities"], dict)
        or not report["identities"]
    ):
        raise ValueError("power development/source binding missing")
    if (
        type(report["diagnostic"]) is not bool
        or any(
            type(report[k]) is not int or report[k] < 0
            for k in ("n_resamples", "repetitions", "seed", "n_original_development_groups")
        )
        or report["n_original_development_groups"] < 30
    ):
        raise ValueError("power controls invalid")
    if (
        type(report["group_effect_variance"]) not in (int, float)
        or not math.isfinite(report["group_effect_variance"])
        or report["group_effect_variance"] <= 0
        or type(report["observed_effect"]) not in (int, float)
        or not -1 <= report["observed_effect"] <= 1
    ):
        raise ValueError("power development moments invalid")
    a = report["assumptions"]
    if (
        not isinstance(a, dict)
        or a.get("independent_original_groups") is not True
        or a.get("development_representative") is not True
        or not a.get("evidence_digests")
    ):
        raise ValueError("power empirical-model assumptions missing")
    grid = report["grid"]
    if not isinstance(grid, list) or not grid:
        raise ValueError("frozen power grid missing")
    floors = [r.get("group_floor") for r in grid]
    if any(type(f) is not int or f < 30 for f in floors) or floors != sorted(set(floors)):
        raise ValueError("invalid power grid")
    if report["repetitions"] < 1:
        raise ValueError("power repetitions missing")
    z = NormalDist().inv_cdf(1 - 0.05 / len(grid))
    for row in grid:
        if (
            set(row)
            != {"group_floor", "passed", "repetitions", "power_estimate", "simultaneous_lower", "seeds"}
            or type(row["passed"]) is not int
            or not 0 <= row["passed"] <= report["repetitions"]
            or row["repetitions"] != report["repetitions"]
            or len(row["seeds"]) != report["repetitions"]
        ):
            raise ValueError("incomplete power simulation grid")
        if any(
            not isinstance(s, dict)
            or set(s) != {"simulation", "bootstrap"}
            or any(type(v) is not int or not 0 <= v < 2**32 for v in s.values())
            for s in row["seeds"]
        ):
            raise ValueError("invalid simulation seeds")
        lower, _ = wilson_interval(row["passed"], report["repetitions"], z=z)
        if (
            row["power_estimate"] != row["passed"] / report["repetitions"]
            or row["simultaneous_lower"] != lower
        ):
            raise ValueError("power Monte Carlo summary mismatch")
    input_identity = report["bound_input_identity"]
    if input_identity is not None and (
        not isinstance(input_identity, str) or not re.fullmatch("[0-9a-f]{64}", input_identity)
    ):
        raise ValueError("power bound input identity invalid")
    diagnostic = report["n_resamples"] != 10000 or report["repetitions"] < 200 or input_identity is None
    selected = (
        next((r["group_floor"] for r in grid if r["simultaneous_lower"] >= 0.8), None)
        if not diagnostic
        else None
    )
    if (
        report["diagnostic"] != diagnostic
        or report["selected_group_floor"] != selected
        or report["status"] != ("supported_conditional_model" if selected else "inconclusive")
    ):
        raise ValueError("power support classification mismatch")


def validate_development_power_envelope(value: dict) -> None:
    """Pure consistency checks; the CLI additionally verifies live observed binding."""
    if (
        not isinstance(value, dict)
        or set(value) != {"identity", "input"}
        or not isinstance(value["input"], dict)
        or sha256_json(value["input"]) != value["identity"]
    ):
        raise ValueError("power input envelope integrity mismatch")
    body = value["input"]
    required = {
        "schema",
        "classification",
        "qualifying",
        "binding",
        "protocol_frame",
        "development_groups",
        "development_labels",
        "predictions",
        "development",
        "identities",
        "power_assumptions",
    }
    if (
        set(body) != required
        or body["schema"] != "magicite/development-power-input/1"
        or body["classification"] != "nonqualifying_local_evaluation"
        or body["qualifying"] is not False
    ):
        raise ValueError("complete nonqualifying power input contract required")
    protocol = validate_protocol(body["protocol_frame"])
    if protocol["power_plan"] is not None:
        raise ValueError("power protocol frame must precede report")
    groups = body["development_groups"]
    labels = body["development_labels"]
    if (
        not isinstance(groups, list)
        or not isinstance(labels, list)
        or not groups
        or any(r.get("family") != "development" for r in groups)
    ):
        raise ValueError("DEVELOPMENT-only projections required")
    indexed = {r["query_id"]: r for r in groups}
    gold = {r["query_id"]: r["relevance"] for r in labels}
    if len(indexed) != len(groups) or len(gold) != len(labels) or set(indexed) != set(gold):
        raise ValueError("power group/label projection membership mismatch")
    binding = body["binding"]
    identities = body["identities"]
    runtime = binding["runtime"]
    expected_identities = {
        "source_digest": sha256_json(binding["source_inputs"]),
        "model_digest": sha256_json(binding["model_manifest"]),
        "config_digest": runtime["comparison_config_digest"],
        "comparison_budget_digest": None,
        "candidate_policy_id": identities["candidate_policy_id"],
        "incumbent_policy_id": "dense-v1",
        "development_group_projection_digest": sha256_json(groups),
        "protocol_frame_digest": sha256_json(protocol),
    }
    if protocol["comparison_budget"] is not None:
        from magicite.core.comparison_budget import ComparisonBudget

        expected_identities["comparison_budget_digest"] = ComparisonBudget.from_dict(
            protocol["comparison_budget"]
        ).digest
    if identities != expected_identities or identities["candidate_policy_id"] == "dense-v1":
        raise ValueError("power source/model/config/budget/group/protocol identity mismatch")
    arms = body["predictions"]
    if not isinstance(arms, dict) or set(arms) != {"candidate", "incumbent"}:
        raise ValueError("complete actual power arms required")
    observations = {
        qid: {"query_id": qid, "group_id": indexed[qid]["group_id"], "family": "development"}
        for qid in indexed
    }
    for arm, rows in arms.items():
        mapped = {r["query_id"]: r for r in rows}
        if len(mapped) != len(rows) or set(mapped) != set(indexed):
            raise ValueError("power paired raw membership mismatch")
        for qid, row in mapped.items():
            expected = {
                "source_digest": identities["source_digest"],
                "model_manifest_digest": identities["model_digest"],
                "comparison_config_digest": identities["config_digest"],
                "comparison_budget_digest": identities["comparison_budget_digest"],
                "policy_id": identities[arm + "_policy_id"],
                "registry_digest": runtime["registry_digest"],
                "index_generation_id": runtime["generation"],
                "snapshot_id": runtime["snapshot"],
                "rank_depth": runtime["rank_depth"],
            }
            if any(k not in row or row[k] != val for k, val in expected.items()):
                raise ValueError("power raw prediction identity mismatch")
            ids, scores = row.get("raw_candidate_ids"), row.get("raw_scores")
            if (
                row.get("status") not in {"selected", "abstained"}
                or not isinstance(ids, list)
                or not isinstance(scores, list)
                or len(ids) != len(scores)
                or len(set(ids)) != len(ids)
                or any(not isinstance(cid, str) or not cid for cid in ids)
                or any(type(score) not in (int, float) or not math.isfinite(score) for score in scores)
            ):
                raise ValueError("unsupported power raw observations")
            observations[qid][arm + "_hit"] = bool(
                row["status"] == "selected" and ids and gold[qid].get(ids[0], 0) > 0
            )
    if body["development"] != [observations[qid] for qid in sorted(observations)]:
        raise ValueError("power hits/group identities differ from raw predictions and development labels")
