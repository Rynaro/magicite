"""Actual-score offline calibration. Local integrity is not empirical qualification."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from magicite.core import calibration, router
from magicite.core.comparison_budget import comparison_config_digest
from magicite.eval import data_readiness as data
from magicite.eval.digests import sha256_bytes, sha256_json
from magicite.eval.production import (
    ActualRouter,
    descriptive_quality,
    freeze_model,
    runtime_queries,
    verify_model,
)

SEMANTICS = "ordered production raw scores; top1-minus-top2; singleton margin=top1; not probabilities"
TRACE_IDENTITY = (
    "policy_id",
    "policy_digest",
    "config_digest",
    "model_digest",
    "registry_digest",
    "index_generation_id",
    "snapshot_id",
    "schema_digest",
    "tokenizer_digest",
)


def source_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    return {str(p.relative_to(root)): sha256_bytes(p.read_bytes()) for p in sorted(root.rglob("*.py"))}


def registry_semantics(actual: ActualRouter) -> dict:
    tables = (
        "engram",
        "engram_step",
        "engram_trigger",
        "edge",
        "context_node",
        "engram_context",
        "engram_community",
        "eph_retrieval",
        "eph_embedding",
        "index_entry",
    )
    state: dict[str, Any] = {}
    for table in tables:
        rows = actual.conn.execute("SELECT * FROM " + table).fetchall()
        encoded = [[sha256_bytes(v) if isinstance(v, bytes) else v for v in row] for row in rows]
        state[table] = sha256_json(sorted(encoded, key=lambda row: json.dumps(row, sort_keys=True)))
    state["registry_files"] = {
        str(path.relative_to(actual.cfg.registry_dir)): sha256_bytes(path.read_bytes())
        for path in sorted(actual.cfg.registry_dir.rglob("*"))
        if path.is_file()
        and path.name
        not in {actual.cfg.db_path.name, actual.cfg.db_path.name + "-wal", actual.cfg.db_path.name + "-shm"}
    }
    return state


def runtime_identity(actual: ActualRouter) -> dict[str, Any]:
    from magicite.core.routing_policy import compute_config_digest

    generation, snapshot, schema, tokenizer, _ = router.pin_index_identity(actual.conn)
    active_policy = router._resolve_active_policy(actual.cfg)
    if active_policy[3]:
        raise ValueError("protected policy unavailable: " + active_policy[3])
    _, _, trust_snapshot = router._trust_decisions_by_engram(actual.cfg)
    cfg = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(actual.cfg).items()}
    return {
        "effective_policy": json.loads(json.dumps(active_policy)),
        "trust_snapshot": {
            "head": trust_snapshot.head,
            "policy": trust_snapshot.policy,
            "decisions": list(trust_snapshot.decisions),
            "records": list(trust_snapshot.records),
            "source_signers": [
                [list(key), sorted(signers)] for key, signers in sorted(trust_snapshot.source_signers.items())
            ],
        },
        "config": cfg,
        "config_digest": compute_config_digest(actual.cfg),
        "comparison_config_digest": comparison_config_digest(actual.cfg),
        "registry_digest": router._registry_digest(actual.conn),
        "registry_semantics": registry_semantics(actual),
        "generation": generation,
        "snapshot": snapshot,
        "schema": schema,
        "tokenizer": tokenizer,
        "model_cache": str(Path(actual.embedder._cache_dir or "").resolve()),
        "model_name": actual.embedder.model_name,
        "model_dim": actual.embedder.dim,
        "rank_depth": actual.rank_depth,
        "comparison_budget": actual.comparison_budget.to_dict()
        if actual.comparison_budget is not None
        else None,
        "score_semantics": SEMANTICS,
    }


def inputs(freeze: Path) -> tuple[dict, list[dict], dict[str, str]]:
    frozen = data.verify_freeze(freeze)
    frozen["consumer_freeze_path"] = str(freeze.resolve())
    root = Path(frozen["input_root"])
    packet = json.loads(data.contained(root, frozen["packet_name"]).read_bytes())
    queries = runtime_queries(data._read(root, packet["files"]["runtime"], {}))
    partitions = data._read(root, packet["files"]["partitions"], {})
    families = {r["query_id"]: data.FAMILIES[r["split"]] for r in partitions}
    return frozen, queries, families


def binding(frozen: dict, queries: list[dict], actual: ActualRouter, model: dict) -> dict:
    body = {
        "freeze_path": frozen["consumer_freeze_path"],
        "input_root": frozen["input_root"],
        "packet_name": frozen["packet_name"],
        "freeze_identity": frozen["identity"],
        "source_inputs": source_identity(),
        "runtime": runtime_identity(actual),
        "queries": queries,
        "model_manifest": model,
        "classification": "nonqualifying_local_evaluation",
        "E2": "UNEVALUATED",
        "E3": "UNEVALUATED",
    }

    return json.loads(json.dumps(body))


def guard(expected: dict, frozen: dict, queries: list[dict], actual: ActualRouter, cache: Path) -> None:
    if not isinstance(actual, ActualRouter):
        raise ValueError("actual production router required")
    current = data.verify_freeze(Path(expected["freeze_path"]))
    if any(current[key] != frozen[key] for key in ("identity", "input_root", "packet_name", "binding")):
        raise ValueError("frozen packet drift")
    if Path(actual.embedder._cache_dir or "").resolve() != cache.resolve():
        raise ValueError("actual provider model cache mismatch")
    verify_model(cache, expected["model_manifest"])
    if freeze_model(cache) != expected["model_manifest"]:
        raise ValueError("observed model libraries/revisions drift")
    if binding(frozen, queries, actual, expected["model_manifest"]) != expected:
        raise ValueError("candidate source/model/policy/config/registry/context identity drift")


def checked_trace(actual: ActualRouter, query: dict) -> dict:
    trace = actual.predict(query, raw_trace=True)
    if trace["query_id"] != query["query_id"]:
        raise ValueError("actual trace query identity mismatch")
    from magicite.core import fingerprint_key

    key = fingerprint_key.load_or_create_fingerprint_key(actual.cfg)
    if trace["query_fingerprint"] != fingerprint_key.query_fingerprint(query["query_text"], key=key):
        raise ValueError("actual trace query fingerprint mismatch")
    ids, scores = trace["raw_candidate_ids"], trace["raw_scores"]
    if len(ids) != len(scores) or len(set(ids)) != len(ids):
        raise ValueError("invalid ordered raw trace")
    if any(isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) for s in scores):
        raise ValueError("nonfinite raw score")
    from magicite.core.comparison_budget import comparison_config_digest

    trace["comparison_config_digest"] = comparison_config_digest(actual.cfg)
    trace["source_digest"] = sha256_json(source_identity())
    trace["query_context_digest"] = sha256_json(query)
    trace["custody_digest"] = sha256_json(runtime_identity(actual)["trust_snapshot"])
    trace["model_manifest_digest"] = sha256_json(freeze_model(Path(actual.embedder._cache_dir or "")))
    if not trace["query_fingerprint"]:
        raise ValueError("query fingerprint missing")
    return trace


def fit_commitment(frozen: dict, identity: str) -> Path:
    if not data.HEX.fullmatch(identity):
        raise ValueError("invalid fitted candidate identity")
    return Path(frozen["input_root"]) / ".calibration-consumer-fits" / (identity + ".json")


def final_owner(frozen: dict) -> Path:
    packet = json.loads(data.contained(Path(frozen["input_root"]), frozen["packet_name"]).read_bytes())
    return Path(frozen["input_root"]) / (
        ".calibration-consumer-final-" + packet["files"]["labels"]["sha256"] + ".json"
    )


def fit_calibration(
    freeze: Path, output: Path, actual: ActualRouter, *, model_cache: Path, model_manifest: dict
) -> dict:
    frozen, queries, families = inputs(freeze)
    if data.access_path(freeze).exists() or final_owner(frozen).exists():
        raise ValueError("final partition exposed; refit denied")
    projection = frozen["binding"].get("calibration_projection")
    if not projection or sha256_json(projection) != frozen["binding"].get("calibration_projection_sha256"):
        raise ValueError("preparation-materialized calibration projection required; prepare a new freeze")
    labels = {r["query_id"]: r for r in projection}
    expected_ids = {qid for qid, family in families.items() if family == "calibration"}
    if len(labels) != len(projection) or set(labels) != expected_ids:
        raise ValueError("calibration projection membership mismatch")
    statistical = frozen["binding"].get("statistical_projection")
    if statistical is not None:
        from magicite.eval.grouped_evaluation import validate_protocol

        validate_protocol(statistical["protocol"])
        if sha256_json(statistical) != frozen["binding"]["statistical_projection_sha256"]:
            raise ValueError("statistical projection digest mismatch")
        if statistical["protocol"]["comparison_budget"] != (
            actual.comparison_budget.to_dict() if actual.comparison_budget is not None else None
        ):
            raise ValueError("frozen comparison budget mismatch")
    elif actual.comparison_budget is not None:
        raise ValueError("budgeted fit requires frozen statistical protocol")
    expected = binding(frozen, queries, actual, model_manifest)
    guard(expected, frozen, queries, actual, model_cache)
    traces, examples = [], []
    identity = None
    for query in queries:
        if families[query["query_id"]] != "calibration":
            continue
        guard(expected, frozen, queries, actual, model_cache)
        trace = checked_trace(actual, query)
        guard(expected, frozen, queries, actual, model_cache)
        current = {k: trace[k] for k in TRACE_IDENTITY}
        if identity is not None and current != identity:
            raise ValueError("per-query runtime identity drift")
        identity = current
        traces.append(trace)
        if trace["status"] == "error":
            raise ValueError("operational error during calibration: " + str(trace["operational_error"]))
        scores = trace["raw_scores"]
        if scores:
            margin = calibration.score_margin(scores)
            assert margin is not None
            examples.append(
                calibration.CalibrationExample(
                    trace["query_fingerprint"],
                    scores[0],
                    margin,
                    any(g > 0 for g in labels[query["query_id"]]["relevance"].values()),
                )
            )
    if not examples or identity is None:
        raise ValueError("no fit-capable observations; empty eligible slates are not scalar examples")
    artifact = calibration.fit_abstention(
        examples,
        cfg=actual.cfg,
        policy_id=identity["policy_id"],
        policy_digest=identity["policy_digest"],
        config_digest=identity["config_digest"],
    )
    probability_reason: str | None = "statistical protocol absent"
    if statistical is not None:
        capable = [t for t in traces if t["raw_scores"] and t["status"] != "error"]
        model, probability_reason = calibration.fit_probability(
            [cast(float, calibration.score_margin(t["raw_scores"])) for t in capable],
            [labels[t["query_id"]]["relevance"].get(t["raw_candidate_ids"][0], 0) > 0 for t in capable],
            calibration_input_digest=sha256_json({"labels": projection, "traces": traces}),
            rank_depth=actual.rank_depth,
        )
        artifact = calibration.with_probability(
            artifact,
            model,
            evaluation_budget_digest=actual.comparison_budget.digest
            if actual.comparison_budget is not None
            else None,
        )
    guard(expected, frozen, queries, actual, model_cache)
    body = {
        "schema": "magicite/frozen-calibration-consumer/1",
        "binding": expected,
        "trace_identity": identity,
        "artifact": artifact.to_dict(),
        "statistical_projection": statistical,
        "probability_reason": probability_reason,
        "calibration_traces": traces,
        "empty_slates": sum(not t["raw_scores"] for t in traces),
        "qualifying": False,
        "confidence": None,
        "ECE": None,
        "limitations": (
            "Observed maxima do not prove universal future abstention; "
            "a fitted probability map is an unqualified calibration-only model."
        )
        if artifact.probability_model is not None
        else "Observed maxima do not prove universal future abstention. No supported probability fit.",
    }
    candidate: dict[str, Any] = {"identity": sha256_json(body), "candidate": body}
    data._publish_immutable(output, candidate)
    data._publish_immutable(fit_commitment(frozen, candidate["identity"]), candidate)
    return candidate


def evaluate_frozen_calibration(
    freeze: Path,
    candidate_path: Path,
    output: Path,
    actual: ActualRouter,
    *,
    model_cache: Path,
    replay: bool = False,
) -> dict:
    candidate_bytes = candidate_path.read_bytes()
    candidate = json.loads(candidate_bytes)
    body = candidate["candidate"]
    if (
        body.get("schema") != "magicite/frozen-calibration-consumer/1"
        or sha256_json(body) != candidate["identity"]
    ):
        raise ValueError("candidate digest/schema mismatch")
    from magicite.errors import InvalidInputError

    try:
        artifact = calibration.CalibrationArtifact.from_dict(body["artifact"])
    except InvalidInputError as exc:
        raise ValueError("fit artifact invalid or digest mismatch") from exc
    if calibration.compute_artifact_digest(artifact.to_dict()) != artifact.digest:
        raise ValueError("fit artifact digest mismatch")
    frozen, queries, families = inputs(freeze)
    expected = body["binding"]
    commitment = fit_commitment(frozen, candidate["identity"])
    if not commitment.exists() or json.loads(commitment.read_bytes()) != candidate:
        raise ValueError("candidate missing or changed from immutable fit commitment")
    guard(expected, frozen, queries, actual, model_cache)
    # The immutable run intent is published before the label access intent.
    run = {"candidate_identity": candidate["identity"], "binding": expected}
    run_path = output.with_name(output.name + ".run-intent.json")
    owner = final_owner(frozen)
    if replay:
        if json.loads(owner.read_bytes()) != run:
            raise ValueError("changed replay binding")
        if run_path.exists():
            if json.loads(run_path.read_bytes()) != run:
                raise ValueError("changed replay binding")
        else:
            data._publish_immutable(run_path, run)
    else:
        data._publish_immutable(run_path, run)
        data._publish_immutable(owner, run)
    rows: list[dict] = []
    report: dict = {
        "schema": "magicite/frozen-calibration-result/1",
        "run": run,
        "status": "incomplete",
        "access": "exposed_replay" if replay else "fresh_local_intent",
        "qualifying": False,
        "E2": "UNEVALUATED",
        "E3": "UNEVALUATED",
        "confidence": None,
        "ECE": None,
        "uncertainty": "No independent-case Wilson or grouped statistical qualification",
        "expected_queries": len(frozen["binding"]["final_ids"]),
        "rows": rows,
    }
    try:
        labels = data.open_final_labels(
            freeze, purpose=sha256_json(run), replay=replay and data.access_path(freeze).exists()
        )
        for query in queries:
            if families[query["query_id"]] != "final":
                continue
            guard(expected, frozen, queries, actual, model_cache)
            trace = checked_trace(actual, query)
            if (
                candidate_path.read_bytes() != candidate_bytes
                or json.loads(commitment.read_bytes()) != candidate
            ):
                raise ValueError("candidate bytes changed during final query")
            guard(expected, frozen, queries, actual, model_cache)
            if {k: trace[k] for k in TRACE_IDENTITY} != body["trace_identity"]:
                raise ValueError("per-query runtime identity drift")
            scores = trace["raw_scores"]
            decision = calibration.decide_abstention(
                query_fingerprint=trace["query_fingerprint"],
                top_score=scores[0] if scores else None,
                margin=calibration.score_margin(scores),
                artifact=artifact,
                rank_depth=actual.rank_depth,
                expected_policy_digest=trace["policy_digest"],
                expected_config_digest=trace["config_digest"],
            )
            # Only a production-selected route establishes composition/authority readiness.
            chosen = (
                trace["raw_candidate_ids"][:1]
                if not decision.abstain and trace["status"] == "selected"
                else []
            )
            rows.append(
                {
                    **trace,
                    "candidate_ids": trace["raw_candidate_ids"],
                    "selected_ids": chosen,
                    "actual_selected_ids": trace["selected_ids"],
                    "frozen_rule_abstained": decision.abstain,
                    "frozen_reason_codes": list(decision.reason_codes),
                    "usable_selection_confirmed": bool(chosen),
                    "proposed_top1_probability": calibration.probability_at_margin(
                        artifact.probability_model, cast(float, calibration.score_margin(scores))
                    )
                    if artifact.probability_model is not None
                    and scores
                    and trace["status"] != "error"
                    and artifact.probability_model["rank_depth"] == actual.rank_depth
                    else None,
                    "confidence": decision.confidence_value if chosen else None,
                }
            )
        if body.get("statistical_projection") is not None:
            from magicite.eval.grouped_evaluation import grouped_abstention, support
            from magicite.eval.metrics import fixed_bin_ece

            statistical = body["statistical_projection"]
            protocol = statistical["protocol"]
            report.update(
                schema="magicite/grouped-calibration-evaluation/1",
                classification="nonqualifying_local_evaluation",
                protocol_digest=sha256_json(protocol),
                group_projection_digest=sha256_json(statistical),
                fit_digest=artifact.digest,
                budget_digest=artifact.evaluation_budget_digest,
                probability_model_digest=sha256_json(artifact.probability_model)
                if artifact.probability_model
                else None,
            )
            gains = {r["query_id"]: r["relevance"] for r in labels}
            correct = [
                bool(r["raw_candidate_ids"] and gains[r["query_id"]].get(r["raw_candidate_ids"][0], 0) > 0)
                for r in rows
            ]
            report["ECE"] = fixed_bin_ece(
                [r["proposed_top1_probability"] for r in rows],
                correct,
                errors=[r["status"] == "error" for r in rows],
            )
            selected_rows = [
                (r, c) for r, c in zip(rows, correct, strict=True) if r["usable_selection_confirmed"]
            ]
            report["selected_ECE"] = fixed_bin_ece(
                [r["proposed_top1_probability"] for r, _ in selected_rows], [c for _, c in selected_rows]
            )
            group_map = {r["query_id"]: r["group_id"] for r in statistical["rows"]}
            bounds = {}
            for name, answerable, upper in (("coverage", True, False), ("false_selection", False, True)):
                subset = [r for r in rows if any(g > 0 for g in gains[r["query_id"]].values()) == answerable]
                bound = grouped_abstention(
                    [group_map[r["query_id"]] for r in subset],
                    [r["usable_selection_confirmed"] or (upper and r["status"] == "error") for r in subset],
                    seed=protocol["seed"],
                    upper=upper,
                    method=protocol["abstention_method"],
                )
                power_subject = {
                    "source_digest": sha256_json(expected["source_inputs"]),
                    "model_digest": sha256_json(expected["model_manifest"]),
                    "config_digest": comparison_config_digest(actual.cfg),
                    "comparison_budget_digest": artifact.evaluation_budget_digest,
                    "candidate_policy_id": body["trace_identity"]["policy_id"],
                    "incumbent_policy_id": "dense-v1",
                    "protocol_frame_digest": sha256_json({**protocol, "power_plan": None}),
                    "development_group_projection_digest": sha256_json(
                        [r for r in statistical["rows"] if r["family"] == "development"]
                    ),
                }
                bound["support"] = support(protocol, bound["n_groups"], identities=power_subject)
                bound["operational_errors"] = sum(r["status"] == "error" for r in subset)
                bound["error_accounting"] = (
                    "no-match errors penalized conservatively as potential selection; answerable errors miss"
                )
                bound["threshold"] = 0.05 if upper else 0.80
                bound["numeric_gate"] = (
                    "inconclusive"
                    if bound["bound"] is None
                    else "pass"
                    if (bound["bound"] <= 0.05 if upper else bound["bound"] >= 0.80)
                    else "fail"
                )
                bound["status"] = (
                    "inconclusive"
                    if bound["support"]["status"] != "supported_assumptions" or bound["operational_errors"]
                    else bound["numeric_gate"]
                )
                bounds[name] = bound
            report["abstention_bounds"] = bounds
        report["quality"] = descriptive_quality(rows, labels)
        gains = {label["query_id"]: label["relevance"] for label in labels}
        graded = []
        for row in rows:
            rel = gains[row["query_id"]]
            if any(g > 0 for g in rel.values()):
                dcg = sum(
                    (2 ** rel.get(cid, 0) - 1) / math.log2(i + 2)
                    for i, cid in enumerate(row["candidate_ids"])
                )
                ideal = sum(
                    (2**g - 1) / math.log2(i + 2)
                    for i, g in enumerate(sorted(rel.values(), reverse=True)[: actual.rank_depth])
                )
                graded.append(dcg / ideal if ideal else 0.0)
        report["quality"]["ndcg_at_rank_depth"] = {
            "numerator": sum(graded),
            "denominator": len(graded),
            "value": sum(graded) / len(graded) if graded else None,
        }
        report["label_kinds"] = {
            kind: sum(r["kind"] == kind for r in labels) for kind in ("positive", "ambiguous", "no_match")
        }
        report["errors"] = sum(r["status"] == "error" for r in rows)
        report["empty_rankings"] = sum(not r["candidate_ids"] for r in rows)
        report["status"] = "complete_with_errors" if report["errors"] else "complete"
    except BaseException as exc:
        report["failure"] = type(exc).__name__ + ": " + str(exc)
        report["completed_queries"] = len(rows)
        data._publish_immutable(output, report)
        raise
    report["completed_queries"] = len(rows)
    data._publish_immutable(output, report)
    return report


def cli_actual(project_root: Path, cache: Path, rank_depth: int) -> ActualRouter:
    from magicite.config import Config
    from magicite.eval.production import ProductionEmbedder
    from magicite.storage.db import connect

    cfg = Config.load(project_root)
    return ActualRouter(
        cfg, connect(cfg.db_path, migrate=False), ProductionEmbedder(cache), rank_depth=rank_depth
    )


def prepare_power_input(
    freeze: Path, actual: ActualRouter, predictions: dict, *, model_cache: Path, model_manifest: dict
) -> dict:
    """Bind saved DEVELOPMENT actual raw arms to live source/custody/model state.

    This validates local observation bindings; it never attests authentic data.
    Final labels are not decoded. No route, fit, activation or simulation runs.
    """
    from dataclasses import replace

    from magicite.core.comparison_budget import ComparisonBudget, comparison_config_digest
    from magicite.core.routing_policy import compute_config_digest, compute_policy_digest
    from magicite.eval.grouped_evaluation import validate_protocol

    frozen, queries, families = inputs(freeze)
    fits = Path(frozen["input_root"]) / ".calibration-consumer-fits"
    if (
        data.access_path(freeze).exists()
        or final_owner(frozen).exists()
        or (fits.exists() and any(fits.iterdir()))
    ):
        raise ValueError("power input must be archived before calibration/final exposure")
    projection = frozen["binding"].get("statistical_projection")
    labels = frozen["binding"].get("development_projection")
    if (
        projection is None
        or not labels
        or sha256_json(projection) != frozen["binding"]["statistical_projection_sha256"]
        or sha256_json(labels) != frozen["binding"]["development_projection_sha256"]
    ):
        raise ValueError("preparation-materialized development labels/groups required")
    power_assumptions = frozen["binding"].get("power_assumptions")
    if (
        not isinstance(power_assumptions, dict)
        or set(power_assumptions) != {"development_representative", "evidence_digests"}
        or type(power_assumptions["development_representative"]) is not bool
        or not isinstance(power_assumptions["evidence_digests"], list)
        or not power_assumptions["evidence_digests"]
        or any(
            not isinstance(d, str) or not data.HEX.fullmatch(d) for d in power_assumptions["evidence_digests"]
        )
    ):
        raise ValueError("preregistered development model assumptions required")
    protocol = validate_protocol(projection["protocol"])
    if protocol["power_plan"] is not None:
        raise ValueError("planning freeze must precede generated power report")
    budget_body = actual.comparison_budget.to_dict() if actual.comparison_budget else None
    if protocol["comparison_budget"] != budget_body:
        raise ValueError("power comparison budget differs from frozen protocol")
    if not isinstance(predictions, dict) or set(predictions) != {"candidate", "incumbent"}:
        raise ValueError("both saved actual prediction arms required")
    expected = binding(frozen, queries, actual, model_manifest)
    guard(expected, frozen, queries, actual, model_cache)
    dev = {row["query_id"]: row for row in projection["rows"] if row["family"] == "development"}
    gold = {row["query_id"]: row["relevance"] for row in labels}
    runtime = {row["query_id"]: row for row in queries if families[row["query_id"]] == "development"}
    if len(gold) != len(labels) or set(gold) != set(dev) or set(runtime) != set(dev):
        raise ValueError("development projection membership mismatch")
    from magicite.core import fingerprint_key

    key = fingerprint_key.load_or_create_fingerprint_key(actual.cfg)
    arms = {}
    policies = {}
    for arm, rows in predictions.items():
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("query_id"), str) for row in rows
        ):
            raise ValueError("invalid actual prediction rows")
        indexed = {row["query_id"]: row for row in rows}
        if len(indexed) != len(rows) or set(indexed) != set(dev):
            raise ValueError("complete unique paired DEVELOPMENT membership required")
        policy_ids = {row.get("policy_id") for row in rows}
        if len(policy_ids) != 1:
            raise ValueError("one actual policy per development arm required")
        policy = next(iter(policy_ids))
        if policy not in protocol["paired_policy_ids"] or (arm == "incumbent" and policy != "dense-v1"):
            raise ValueError("unfrozen development policy pair")
        policies[arm] = policy
        for qid, row in indexed.items():
            wanted = {
                "source_digest": sha256_json(expected["source_inputs"]),
                "model_manifest_digest": sha256_json(model_manifest),
                "config_digest": compute_config_digest(replace(actual.cfg, routing_policy=policy)),
                "comparison_config_digest": comparison_config_digest(actual.cfg),
                "registry_digest": expected["runtime"]["registry_digest"],
                "index_generation_id": expected["runtime"]["generation"],
                "snapshot_id": expected["runtime"]["snapshot"],
                "schema_digest": expected["runtime"]["schema"],
                "tokenizer_digest": expected["runtime"]["tokenizer"],
                "custody_digest": sha256_json(expected["runtime"]["trust_snapshot"]),
                "comparison_budget_digest": ComparisonBudget.from_dict(budget_body).digest
                if budget_body
                else None,
                "query_context_digest": sha256_json(runtime[qid]),
                "query_fingerprint": fingerprint_key.query_fingerprint(runtime[qid]["query_text"], key=key),
                "rank_depth": actual.rank_depth,
                "policy_digest": compute_policy_digest(policy, actual.cfg),
            }
            mismatched = [k for k, value in wanted.items() if k not in row or row[k] != value]
            if mismatched:
                raise ValueError("saved power trace binding mismatch: " + ", ".join(mismatched))
            ids, scores = row.get("raw_candidate_ids"), row.get("raw_scores")
            if (
                row.get("status") not in {"selected", "abstained", "error"}
                or not isinstance(ids, list)
                or not isinstance(scores, list)
                or len(ids) != len(scores)
                or len(set(ids)) != len(ids)
                or any(not isinstance(cid, str) or not cid for cid in ids)
                or any(type(score) not in (int, float) or not math.isfinite(score) for score in scores)
            ):
                raise ValueError("invalid saved actual raw prediction")
            if row["status"] == "error":
                raise ValueError("operational error cannot support development power model")
        arms[arm] = indexed
    if policies["candidate"] == policies["incumbent"]:
        raise ValueError("distinct actual power arms required")
    observations = []
    for qid in sorted(dev):
        hits = {}
        for arm in arms:
            row = arms[arm][qid]
            hits[arm] = bool(
                row["status"] == "selected"
                and row["raw_candidate_ids"]
                and gold[qid].get(row["raw_candidate_ids"][0], 0) > 0
            )
        observations.append(
            {
                "query_id": qid,
                "group_id": dev[qid]["group_id"],
                "family": "development",
                "candidate_hit": hits["candidate"],
                "incumbent_hit": hits["incumbent"],
            }
        )
    frame = {**protocol, "power_plan": None}
    identities = {
        "source_digest": sha256_json(expected["source_inputs"]),
        "model_digest": sha256_json(model_manifest),
        "config_digest": comparison_config_digest(actual.cfg),
        "comparison_budget_digest": actual.comparison_budget.digest if actual.comparison_budget else None,
        "candidate_policy_id": policies["candidate"],
        "incumbent_policy_id": policies["incumbent"],
        "development_group_projection_digest": sha256_json([dev[qid] for qid in sorted(dev)]),
        "protocol_frame_digest": sha256_json(frame),
    }
    body = {
        "schema": "magicite/development-power-input/1",
        "classification": "nonqualifying_local_evaluation",
        "qualifying": False,
        "binding": expected,
        "protocol_frame": frame,
        "development_groups": [dev[qid] for qid in sorted(dev)],
        "development_labels": labels,
        "predictions": predictions,
        "development": observations,
        "identities": identities,
        "power_assumptions": power_assumptions,
    }
    guard(expected, frozen, queries, actual, model_cache)
    return {"identity": sha256_json(body), "input": body}


def validate_power_input(
    value: dict, freeze: Path, actual: ActualRouter, *, model_cache: Path, model_manifest: dict
) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != {"identity", "input"}
        or not isinstance(value["input"], dict)
        or sha256_json(value["input"]) != value["identity"]
    ):
        raise ValueError("power input envelope integrity mismatch")
    observed = prepare_power_input(
        freeze, actual, value["input"]["predictions"], model_cache=model_cache, model_manifest=model_manifest
    )
    if observed != value:
        raise ValueError("power development projection/binding substitution or live source drift")
    return observed
