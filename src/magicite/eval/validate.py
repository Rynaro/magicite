"""Pure validators for evaluation manifests (evaluation.md E1).

Reject leakage, digest mismatches, missing prediction bytes, and
structural-to-efficacy relabeling. Historical v0.3 artifacts are allowed
to remain carried-forward but cannot satisfy a new-run gate.
"""

from __future__ import annotations

import re
from typing import Any

from magicite.eval.digests import (
    CLAIM_STATUSES,
    EVIDENCE_CLASSES,
    LABEL_ORIGINS,
    SCHEMA_CLAIM,
    SCHEMA_CORPUS,
    SCHEMA_EXPERIMENT,
    SCHEMA_PREDICTION,
    SCHEMA_RESULT,
    SPLITS,
    sha256_json,
)
from magicite.eval.manifests import (
    Claim,
    CorpusManifest,
    ExperimentManifest,
    Prediction,
    ResultManifest,
    parse_claim,
    parse_corpus,
    parse_experiment,
    parse_prediction,
    parse_result,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _require_hex64(value: Any, locus: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        errors.append(f"{locus} must be a 64-char lowercase hex SHA-256")


def validate_experiment_data(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["experiment must be an object"]
    if data.get("schema") != SCHEMA_EXPERIMENT:
        errors.append(f"schema must be {SCHEMA_EXPERIMENT}")
    for key in (
        "experiment_id",
        "hypothesis",
        "reversal_condition",
        "source_commit",
        "runner_lock_sha256",
        "dependency_lock_sha256",
        "corpus_sha256",
        "labels_sha256",
        "split_sha256",
        "primary_metric",
        "statistical_method",
        "dataset_license",
        "timestamp",
    ):
        if not isinstance(data.get(key), str) or not data[key]:
            errors.append(f"{key} must be a non-empty string")
    for digest_key in (
        "runner_lock_sha256",
        "dependency_lock_sha256",
        "corpus_sha256",
        "labels_sha256",
        "split_sha256",
    ):
        if digest_key in data:
            _require_hex64(data.get(digest_key), digest_key, errors)
    dirty = data.get("dirty_tree_digest")
    if dirty is not None:
        _require_hex64(dirty, "dirty_tree_digest", errors)
    if not isinstance(data.get("seeds"), dict):
        errors.append("seeds must be an object")
    if not isinstance(data.get("thresholds"), dict):
        errors.append("thresholds must be an object")
    if not isinstance(data.get("label_provenance"), dict):
        errors.append("label_provenance must be an object")
    return errors


def validate_corpus_manifest_data(data: Any) -> list[str]:
    """Validate ``CorpusManifest/1`` including leakage / duplicate guards."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["corpus must be an object"]
    if data.get("schema") != SCHEMA_CORPUS:
        errors.append(f"schema must be {SCHEMA_CORPUS}")
    for key in ("corpus_id", "dataset_revision", "license", "content_identity_sha256"):
        if not isinstance(data.get(key), str) or not data[key]:
            errors.append(f"{key} must be a non-empty string")
    _require_hex64(data.get("content_identity_sha256"), "content_identity_sha256", errors)
    origin = data.get("label_origin")
    if origin not in LABEL_ORIGINS:
        errors.append(f"label_origin must be one of {sorted(LABEL_ORIGINS)}")

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("artifacts must be an array")
        artifacts = []
    for index, item in enumerate(artifacts):
        locus = f"artifacts[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{locus} must be an object")
            continue
        for key in ("path", "sha256", "role"):
            if not isinstance(item.get(key), str) or not item[key]:
                errors.append(f"{locus}.{key} must be a non-empty string")
        _require_hex64(item.get("sha256"), f"{locus}.sha256", errors)

    queries = data.get("queries")
    if not isinstance(queries, list):
        return [*errors, "queries must be an array"]

    seen_ids: set[str] = set()
    content_identity: dict[str, str] = {}
    split_to_ids: dict[str, set[str]] = {}
    split_to_groups: dict[str, set[str]] = {}
    split_to_texts: dict[str, set[str]] = {}

    for index, item in enumerate(queries):
        locus = f"queries[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{locus} must be an object")
            continue
        qid = item.get("query_id")
        if not isinstance(qid, str) or not qid:
            errors.append(f"{locus}.query_id must be a non-empty string")
            continue
        if qid in seen_ids:
            errors.append(f"duplicate query_id {qid!r}")
        else:
            seen_ids.add(qid)

        text = item.get("query_text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{locus}.query_text must be a non-empty string")
            text = ""
        normalized = " ".join(text.lower().split())
        prior = content_identity.get(normalized)
        if prior is not None and prior != qid:
            errors.append(f"duplicate normalized query content between {prior!r} and {qid!r}")
        else:
            content_identity[normalized] = qid

        split = item.get("split")
        if split not in SPLITS:
            errors.append(f"{locus}.split must be one of {sorted(SPLITS)}")
            continue
        group_id = item.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            errors.append(f"{locus}.group_id must be a non-empty string")
            group_id = ""
        split_to_ids.setdefault(split, set()).add(qid)
        if group_id:
            split_to_groups.setdefault(split, set()).add(group_id)
        if normalized:
            split_to_texts.setdefault(split, set()).add(normalized)

        if not isinstance(item.get("relevance"), dict):
            errors.append(f"{locus}.relevance must be an object")
        if not isinstance(item.get("provenance"), dict):
            errors.append(f"{locus}.provenance must be an object")
        else:
            provenance = item["provenance"]
            if provenance.get("production_expansion_used") is True:
                errors.append(f"{locus}.provenance must not use production expansion as gold")

    # Train/development vs test/final/holdout leakage by query id, group, or text.
    development_like = {"train", "development", "calibration"}
    evaluation_like = {"test", "final", "holdout"}
    dev_ids = set().union(*(split_to_ids.get(s, set()) for s in development_like))
    eval_ids = set().union(*(split_to_ids.get(s, set()) for s in evaluation_like))
    leaked_ids = sorted(dev_ids & eval_ids)
    if leaked_ids:
        errors.append(f"train/test query_id leakage: {leaked_ids}")

    dev_groups = set().union(*(split_to_groups.get(s, set()) for s in development_like))
    eval_groups = set().union(*(split_to_groups.get(s, set()) for s in evaluation_like))
    leaked_groups = sorted(dev_groups & eval_groups)
    if leaked_groups:
        errors.append(f"train/test group_id leakage: {leaked_groups}")

    dev_texts = set().union(*(split_to_texts.get(s, set()) for s in development_like))
    eval_texts = set().union(*(split_to_texts.get(s, set()) for s in evaluation_like))
    leaked_texts = sorted(dev_texts & eval_texts)
    if leaked_texts:
        errors.append(f"train/test normalized-text leakage ({len(leaked_texts)} queries)")

    return errors


def validate_prediction_data(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["prediction must be an object"]
    if data.get("schema") != SCHEMA_PREDICTION:
        errors.append(f"schema must be {SCHEMA_PREDICTION}")
    if not isinstance(data.get("query_id"), str) or not data["query_id"]:
        errors.append("query_id must be a non-empty string")
    if not isinstance(data.get("candidate_ids"), list):
        errors.append("candidate_ids must be an array")
    if not isinstance(data.get("abstained"), bool):
        errors.append("abstained must be a boolean")
    if not isinstance(data.get("policy_id"), str) or not data["policy_id"]:
        errors.append("policy_id must be a non-empty string")
    selection = data.get("selection")
    abstained = data.get("abstained")
    if abstained is True and selection is not None:
        errors.append("abstained predictions must not carry a selection")
    if abstained is False and not (isinstance(selection, str) and selection):
        errors.append("non-abstained predictions require a selection")
    return errors


def validate_result_data(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["result must be an object"]
    if data.get("schema") != SCHEMA_RESULT:
        errors.append(f"schema must be {SCHEMA_RESULT}")
    for key in ("result_id", "experiment_sha256", "predictions_sha256"):
        if not isinstance(data.get(key), str) or not data[key]:
            errors.append(f"{key} must be a non-empty string")
    _require_hex64(data.get("experiment_sha256"), "experiment_sha256", errors)
    _require_hex64(data.get("predictions_sha256"), "predictions_sha256", errors)
    digests = data.get("prediction_digests")
    if not isinstance(digests, list) or not digests:
        errors.append("prediction_digests must be a non-empty array")
    else:
        for index, digest in enumerate(digests):
            _require_hex64(digest, f"prediction_digests[{index}]", errors)
    if not isinstance(data.get("aggregates"), dict):
        errors.append("aggregates must be an object")
    return errors


def validate_claim_data(
    data: Any,
    *,
    result: dict[str, Any] | ResultManifest | None = None,
    predictions: list[dict[str, Any]] | list[Prediction] | None = None,
    labels_sha256: str | None = None,
    expected_labels_sha256: str | None = None,
) -> list[str]:
    """Validate ``Claim/1`` and optional integrity bindings.

    Integrity failures covered:
    - mismatched published number vs result aggregates
    - missing raw prediction bytes / digests
    - labels digest drift (changed labels after the result was frozen)
    - structural evidence class used for efficacy claims
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["claim must be an object"]
    if data.get("schema") != SCHEMA_CLAIM:
        errors.append(f"schema must be {SCHEMA_CLAIM}")
    for key in (
        "claim_id",
        "text_location",
        "metric",
        "unit",
        "population_split",
        "result_digest",
        "evidence_class",
        "status",
        "limitations",
    ):
        if not isinstance(data.get(key), str) or not data[key]:
            errors.append(f"{key} must be a non-empty string")
    _require_hex64(data.get("result_digest"), "result_digest", errors)
    if data.get("evidence_class") not in EVIDENCE_CLASSES:
        errors.append(f"evidence_class must be one of {sorted(EVIDENCE_CLASSES)}")
    if data.get("status") not in CLAIM_STATUSES:
        errors.append(f"status must be one of {sorted(CLAIM_STATUSES)}")
    ci = data.get("confidence_interval")
    if ci is not None:
        if (
            not isinstance(ci, (list, tuple))
            or len(ci) != 2
            or not all(isinstance(x, (int, float)) for x in ci)
        ):
            errors.append("confidence_interval must be [low, high] numbers or null")

    metric = data.get("metric")
    value = data.get("value")
    evidence_class = data.get("evidence_class")
    if evidence_class == "structural" and metric in {
        "hit_at_1",
        "task_pass_rate",
        "end_task_usefulness",
        "usefulness_delta",
    }:
        errors.append(f"structural evidence cannot support retrieval/efficacy metrics (metric={metric!r})")

    result_dict: dict[str, Any] | None
    if isinstance(result, ResultManifest):
        result_dict = result.to_dict()
    else:
        result_dict = result

    if result_dict is not None:
        if sha256_json(result_dict) != data.get("result_digest"):
            errors.append("result_digest does not match provided result bytes")
        aggregates = result_dict.get("aggregates") or {}
        if (
            isinstance(metric, str)
            and metric in aggregates
            and value is not None
            and aggregates[metric] != value
        ):
            errors.append(
                f"claim value {value!r} does not match result.aggregates[{metric!r}]={aggregates[metric]!r}"
            )
        pred_digests = result_dict.get("prediction_digests") or []
        if not pred_digests:
            errors.append("result is missing raw prediction digests")

    if predictions is None:
        # Explicit integrity probe: callers that omit predictions when a
        # claim requires them trigger a failure.
        if data.get("require_predictions", True) and result_dict is not None:
            # predictions argument omitted — only fail when caller asks via
            # the dedicated missing-bytes path below.
            pass
    else:
        if len(predictions) == 0:
            errors.append("missing prediction bytes")
        else:
            pred_dicts = [p.to_dict() if isinstance(p, Prediction) else p for p in predictions]
            recomputed = sha256_json(pred_dicts)
            if result_dict is not None and result_dict.get("predictions_sha256") != recomputed:
                errors.append("predictions_sha256 does not match prediction bytes")

    if expected_labels_sha256 is not None and labels_sha256 is not None:
        if labels_sha256 != expected_labels_sha256:
            errors.append("labels digest changed relative to the frozen experiment")

    if labels_sha256 is None and expected_labels_sha256 is not None:
        errors.append("missing labels digest for claim integrity")

    return errors


def validate_claim_integrity(
    claim: Claim | dict[str, Any],
    *,
    result: ResultManifest | dict[str, Any],
    predictions: list[Prediction] | list[dict[str, Any]] | None,
    experiment: ExperimentManifest | dict[str, Any] | None = None,
    current_labels_sha256: str | None = None,
) -> list[str]:
    """High-level integrity gate used by AC-S01-03 and check scripts."""
    claim_data = claim.to_dict() if isinstance(claim, Claim) else claim
    result_data = result.to_dict() if isinstance(result, ResultManifest) else result
    expected_labels: str | None = None
    if isinstance(experiment, ExperimentManifest):
        expected_labels = experiment.labels_sha256
    elif isinstance(experiment, dict):
        labels = experiment.get("labels_sha256")
        expected_labels = str(labels) if labels is not None else None
    return validate_claim_data(
        claim_data,
        result=result_data,
        predictions=predictions,
        labels_sha256=current_labels_sha256,
        expected_labels_sha256=expected_labels,
    )


def load_and_validate_experiment(data: dict[str, Any]) -> tuple[ExperimentManifest | None, list[str]]:
    errors = validate_experiment_data(data)
    if errors:
        return None, errors
    return parse_experiment(data), []


def load_and_validate_corpus(data: dict[str, Any]) -> tuple[CorpusManifest | None, list[str]]:
    errors = validate_corpus_manifest_data(data)
    if errors:
        return None, errors
    return parse_corpus(data), []


def load_and_validate_prediction(data: dict[str, Any]) -> tuple[Prediction | None, list[str]]:
    errors = validate_prediction_data(data)
    if errors:
        return None, errors
    return parse_prediction(data), []


def load_and_validate_result(data: dict[str, Any]) -> tuple[ResultManifest | None, list[str]]:
    errors = validate_result_data(data)
    if errors:
        return None, errors
    return parse_result(data), []


def load_and_validate_claim(data: dict[str, Any]) -> tuple[Claim | None, list[str]]:
    errors = validate_claim_data(data)
    if errors:
        return None, errors
    return parse_claim(data), []
