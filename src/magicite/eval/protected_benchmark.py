"""Protected live routing, immutable experimental integrity, no local qualification."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from magicite.core import calibration, policy_store
from magicite.core.comparison_budget import ComparisonBudget
from magicite.core.evaluation_context import EvaluationContext
from magicite.eval import calibration_consumer as consumer
from magicite.eval import data_readiness as data
from magicite.eval import grouped_evaluation as grouped
from magicite.eval.digests import sha256_json
from magicite.eval.metrics import fixed_bin_ece
from magicite.eval.production import ActualRouter, freeze_model, verify_model
from magicite.eval.profiles import build_profile_result_skeleton, get_profile

ARMS = ["dense-v1", "experimental/sparse-v1", "experimental/trigger-v1", "experimental/hybrid-rrf-v1"]
SIMPLE = ARMS[:3]
RESEARCH: dict[str, Any] = {
    "policy_id": "experimental/adaptive-blend-v1",
    "budget": None,
    "comparative": False,
    "mechanism": "existing-uncapped-adaptive-blend",
}
CLASSIFICATION = {
    "classification": "nonqualifying_local_evaluation",
    "qualifying": False,
    "E2": "UNEVALUATED",
    "E3": "UNEVALUATED",
    "E6": "UNEVALUATED",
    "GA": "UNEVALUATED",
}
PLAN_SCHEMA = "magicite/protected-benchmark-plan/1"


def make_plan(
    freeze: Path,
    actual: ActualRouter,
    model: dict,
    *,
    experiment_id: str,
    budget: ComparisonBudget,
    profile_id: str = "ci-smoke",
    seed: int = 7,
) -> dict:
    frozen, queries, _ = consumer.inputs(freeze)
    binding = consumer.binding(frozen, queries, actual, model)
    protocol = frozen["binding"]["statistical_projection"]["protocol"]
    return {
        "schema": PLAN_SCHEMA,
        "experiment_id": experiment_id,
        "packet_freeze_digest": frozen["identity"],
        "binding": binding,
        "profile_id": profile_id,
        "comparison_budget": budget.to_dict(),
        "rank_depth": actual.rank_depth,
        "arms": ARMS,
        "research_arm": RESEARCH,
        "candidate_id": ARMS[-1],
        "seed": seed,
        "selection_rule": "max-all-query-hit1;arm-order-tie",
        "incumbent_selection": {"safe_baselines": SIMPLE, "tie_break": "arm-order"},
        "statistical_protocol_digest": sha256_json(protocol),
        "measurement_mode": "local-single-process",
    }


def validate_plan(plan: dict, frozen: dict) -> ComparisonBudget:
    fields = {
        "schema",
        "experiment_id",
        "packet_freeze_digest",
        "binding",
        "profile_id",
        "comparison_budget",
        "rank_depth",
        "arms",
        "research_arm",
        "candidate_id",
        "seed",
        "selection_rule",
        "incumbent_selection",
        "statistical_protocol_digest",
        "measurement_mode",
    }
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError("strict preregistered benchmark plan required")
    if (
        plan["schema"] != PLAN_SCHEMA
        or not isinstance(plan["experiment_id"], str)
        or not plan["experiment_id"]
        or plan["arms"] != ARMS
        or plan["research_arm"] != RESEARCH
        or plan["candidate_id"] != ARMS[-1]
        or type(plan["seed"]) is not int
        or plan["seed"] < 0
        or type(plan["rank_depth"]) is not int
        or plan["selection_rule"] != "max-all-query-hit1;arm-order-tie"
        or plan["incumbent_selection"] != {"safe_baselines": SIMPLE, "tie_break": "arm-order"}
        or plan["measurement_mode"] != "local-single-process"
    ):
        raise ValueError("unsupported benchmark plan semantics")
    get_profile(plan["profile_id"])
    budget = ComparisonBudget.from_dict(plan["comparison_budget"])
    statistical = frozen["binding"]["statistical_projection"]
    if statistical is None:
        raise ValueError("frozen grouped protocol required")
    protocol = grouped.validate_protocol(statistical["protocol"])
    frame = {**protocol, "power_plan": None}
    planning = frozen["binding"].get("planning_successor", {}).get("identity", frozen["identity"])
    if (
        plan["packet_freeze_digest"] != planning
        or plan["statistical_protocol_digest"] != sha256_json(frame)
        or protocol["experiment_id"] != plan["experiment_id"]
        or protocol["seed"] != plan["seed"]
        or protocol["comparison_budget"] != budget.to_dict()
        or plan["rank_depth"] != budget.output_k
        or set(protocol["paired_policy_ids"]) != set(ARMS)
    ):
        raise ValueError("benchmark plan differs from frozen protocol/input/depth")
    sha256_json(plan)
    return budget


def _load(path: Path, schema: str) -> dict:
    value = json.loads(path.read_bytes())
    if (
        set(value) != {"identity", "body"}
        or value["body"].get("schema") != schema
        or sha256_json(value["body"]) != value["identity"]
    ):
        raise ValueError("immutable benchmark receipt integrity mismatch")
    return value


def _publish(path: Path, body: dict) -> dict:
    value = {"identity": sha256_json(body), "body": body}
    data._publish_immutable(path, value)
    return value


def _stage(frozen: dict, kind: str, identity: str) -> Path:
    return Path(frozen["input_root"]) / ".protected-benchmark" / kind / (identity + ".json")


def _registered(value: dict, frozen: dict, kind: str) -> None:
    path = _stage(frozen, kind, value["identity"])
    if not path.exists() or json.loads(path.read_bytes()) != value:
        raise ValueError("receipt missing or changed from packet-root immutable registration")


def _unspent(freeze: Path, frozen: dict) -> None:
    if data.access_path(freeze).exists() or consumer.final_owner(frozen).exists():
        raise ValueError("final partition already exposed; new preparation denied")


def _arm(
    actual: ActualRouter,
    policy: str,
    budget: ComparisonBudget | None,
    artifact: calibration.CalibrationArtifact | None = None,
) -> ActualRouter:
    return ActualRouter(
        actual.cfg,
        actual.conn,
        actual.embedder,
        rank_depth=actual.rank_depth,
        comparison_budget=budget,
        evaluation_context=EvaluationContext.bind(actual.cfg, policy, actual.rank_depth, budget, artifact),
    )


def _observations(
    actual: ActualRouter, queries: list[dict], frozen: dict, expected: dict, cache: Path
) -> list[dict]:
    rows = []
    for query in queries:
        consumer.guard(expected, frozen, expected["queries"], actual, cache)
        start = time.perf_counter_ns()
        try:
            trace = consumer.checked_trace(actual, query)
        except Exception as exc:
            # Final caller publishes failure/exposure; never manufacture an authenticated row.
            raise ValueError("actual route failed for " + query["query_id"] + ": " + str(exc)) from exc
        trace["duration_ms"] = (time.perf_counter_ns() - start) / 1_000_000
        trace["process_id"] = os.getpid()
        consumer.guard(expected, frozen, expected["queries"], actual, cache)
        rows.append(trace)
    return rows


def develop(
    freeze: Path,
    plan_path: Path,
    output: Path,
    actual: ActualRouter,
    *,
    model_cache: Path,
    model_manifest: dict,
    group_floors: list[int],
    repetitions: int,
) -> dict:
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    frozen, queries, families = consumer.inputs(freeze)
    _unspent(freeze, frozen)
    budget = validate_plan(plan, frozen)
    if actual.rank_depth != plan["rank_depth"] or actual.comparison_budget != budget:
        raise ValueError("actual development allowance mismatch")
    expected = plan["binding"]
    consumer.guard(expected, frozen, queries, actual, model_cache)
    dev = [q for q in queries if families[q["query_id"]] == "development"]
    labels = frozen["binding"]["development_projection"]
    if not labels or sha256_json(labels) != frozen["binding"]["development_projection_sha256"]:
        raise ValueError("materialized development projection required")
    if len(labels) != len(dev) or {r["query_id"] for r in labels} != {r["query_id"] for r in dev}:
        raise ValueError("complete development membership required")
    arms = {}
    for policy in ARMS:
        arm = _arm(actual, policy, budget)
        bound = consumer.binding(frozen, queries, arm, model_manifest)
        arms[policy] = _observations(arm, dev, frozen, bound, model_cache)
    research = _arm(actual, RESEARCH["policy_id"], None)
    research_rows = _observations(
        research, dev, frozen, consumer.binding(frozen, queries, research, model_manifest), model_cache
    )
    gold = {r["query_id"]: r["relevance"] for r in labels}
    hits = {
        p: sum(
            bool(
                r["status"] == "selected"
                and r["selected_ids"]
                and gold[r["query_id"]].get(r["selected_ids"][0], 0) > 0
            )
            for r in arms[p]
        )
        for p in SIMPLE
    }
    incumbent = max(SIMPLE, key=lambda p: hits[p])
    selection = {
        "schema": "magicite/development-incumbent-selection/1",
        "plan_digest": sha256_json(plan),
        "arm_order": SIMPLE,
        "labels": labels,
        "rows": {p: arms[p] for p in SIMPLE},
        "incumbent": incumbent,
    }
    selection["identity"] = sha256_json(selection)
    grouped.validate_incumbent_selection(selection)
    envelope = consumer.prepare_power_input(
        freeze,
        actual,
        {"candidate": arms[plan["candidate_id"]], "incumbent": arms[incumbent]},
        model_cache=model_cache,
        model_manifest=model_manifest,
        selection=selection,
    )
    grouped.validate_development_power_envelope(envelope)
    assumptions = {
        "independent_original_groups": envelope["input"]["protocol_frame"]["group_assumptions"][
            "independent_original_groups"
        ],
        **envelope["input"]["power_assumptions"],
    }
    power, power_error = None, None
    try:
        power = grouped.plan_grouped_power(
            envelope["input"]["development"],
            group_floors=group_floors,
            seed=plan["seed"],
            repetitions=repetitions,
            identities=envelope["input"]["identities"],
            assumptions=assumptions,
            bound_input=envelope,
        )
        grouped.validate_power_report(power)
    except ValueError as exc:
        power_error = str(exc)
    consumer.guard(expected, frozen, queries, actual, model_cache)
    if plan_path.read_bytes() != plan_bytes:
        raise ValueError("plan changed during development")
    body = {
        "schema": "magicite/protected-benchmark-development/1",
        "plan": plan,
        "selection": selection,
        "arms": arms,
        "research": {**RESEARCH, "rows": research_rows},
        "power_input": envelope,
        "power_report": power,
        "power_error": power_error,
        "status": "complete" if power else "incomplete_power",
        **CLASSIFICATION,
    }
    value = _publish(output, body)
    data._publish_immutable(_stage(frozen, "development", value["identity"]), value)
    return value


def fit(
    freeze: Path,
    development_path: Path,
    output: Path,
    actual: ActualRouter,
    *,
    model_cache: Path,
    model_manifest: dict,
) -> dict:
    frozen, queries, _ = consumer.inputs(freeze)
    _unspent(freeze, frozen)
    receipt = _load(development_path, "magicite/protected-benchmark-development/1")
    _registered(receipt, frozen, "development")
    body = receipt["body"]
    plan = body["plan"]
    budget = validate_plan(plan, frozen)
    consumer.guard(plan["binding"], frozen, queries, actual, model_cache)
    if body["power_report"] is None:
        raise ValueError("generated frozen development power report required: " + str(body["power_error"]))
    # Revalidate saved raw arms with current source/model/custody and exact frozen selection.
    fit_start = _stage(frozen, "fit-start", receipt["identity"])
    if fit_start.exists():
        if json.loads(fit_start.read_bytes()) != receipt:
            raise ValueError("changed resumed fit development receipt")
        grouped.validate_development_power_envelope(body["power_input"])
    else:
        consumer.validate_power_input(
            body["power_input"], freeze, actual, model_cache=model_cache, model_manifest=model_manifest
        )
        data._publish_immutable(fit_start, receipt)
    grouped.validate_power_report(body["power_report"])
    if body["power_report"]["bound_input_identity"] != body["power_input"]["identity"]:
        raise ValueError("power report differs from development input")
    successor_path = _stage(frozen, "successor", receipt["identity"])
    if successor_path.exists():
        successor = data.verify_freeze(successor_path)
        if successor["binding"]["planning_successor"]["selection"] != body["selection"]:
            raise ValueError("changed successor development selection")
    else:
        successor = data.link_power_freeze(freeze, successor_path, body["power_report"], body["selection"])
    fits = {}
    for policy in [*ARMS, RESEARCH["policy_id"]]:
        arm = _arm(actual, policy, budget if policy in ARMS else None)
        fit_path = _stage(frozen, "fit-" + policy.replace("/", "-"), receipt["identity"])
        # The research leg is intentionally uncapped: its protocol budget must not be silently changed.
        if policy not in ARMS:
            fits[policy] = {
                "artifact": None,
                "reason": "uncapped noncomparative research; capped calibration incompatible",
            }
            continue
        if fit_path.exists():
            candidate = json.loads(fit_path.read_bytes())
            if (
                json.loads(consumer.fit_commitment(successor, candidate["identity"]).read_bytes())
                != candidate
            ):
                raise ValueError("changed saved arm fit")
            consumer.guard(
                candidate["candidate"]["binding"], *consumer.inputs(successor_path)[:2], arm, model_cache
            )
        else:
            candidate = consumer.fit_calibration(
                successor_path, fit_path, arm, model_cache=model_cache, model_manifest=model_manifest
            )
        fits[policy] = candidate
    consumer.guard(plan["binding"], frozen, queries, actual, model_cache)
    commitment = {
        "schema": "magicite/protected-benchmark-commitment/1",
        "plan": plan,
        "development_identity": receipt["identity"],
        "development_path": str(development_path.resolve()),
        "selection": body["selection"],
        "power_report": body["power_report"],
        "planning_freeze": str(freeze.resolve()),
        "planning_identity": frozen["identity"],
        "successor_freeze": str(successor_path.resolve()),
        "successor_identity": successor["identity"],
        "fits": fits,
        **CLASSIFICATION,
    }
    value = _publish(output, commitment)
    data._publish_immutable(_stage(frozen, "commitment", value["identity"]), value)
    return value


def _summary(rows: list[dict], labels: list[dict], projection: dict) -> dict:
    gold = {r["query_id"]: r["relevance"] for r in labels}
    groups = {r["query_id"]: r["group_id"] for r in projection["rows"]}
    protocol = projection["protocol"]
    correct = [
        bool(
            r["status"] == "selected"
            and r["selected_ids"]
            and gold[r["query_id"]].get(r["selected_ids"][0], 0) > 0
        )
        for r in rows
    ]
    selected = [
        (r, c) for r, c in zip(rows, correct, strict=True) if r["status"] == "selected" and r["selected_ids"]
    ]
    bounds = {}
    for name, answerable, upper in (("coverage", True, False), ("false_selection", False, True)):
        subset = [r for r in rows if any(v > 0 for v in gold[r["query_id"]].values()) == answerable]
        bound = grouped.grouped_abstention(
            [groups[r["query_id"]] for r in subset],
            [
                bool(r["status"] == "selected" and r["selected_ids"])
                or (upper and r["status"] in {"error", "timeout"})
                for r in subset
            ],
            seed=protocol["seed"],
            upper=upper,
            method=protocol["abstention_method"],
        )
        bound["operational_errors"] = sum(r["status"] in {"error", "timeout"} for r in subset)
        bound["threshold"] = 0.05 if upper else 0.80
        bound["numeric_gate"] = (
            "inconclusive"
            if bound["bound"] is None
            else "pass"
            if (bound["bound"] <= 0.05 if upper else bound["bound"] >= 0.80)
            else "fail"
        )
        power = protocol["power_plan"]["report"] if protocol["power_plan"] else None
        subject = {
            **(power["identities"] if power else {}),
            "candidate_policy_id": rows[0]["policy_id"] if rows else None,
        }
        bound["support"] = grouped.support(protocol, bound["n_groups"], identities=subject)
        bound["status"] = (
            bound["numeric_gate"]
            if bound["support"]["status"] == "supported_assumptions" and not bound["operational_errors"]
            else "inconclusive"
        )
        bounds[name] = bound
    proposed = []
    proposed_correct = []
    for row in rows:
        proposed.append(row.get("proposed_top1_probability"))
        proposed_correct.append(
            bool(row["raw_candidate_ids"] and gold[row["query_id"]].get(row["raw_candidate_ids"][0], 0) > 0)
        )
    return {
        "all_query_hit1": {
            "numerator": sum(correct),
            "denominator": len(rows),
            "value": sum(correct) / len(rows) if rows else None,
        },
        "abstention_bounds": bounds,
        "selected_ECE": fixed_bin_ece([r["confidence"] for r, _ in selected], [c for _, c in selected]),
        "proposed_top1_ECE": fixed_bin_ece(
            proposed, proposed_correct, errors=[r["status"] in {"error", "timeout"} for r in rows]
        ),
        "group_count": len({groups[r["query_id"]] for r in rows}),
        "query_count": len(rows),
        "error_count": sum(r["status"] in {"error", "timeout"} for r in rows),
    }


def _profile(actual: ActualRouter, model: dict, profile_id: str, arms: dict) -> dict:
    import numpy as np

    from magicite.eval.envelopes import validate_envelope

    profile = get_profile(profile_id)
    shell = build_profile_result_skeleton(
        profile,
        provider="production",
        model_name=actual.embedder.model_name,
        model_digest=sha256_json(model),
        runner_label="local-single-process",
        project_root=Path(__file__).parents[3],
    )
    shell["process_id"] = os.getpid()
    timings = [r["duration_ms"] for rows in arms.values() for r in rows]
    shell["observed_route_ms"] = {
        "count": len(timings),
        "p50": float(np.percentile(timings, 50)) if timings else None,
        "p95": float(np.percentile(timings, 95)) if timings else None,
        "p99": float(np.percentile(timings, 99)) if timings else None,
        "population": "all arm calls including operational errors; cache state undeclared",
    }
    # These are observed call times, not warm-state or cold-process measurements.
    shell["measurements"] = {}
    shell["unevaluated"] = [
        "reference hardware",
        "cold process repetitions",
        "cache-state measurements",
        "index build",
        "RSS",
        "payload bytes/tokens",
    ]
    shell["envelope_check"] = validate_envelope(shell, profile, mode="completeness").to_dict()
    shell["status"] = "UNEVALUATED"
    return shell


def _publish_exact(path: Path, value: dict) -> None:
    try:
        data._publish_immutable(path, value)
    except FileExistsError:
        if json.loads(path.read_bytes()) != value:
            raise ValueError("changed immutable final publication") from None


def _complete_publication(frozen: dict, identity: str, result: dict) -> None:
    # Full durable staging precedes either publication; recovery never rescores.
    ready = {"commitment_digest": identity, "result_digest": sha256_json(result), "result": result}
    _publish_exact(_stage(frozen, "result-ready", identity), ready)
    _publish_exact(_stage(frozen, "result", identity), result)
    _publish_exact(
        _stage(frozen, "completion", identity),
        {"commitment_digest": identity, "result_digest": ready["result_digest"]},
    )


def final(
    commitment_path: Path, output: Path, actual: ActualRouter, *, model_cache: Path, replay: bool = False
) -> dict:
    commitment_bytes = commitment_path.read_bytes()
    value = _load(commitment_path, "magicite/protected-benchmark-commitment/1")
    body = value["body"]
    planning_path = Path(body["planning_freeze"])
    original, queries, families = consumer.inputs(planning_path)
    frozen, _, _ = consumer.inputs(Path(body["successor_freeze"]))
    _registered(value, original, "commitment")
    budget = validate_plan(body["plan"], frozen)
    development = _load(Path(body["development_path"]), "magicite/protected-benchmark-development/1")
    _registered(development, original, "development")
    if (
        development["identity"] != body["development_identity"]
        or development["body"]["selection"] != body["selection"]
        or development["body"]["power_report"] != body["power_report"]
        or frozen["identity"] != body["successor_identity"]
    ):
        raise ValueError("commitment development/successor changed")
    expected = body["plan"]["binding"]
    consumer.guard(expected, original, queries, actual, model_cache)
    arms = {}
    for policy in ARMS:
        fitted = body["fits"][policy]
        _body = fitted["candidate"]
        if (
            sha256_json(_body) != fitted["identity"]
            or json.loads(consumer.fit_commitment(frozen, fitted["identity"]).read_bytes()) != fitted
        ):
            raise ValueError("changed arm fit commitment")
        artifact: calibration.CalibrationArtifact | None = calibration.CalibrationArtifact.from_dict(
            _body["artifact"]
        )
        raw_arm = _arm(actual, policy, budget)
        consumer.guard(_body["binding"], frozen, queries, raw_arm, model_cache)
        arms[policy] = (_arm(actual, policy, budget, artifact), artifact)
    run = {
        "benchmark_commitment": value["identity"],
        "planning_identity": original["identity"],
        "successor_identity": frozen["identity"],
    }
    owner = consumer.final_owner(original)
    result_path = _stage(original, "result", value["identity"])
    completion_path = _stage(original, "completion", value["identity"])
    if replay:
        if json.loads(owner.read_bytes()) != run:
            raise ValueError("changed experiment replay owner")
        ready_path = _stage(original, "result-ready", value["identity"])
        if ready_path.exists() or result_path.exists():
            if not ready_path.exists():
                raise ValueError("completed result lacks durable exact-output staging")
            ready = json.loads(ready_path.read_bytes())
            stored = ready["result"]
            if (
                ready
                != {
                    "commitment_digest": value["identity"],
                    "result_digest": sha256_json(stored),
                    "result": stored,
                }
                or stored["commitment_digest"] != value["identity"]
                or stored["trace_digest"] != sha256_json(stored["arms"])
            ):
                raise ValueError("stored final result/trace digest changed")
            if result_path.exists() and json.loads(result_path.read_bytes()) != stored:
                raise ValueError("stored final result/trace digest changed")
            if completion_path.exists() and json.loads(completion_path.read_bytes()) != {
                "commitment_digest": value["identity"],
                "result_digest": sha256_json(stored),
            }:
                raise ValueError("stored final result/trace digest changed")
            successor_path = Path(body["successor_freeze"])
            data.validate_receipt(
                json.loads(data.access_path(successor_path).read_bytes()),
                data.receipt_binding(frozen, sha256_json(run)),
            )
            consumer.guard(expected, original, queries, actual, model_cache)
            _complete_publication(original, value["identity"], stored)
            replay_result = {
                **stored,
                "access": "exposed_replay",
                "original_result_digest": sha256_json(stored),
            }
            data._publish_immutable(output, replay_result)
            return replay_result
    else:
        data._publish_immutable(owner, run)
    intent = _stage(original, "final-intent", value["identity"])
    if intent.exists():
        if not replay or json.loads(intent.read_bytes()) != run:
            raise ValueError("changed final intent")
    else:
        data._publish_immutable(intent, run)
    rows: dict[str, list[dict]] = {p: [] for p in [*ARMS, RESEARCH["policy_id"]]}
    result = {
        "schema": "magicite/protected-benchmark-result/1",
        "commitment_digest": value["identity"],
        "status": "incomplete",
        "expected_query_ids": original["binding"]["final_ids"],
        "expected_queries_per_arm": len(original["binding"]["final_ids"]),
        "access": "exposed_replay" if replay else "fresh_local_intent",
        "arms": rows,
        "plan": body["plan"],
        "selection": body["selection"],
        "power_report": body["power_report"],
        "planning_identity": original["identity"],
        "successor_identity": frozen["identity"],
        **CLASSIFICATION,
    }
    try:
        labels = data.open_final_labels(
            Path(body["successor_freeze"]),
            purpose=sha256_json(run),
            replay=replay and data.access_path(Path(body["successor_freeze"])).exists(),
        )
        finals = [q for q in queries if families[q["query_id"]] == "final"]
        for policy in [*ARMS, RESEARCH["policy_id"]]:
            if policy in arms:
                arm, artifact = arms[policy]
            else:
                arm, artifact = _arm(actual, policy, None), None
            bound = consumer.binding(frozen, queries, arm, expected["model_manifest"])
            for query in finals:
                consumer.guard(expected, original, queries, actual, model_cache)
                if commitment_path.read_bytes() != commitment_bytes:
                    raise ValueError("commitment bytes changed during final")
                _registered(value, original, "commitment")
                if policy in ARMS:
                    fitted = body["fits"][policy]
                    if json.loads(consumer.fit_commitment(frozen, fitted["identity"]).read_bytes()) != fitted:
                        raise ValueError("arm fit registration changed during final")
                trace = _observations(arm, [query], frozen, bound, model_cache)[0]
                if artifact is not None and trace["calibration_digest"] != artifact.digest:
                    raise ValueError("actual finalizer calibration differs from frozen arm")
                if artifact is not None:
                    trace["proposed_top1_probability"] = (
                        calibration.probability_at_margin(
                            artifact.probability_model,
                            float(calibration.score_margin(trace["raw_scores"]) or 0.0),
                        )
                        if artifact.probability_model and trace["raw_scores"] and trace["status"] != "error"
                        else None
                    )
                else:
                    trace["proposed_top1_probability"] = None
                trace["usable_selection_confirmed"] = bool(
                    trace["status"] == "selected" and trace["selected_ids"]
                )
                rows[policy].append(trace)
        consumer.guard(expected, original, queries, actual, model_cache)
        if commitment_path.read_bytes() != commitment_bytes:
            raise ValueError("commitment changed before final publication")
        _registered(value, original, "commitment")
        for policy in ARMS:
            fitted = body["fits"][policy]
            if json.loads(consumer.fit_commitment(frozen, fitted["identity"]).read_bytes()) != fitted:
                raise ValueError("arm fit changed before final publication")
        projection = frozen["binding"]["statistical_projection"]
        result["summaries"] = {p: _summary(r, labels, projection) for p, r in rows.items()}
        gains = {r["query_id"]: r["relevance"] for r in labels}
        candidate, incumbent = (
            body["plan"]["candidate_id"],
            grouped.validate_incumbent_selection(body["selection"]),
        )
        paired = {
            p: [
                {
                    **r,
                    "hit": bool(
                        r["status"] == "selected"
                        and r["selected_ids"]
                        and gains[r["query_id"]].get(r["selected_ids"][0], 0) > 0
                    ),
                }
                for r in rows[p]
            ]
            for p in (candidate, incumbent)
        }
        result["paired"] = (
            None
            if candidate == incumbent
            else grouped.paired_report(
                projection,
                paired[candidate],
                paired[incumbent],
                identities={"source_digest": sha256_json(expected["source_inputs"])},
                selection=body["selection"],
            )
        )
        result["dense_reference"] = "dense-v1"
        result["research"] = {
            **RESEARCH,
            "limitation": (
                "uncapped mechanism excluded from common-budget paired inference; E2/E5 promotion UNEVALUATED"
            ),
        }
        result["trace_digest"] = sha256_json(rows)
        result["profile"] = _profile(actual, expected["model_manifest"], body["plan"]["profile_id"], rows)
        source_root = Path(__file__).parents[3]
        result["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
        ).strip()
        result["source_digest"] = sha256_json(expected["source_inputs"])
        result["model_manifest"] = expected["model_manifest"]
        result["status"] = (
            "complete_with_errors"
            if any(r["status"] == "error" for rr in rows.values() for r in rr)
            else "complete"
        )
        _complete_publication(original, value["identity"], result)
        data._publish_immutable(output, result)
        return result
    except BaseException as exc:
        result["error"] = str(exc)
        result["completed_queries_by_arm"] = {policy: len(observed) for policy, observed in rows.items()}
        result["exposure_retained"] = True
        failure = output.with_name(output.name + ".failure.json")
        if not failure.exists():
            data._publish_immutable(failure, result)
        raise


def production_witness(
    actual: ActualRouter,
    queries: list[dict],
    output: Path,
    *,
    model_cache: Path,
    model_manifest: dict,
    profile_id: str,
) -> dict:
    if actual.evaluation_context is not None or actual.comparison_budget is not None:
        raise ValueError("ordinary protected production mode required")
    if Path(actual.embedder._cache_dir or "").resolve() != model_cache.resolve():
        raise ValueError("production witness actual model cache mismatch")
    verify_model(model_cache, model_manifest)
    if freeze_model(model_cache) != model_manifest:
        raise ValueError("production witness model libraries changed")
    approved = policy_store.active_calibration(actual.cfg, actual.conn, actual.embedder)
    if approved is None or approved.probability_model is None:
        raise ValueError("compatible approved active v2 probability calibration required")
    if not queries:
        raise ValueError("production witness queries required")
    before = consumer.runtime_identity(actual)
    rows = []
    for query in queries:
        if (
            consumer.runtime_identity(actual) != before
            or policy_store.active_calibration(actual.cfg, actual.conn, actual.embedder) != approved
        ):
            raise ValueError("production approved authority changed")
        start = time.perf_counter_ns()
        trace = consumer.checked_trace(actual, query)
        trace["duration_ms"] = (time.perf_counter_ns() - start) / 1_000_000
        trace["process_id"] = os.getpid()
        if (
            trace["calibration_digest"] != approved.digest
            or trace["evaluation_only"]
            or consumer.runtime_identity(actual) != before
        ):
            raise ValueError("ordinary production finalizer/authority mismatch")
        verify_model(model_cache, model_manifest)
        if freeze_model(model_cache) != model_manifest:
            raise ValueError("production witness model libraries changed")
        rows.append(trace)
    result = {
        "schema": "magicite/protected-benchmark-production-witness/1",
        "approved_artifact_digest": approved.digest,
        "runtime": before,
        "rows": rows,
        "trace_digest": sha256_json(rows),
        "profile": _profile(actual, model_manifest, profile_id, {"production": rows}),
        **CLASSIFICATION,
    }
    data._publish_immutable(output, result)
    return result


def quality_reference(result: dict, matrix: dict, *, observed_model: dict) -> dict:
    """Attach compatible LOCAL quality bytes; never change matrix eligibility."""
    if (
        result.get("schema") != "magicite/protected-benchmark-result/1"
        or result.get("classification") != CLASSIFICATION["classification"]
        or result.get("qualifying") is not False
        or result.get("status") not in {"complete", "complete_with_errors"}
        or result.get("trace_digest") != sha256_json(result.get("arms"))
        or any(result.get(k) != "UNEVALUATED" for k in ("E2", "E3", "E6", "GA"))
    ):
        raise ValueError("complete nonqualifying benchmark reference required")
    frozen = data.verify_freeze(Path(result["plan"]["binding"]["freeze_path"]))
    commitment = _load(
        _stage(frozen, "commitment", result["commitment_digest"]), "magicite/protected-benchmark-commitment/1"
    )
    _registered(commitment, frozen, "commitment")
    if commitment["identity"] != result["commitment_digest"] or commitment["body"]["plan"] != result["plan"]:
        raise ValueError("quality reference commitment/plan mismatch")
    saved = json.loads(_stage(frozen, "result", commitment["identity"]).read_bytes())
    completion = json.loads(_stage(frozen, "completion", commitment["identity"]).read_bytes())
    if completion != {"commitment_digest": commitment["identity"], "result_digest": sha256_json(saved)}:
        raise ValueError("quality reference completed result changed")
    expected_result = saved
    if "original_result_digest" in result:
        expected_result = {**saved, "access": "exposed_replay", "original_result_digest": sha256_json(saved)}
    if result != expected_result:
        raise ValueError("quality reference differs from immutable completed result")
    if set(result["arms"]) != {*ARMS, RESEARCH["policy_id"]}:
        raise ValueError("complete required quality arms missing")
    final_ids = set(frozen["binding"]["final_ids"])
    for policy, rows in result["arms"].items():
        if (
            len(rows) != len(final_ids)
            or {row["query_id"] for row in rows} != final_ids
            or any(row["policy_id"] != policy for row in rows)
        ):
            raise ValueError("complete unique final quality membership required")
    current_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parents[3], text=True
    ).strip()
    if result["source_commit"] != current_commit or result["source_digest"] != sha256_json(
        consumer.source_identity()
    ):
        raise ValueError("quality reference source differs from matrix source")
    if result["model_manifest"] != observed_model:
        raise ValueError("quality reference model bytes/libraries differ from matrix runtime")
    source_profile = result["profile"]
    if source_profile["profile"]["profile_id"] != matrix["profile"]["profile_id"]:
        raise ValueError("quality reference profile differs from matrix profile")
    fields = (
        "python",
        "platform",
        "machine",
        "processor",
        "os_release",
        "provider",
        "model_name",
        "dependency_lock_sha256",
        "runtime_is_container",
        "container_image_digest",
    )
    if any(source_profile["fingerprint"][field] != matrix["fingerprint"][field] for field in fields):
        raise ValueError("quality reference environment/dependencies differ from matrix runtime")
    return {
        "schema": "magicite/benchmark-quality-reference/1",
        "sha256": sha256_json(result),
        "commitment_digest": result["commitment_digest"],
        "trace_digest": result["trace_digest"],
        **CLASSIFICATION,
    }
