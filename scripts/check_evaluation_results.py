#!/usr/bin/env python3
"""Validate superseding v0.3 evaluation evidence and reproduce composition metrics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from magicite.core import composition as composition_mod
from magicite.eval import metrics as metrics_mod
from magicite.eval.__main__ import validate_corpus_data

ROOT = Path(__file__).resolve().parents[1]


def _production_predictions(corpus: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], Any]:
    predictions: list[dict[str, Any]] = []
    mismatches: list[str] = []
    metric_pairs: list[tuple[list[str], list[str]]] = []
    for case in corpus["cases"]:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE edge (src_id TEXT, dst_name TEXT, dst_id TEXT, type TEXT, "
            "storage_strength REAL, provenance TEXT)"
        )
        nodes = {case["winner"]}
        for edge in case["edges"]:
            if edge["resolved"]:
                nodes.update((edge["src"], edge["dst"]))
        ids = {name: f"id:{name}" for name in nodes}
        for edge in case["edges"]:
            conn.execute(
                "INSERT INTO edge VALUES (?,?,?,?,?,?)",
                (
                    ids[edge["src"]],
                    edge["dst"],
                    ids.get(edge["dst"]) if edge["resolved"] else None,
                    edge["type"],
                    edge["strength"],
                    "declared",
                ),
            )
        plan = composition_mod.expand(conn, ids[case["winner"]], case["winner"])
        confidence = composition_mod.plan_confidence(plan)
        predictions.append(
            {
                "id": case["id"],
                "predicted_plan": plan.order,
                "plan_confidence": confidence,
                "cycle_broken": plan.cycle_broken,
            }
        )
        metric_pairs.append((plan.order, case["expected_plan"]))
        if plan.order != case["expected_plan"] or confidence != case["expected_confidence"]:
            mismatches.append(case["id"])
    return predictions, mismatches, metrics_mod.aggregate_plan_f1(metric_pairs)


def check(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read results: {exc}"]
    if result.get("schema") != "magicite-evaluation-results/1":
        errors.append("unexpected results schema")
    if result.get("status") != "superseding" or len(result.get("supersedes", [])) < 2:
        errors.append("results must explicitly supersede prior composition evidence")
    for item in result.get("supersedes", []):
        if "retained" not in item.get("disposition", ""):
            errors.append("superseded evidence must be retained, never rewritten")

    composition = result.get("composition", {})
    corpus_path = ROOT / composition.get("corpus", "")
    try:
        corpus_bytes = corpus_path.read_bytes()
        corpus = json.loads(corpus_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        return [*errors, f"cannot read referenced corpus: {exc}"]
    errors.extend(f"corpus: {error}" for error in validate_corpus_data(corpus))
    digest = hashlib.sha256(corpus_bytes).hexdigest()
    if composition.get("corpus_sha256") != digest:
        errors.append("corpus_sha256 does not match referenced bytes")

    predictions, mismatches, aggregate = _production_predictions(corpus)
    prediction_digest = hashlib.sha256(
        json.dumps(predictions, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if composition.get("prediction_digest_sha256") != prediction_digest:
        errors.append("prediction digest does not reproduce")
    cycle_count = sum("cycle" in case["features"] for case in corpus["cases"])
    dangling_count = sum("dangling" in case["features"] for case in corpus["cases"])
    expected_values = {
        "n_cases": len(corpus["cases"]),
        "n_cycle_cases": cycle_count,
        "n_dangling_cases": dangling_count,
        "plan_precision": aggregate.precision,
        "plan_recall": aggregate.recall,
        "plan_f1": aggregate.f1,
        "order_accuracy": 1.0 if aggregate.order_correct else 0.0,
        "confidence_accuracy": 1.0 if not mismatches else 0.0,
        "mismatched_case_ids": mismatches,
    }
    for key, expected in expected_values.items():
        if composition.get(key) != expected:
            errors.append(f"composition.{key} is {composition.get(key)!r}; reproduced {expected!r}")

    ranking = result.get("corrected_ranking_reference", {})
    if ranking.get("baseline_c_semantics") != "production seed selection and declared-inhibition parity":
        errors.append("baseline-c corrected semantics are missing")
    if ranking.get("hit_at_1") != {"a": 0.4048, "b": 0.5476, "c": 0.5286, "d": 0.5333}:
        errors.append("carried-forward Hit@1 reference does not match the preserved evidence")
    hypotheses = result.get("hypotheses", {})
    if hypotheses.get("H-BODY-b", {}).get("verdict") != "FALSIFIED_AS_IMPLEMENTED":
        errors.append("H-BODY-b negative result was softened or omitted")
    if hypotheses.get("H-COMPOSE", {}).get("verdict") != "SUPPORTED_STRUCTURAL_ONLY":
        errors.append("H-COMPOSE must remain explicitly scoped to structural expansion")
    if "remain untested" not in hypotheses.get("H-COMPOSE", {}).get("remaining", ""):
        errors.append("H-COMPOSE must retain its end-to-end limitation")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_evaluation_results.py RESULTS.json", file=sys.stderr)
        return 2
    errors = check(Path(argv[1]))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("evaluation results reproduce and supersede prior evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
