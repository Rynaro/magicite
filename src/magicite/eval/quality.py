"""Official positive-only observation helpers; no ranking, fitting or promotion."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict

from magicite.eval.metrics import ndcg_at_k, paired_bootstrap_ci
from magicite.eval.production import runtime_queries

SMOKE_SEED = "magicite-official-quality-v1"
QUERY_TIMEOUT_SECONDS = 120
PHASE_BUDGET_SECONDS = 7200
RANK_DEPTH = 10


def smoke_sample(runtime: list[dict], scoring: list[dict], *, count: int = 64) -> dict:
    runtime_queries(runtime)
    queries = {row["query_id"]: row for row in runtime}
    if len(scoring) != len(queries) or {row["query_id"] for row in scoring} != set(queries):
        raise ValueError("sample runtime/scoring coverage mismatch")
    groups: dict[str, list[str]] = defaultdict(list)
    for row in scoring:
        original = row["upstream_query"]["original_id"]
        if not isinstance(original, str) or not original:
            raise ValueError("original_id required for prospective bookkeeping sample")
        if queries[row["query_id"]]["query_text"] != row["upstream_query"]["query"]:
            raise ValueError("runtime text differs from supplied query")
        groups[original].append(row["query_id"])

    def key(text: str) -> tuple[str, str]:
        return hashlib.sha256((SMOKE_SEED + "\0" + text).encode()).hexdigest(), text

    if len(groups) < count:
        raise ValueError("insufficient original_id groups for fixed smoke")
    chosen = sorted(groups, key=key)[:count]
    ids = [min(groups[group], key=key) for group in chosen]
    return {
        "seed": SMOKE_SEED,
        "query_ids": sorted(ids),
        "selected_original_id_groups": {group: sorted(groups[group]) for group in chosen},
        "scope": "Train-only plumbing sample; original_id is not proven independence",
    }


def validate_prediction(row: dict) -> None:
    if row.get("status") not in {"selected", "abstained", "error", "timeout"}:
        raise ValueError("unsupported outcome status")
    ids, scores, selected = row.get("candidate_ids"), row.get("scores"), row.get("selected_ids")
    if not isinstance(ids, list) or not isinstance(scores, list) or not isinstance(selected, list):
        raise ValueError("explicit candidate/score/selection arrays required")
    if len(ids) != len(set(ids)) or len(ids) > RANK_DEPTH or len(scores) != len(ids):
        raise ValueError("duplicate/oversized rank slate or score mismatch")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in scores
    ):
        raise ValueError("nonfinite ranking score")
    if len(selected) != len(set(selected)) or not set(selected).issubset(ids):
        raise ValueError("selection not in retained rank slate")
    if row["status"] != "selected" and selected:
        raise ValueError("abstention/error cannot acknowledge selection")
    if row.get("abstained") is not (row["status"] == "abstained"):
        raise ValueError("abstention status mismatch")
    if row.get("confidence") is not None:
        raise ValueError("no calibrated confidence available")


def per_query(row: dict, relevant: dict[str, float]) -> dict[str, float]:
    validate_prediction(row)
    if not relevant or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or not math.isfinite(value)
        for value in relevant.values()
    ):
        raise ValueError("complete positive graded qrels required")
    failed = row["status"] in {"error", "timeout"}
    ranked = [] if failed else row["candidate_ids"]
    selected = row["selected_ids"] if row["status"] == "selected" and not failed else []
    first_relevant = next(
        (index for index, identity in enumerate(ranked[:10], 1) if identity in relevant), None
    )
    excluded = {value["engram_id"] for value in row.get("exclusions", [])}
    return {
        "decision_hit_at_1": float(bool(selected and selected[0] in relevant)),
        "rank_hit_at_1": float(bool(ranked and ranked[0] in relevant)),
        "recall_at_5": len(set(ranked[:5]) & set(relevant)) / len(relevant),
        "recall_at_10": len(set(ranked[:10]) & set(relevant)) / len(relevant),
        "mrr_at_10": 1 / first_relevant if first_relevant else 0.0,
        "ndcg_at_10_linear_gain": ndcg_at_k(ranked, relevant, 10),
        "selected": float(bool(selected)),
        "selected_correct": float(bool(selected and selected[0] in relevant)),
        "error": float(failed),
        "abstention": float(row["status"] == "abstained"),
        "safety_exclusion_violations": float(len(set(row["candidate_ids"]) & excluded)),
        "selected_exclusion_violations": float(len(set(selected) & excluded)),
    }


def metrics(rows: list[dict], labels: list[dict], *, allow_incomplete: bool = False) -> dict:
    oracle = {row["query_id"]: row["runtime_relevance"] for row in labels}
    predicted = {row["query_id"]: row for row in rows}
    if (
        not oracle
        or len(oracle) != len(labels)
        or len(predicted) != len(rows)
        or not set(predicted).issubset(oracle)
    ):
        raise ValueError("duplicate/foreign label or prediction identity")
    missing = set(oracle) - set(predicted)
    if missing and not allow_incomplete:
        raise ValueError("missing prediction rows")
    values = {}
    for qid, relevant in oracle.items():
        row = predicted.get(
            qid,
            {
                "status": "error",
                "candidate_ids": [],
                "scores": [],
                "selected_ids": [],
                "abstained": False,
                "confidence": None,
            },
        )
        values[qid] = per_query(row, relevant)
    names = (
        "decision_hit_at_1",
        "rank_hit_at_1",
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10_linear_gain",
    )
    n = len(oracle)
    selected = sum(row["selected"] for row in values.values())
    return {
        "requested_queries": n,
        "observed_rows": len(rows),
        "missing_rows": len(missing),
        "complete": not missing,
        **{name: sum(row[name] for row in values.values()) / n for name in names},
        "selection_coverage": selected / n,
        "selective_accuracy": sum(row["selected_correct"] for row in values.values()) / selected
        if selected
        else None,
        "errors": sum(row["error"] for row in values.values()),
        "abstentions": sum(row["abstention"] for row in values.values()),
        "safety_exclusion_violations": sum(row["safety_exclusion_violations"] for row in values.values()),
        "per_query": values,
        "no_match_false_selection_rate": None,
        "calibrated_ece": None,
        "rank_scope": "Actually retained slate only; no inferred pre-abstention rankings",
        "qualification": "UNEVALUATED E3; positive-only labels and unproven independent groups",
    }


def paired_descriptive(candidate: dict, dense: dict) -> dict:
    ids = sorted(dense["per_query"])
    if set(ids) != set(candidate["per_query"]):
        raise ValueError("paired query coverage differs")
    intervals = {}
    for metric in (
        "decision_hit_at_1",
        "rank_hit_at_1",
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10_linear_gain",
    ):
        ci = paired_bootstrap_ci(
            ids,
            [candidate["per_query"][qid][metric] for qid in ids],
            [dense["per_query"][qid][metric] for qid in ids],
            n_resamples=10000,
            seed=0,
        ).to_dict()
        ci["n_query_rows"] = ci.pop("n_groups")
        intervals[metric] = ci
    return {
        "intervals": intervals,
        "resampling_unit": "query row",
        "independence": "UNPROVEN",
        "scope": "Descriptive conditional query-row resampling; not E3 group CI or promotion",
    }


def resume_rows(existing: list[dict], query_ids: list[str]) -> set[str]:
    if len({row["query_id"] for row in existing}) != len(existing):
        raise ValueError("duplicate completed outcome; no selective retry")
    if [row["query_id"] for row in existing] != query_ids[: len(existing)]:
        raise ValueError("outcomes do not match frozen query order")
    for row in existing:
        validate_prediction(row)
    return set(query_ids) - {row["query_id"] for row in existing}


def reconcile_checkpoint(state: dict, raw: bytes, binding: dict, interruptions: list[dict]) -> dict:
    """Recover one fully durable outcome; never replay it or reset spent budget."""
    import json

    from magicite.eval.digests import sha256_json

    if raw and not raw.endswith(b"\n"):
        raise ValueError("unsealed partial outcome bytes; retained for interruption reconciliation")
    rows = [json.loads(line) for line in raw.splitlines()]
    if state["binding"] != binding:
        raise ValueError("checkpoint source/input binding changed")
    measured = sum(row["query_elapsed_seconds"] for row in rows)
    if any(
        not isinstance(row["conservative_budget_charge_seconds"], (int, float))
        or not 0 <= row["conservative_budget_charge_seconds"] <= 120
        for row in interruptions
    ):
        raise ValueError("invalid interrupted-attempt budget estimate")
    estimated = sum(row["conservative_budget_charge_seconds"] for row in interruptions)
    if any(
        not math.isfinite(row["query_elapsed_seconds"]) or row["query_elapsed_seconds"] < 0 for row in rows
    ):
        raise ValueError("invalid measured query elapsed")
    if len(rows) == state["completed"] + 1 and state["pending"]:
        last = rows[-1]
        prefix = b"".join(raw.splitlines(keepends=True)[:-1])
        old_hash = hashlib.sha256(prefix).hexdigest() if prefix else None
        if (
            old_hash != state["row_file_sha256"]
            or last["query_id"] != state["pending"]["query_id"]
            or last["binding_sha256"] != sha256_json(binding)
        ):
            raise ValueError("durable row does not reconcile pending checkpoint")
        validate_prediction(last)
        state = {
            **state,
            "completed": len(rows),
            "pending": None,
            "row_file_sha256": hashlib.sha256(raw).hexdigest(),
            "spent_seconds": measured + estimated,
        }
    expected_hash = hashlib.sha256(raw).hexdigest() if raw else None
    if (
        state["completed"] != len(rows)
        or state["row_file_sha256"] != expected_hash
        or not math.isclose(state["spent_seconds"], measured + estimated, abs_tol=1e-6)
    ):
        raise ValueError("checkpoint rows or cumulative budget changed")
    return state
