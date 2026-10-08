"""Actual-score offline calibration. Local integrity is not empirical qualification."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from magicite.core import calibration, router
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
    guard(expected, frozen, queries, actual, model_cache)
    body = {
        "schema": "magicite/frozen-calibration-consumer/1",
        "binding": expected,
        "trace_identity": identity,
        "artifact": artifact.to_dict(),
        "calibration_traces": traces,
        "empty_slates": sum(not t["raw_scores"] for t in traces),
        "qualifying": False,
        "confidence": None,
        "ECE": None,
        "limitations": "Observed maxima do not prove universal future abstention. No probability fit.",
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
    artifact = calibration.CalibrationArtifact.from_dict(body["artifact"])
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
                }
            )
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
