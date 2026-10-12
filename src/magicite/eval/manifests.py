"""Versioned evaluation manifests (evaluation.md E1).

Pure schema construction / parsing helpers. Validators live in
``magicite.eval.validate``. Downstream slices (S07, S10, S13, S14, S16)
consume these schema IDs and digest helpers; S14 publishes release-scale
results using the same shapes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str
    role: str
    byte_length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "sha256": self.sha256, "role": self.role}
        if self.byte_length is not None:
            out["byte_length"] = self.byte_length
        return out


@dataclass(frozen=True)
class ExperimentManifest:
    """``ExperimentManifest/1`` — immutable experiment pins."""

    experiment_id: str
    hypothesis: str
    reversal_condition: str
    source_commit: str
    dirty_tree_digest: str | None
    runner_lock_sha256: str
    dependency_lock_sha256: str
    corpus_sha256: str
    labels_sha256: str
    split_sha256: str
    primary_metric: str
    secondary_metrics: tuple[str, ...]
    statistical_method: str
    thresholds: dict[str, Any]
    seeds: dict[str, int]
    embedder_artifact_sha256: str | None
    policy_config_sha256: str | None
    label_provenance: dict[str, Any]
    dataset_license: str
    timestamp: str
    supersedes: tuple[str, ...] = ()
    sample_power_plan: dict[str, Any] = field(default_factory=dict)
    subgroup_definitions: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    warmup_cache: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA_EXPERIMENT

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["secondary_metrics"] = list(self.secondary_metrics)
        payload["supersedes"] = list(self.supersedes)
        return payload

    def digest(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class QueryRecord:
    query_id: str
    query_text: str
    split: str
    relevance: dict[str, float]
    group_id: str
    provenance: dict[str, Any]
    accepted_plans: tuple[tuple[str, ...], ...] = ()
    partial_order: tuple[tuple[str, str], ...] = ()
    negative_ids: tuple[str, ...] = ()
    ambiguous: bool = False
    compatibility_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_text": self.query_text,
            "split": self.split,
            "relevance": dict(self.relevance),
            "group_id": self.group_id,
            "provenance": dict(self.provenance),
            "accepted_plans": [list(plan) for plan in self.accepted_plans],
            "partial_order": [list(edge) for edge in self.partial_order],
            "negative_ids": list(self.negative_ids),
            "ambiguous": self.ambiguous,
            "compatibility_context": dict(self.compatibility_context),
        }


@dataclass(frozen=True)
class CorpusManifest:
    """``CorpusManifest/1`` — immutable corpus + label inventory."""

    corpus_id: str
    artifacts: tuple[ArtifactRef, ...]
    queries: tuple[QueryRecord, ...]
    label_origin: str
    dataset_revision: str
    license: str
    content_identity_sha256: str
    schema: str = SCHEMA_CORPUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "corpus_id": self.corpus_id,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "queries": [q.to_dict() for q in self.queries],
            "label_origin": self.label_origin,
            "dataset_revision": self.dataset_revision,
            "license": self.license,
            "content_identity_sha256": self.content_identity_sha256,
            "n_queries": len(self.queries),
        }

    def digest(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class Prediction:
    """``Prediction/1`` — one per-query prediction record."""

    query_id: str
    candidate_ids: tuple[str, ...]
    selection: str | None
    abstained: bool
    policy_id: str
    calibration_id: str | None = None
    compatibility_exclusions: tuple[str, ...] = ()
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    task_verifier_result: str | None = None
    schema: str = SCHEMA_PREDICTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "query_id": self.query_id,
            "candidate_ids": list(self.candidate_ids),
            "selection": self.selection,
            "abstained": self.abstained,
            "policy_id": self.policy_id,
            "calibration_id": self.calibration_id,
            "compatibility_exclusions": list(self.compatibility_exclusions),
            "stage_timings_ms": dict(self.stage_timings_ms),
            "task_verifier_result": self.task_verifier_result,
        }

    def digest(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class ResultManifest:
    """``ResultManifest/1`` — aggregates recomputable from predictions."""

    result_id: str
    experiment_sha256: str
    prediction_digests: tuple[str, ...]
    predictions_sha256: str
    aggregates: dict[str, Any]
    unevaluated: tuple[dict[str, Any], ...] = ()
    invalid_skipped: tuple[dict[str, Any], ...] = ()
    supersedes: tuple[str, ...] = ()
    schema: str = SCHEMA_RESULT

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "result_id": self.result_id,
            "experiment_sha256": self.experiment_sha256,
            "prediction_digests": list(self.prediction_digests),
            "predictions_sha256": self.predictions_sha256,
            "aggregates": dict(self.aggregates),
            "unevaluated": [dict(item) for item in self.unevaluated],
            "invalid_skipped": [dict(item) for item in self.invalid_skipped],
            "supersedes": list(self.supersedes),
        }

    def digest(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class Claim:
    """``Claim/1`` — a published metric claim bound to a result digest."""

    claim_id: str
    text_location: str
    metric: str
    value: float | int | None
    unit: str
    population_split: str
    result_digest: str
    confidence_interval: tuple[float, float] | None
    evidence_class: str
    status: str
    limitations: str
    superseding_claim: str | None = None
    schema: str = SCHEMA_CLAIM

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "claim_id": self.claim_id,
            "text_location": self.text_location,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "population_split": self.population_split,
            "result_digest": self.result_digest,
            "confidence_interval": (
                list(self.confidence_interval) if self.confidence_interval is not None else None
            ),
            "evidence_class": self.evidence_class,
            "status": self.status,
            "limitations": self.limitations,
            "superseding_claim": self.superseding_claim,
        }

    def digest(self) -> str:
        return sha256_json(self.to_dict())


def parse_experiment(data: dict[str, Any]) -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id=str(data["experiment_id"]),
        hypothesis=str(data["hypothesis"]),
        reversal_condition=str(data["reversal_condition"]),
        source_commit=str(data["source_commit"]),
        dirty_tree_digest=data.get("dirty_tree_digest"),
        runner_lock_sha256=str(data["runner_lock_sha256"]),
        dependency_lock_sha256=str(data["dependency_lock_sha256"]),
        corpus_sha256=str(data["corpus_sha256"]),
        labels_sha256=str(data["labels_sha256"]),
        split_sha256=str(data["split_sha256"]),
        primary_metric=str(data["primary_metric"]),
        secondary_metrics=tuple(data.get("secondary_metrics") or ()),
        statistical_method=str(data["statistical_method"]),
        thresholds=dict(data.get("thresholds") or {}),
        seeds={str(k): int(v) for k, v in dict(data.get("seeds") or {}).items()},
        embedder_artifact_sha256=data.get("embedder_artifact_sha256"),
        policy_config_sha256=data.get("policy_config_sha256"),
        label_provenance=dict(data.get("label_provenance") or {}),
        dataset_license=str(data["dataset_license"]),
        timestamp=str(data["timestamp"]),
        supersedes=tuple(data.get("supersedes") or ()),
        sample_power_plan=dict(data.get("sample_power_plan") or {}),
        subgroup_definitions=dict(data.get("subgroup_definitions") or {}),
        environment=dict(data.get("environment") or {}),
        warmup_cache=dict(data.get("warmup_cache") or {}),
        schema=str(data.get("schema", SCHEMA_EXPERIMENT)),
    )


def parse_corpus(data: dict[str, Any]) -> CorpusManifest:
    artifacts = tuple(
        ArtifactRef(
            path=str(item["path"]),
            sha256=str(item["sha256"]),
            role=str(item["role"]),
            byte_length=item.get("byte_length"),
        )
        for item in data.get("artifacts") or []
    )
    queries = tuple(
        QueryRecord(
            query_id=str(item["query_id"]),
            query_text=str(item["query_text"]),
            split=str(item["split"]),
            relevance={str(k): float(v) for k, v in dict(item.get("relevance") or {}).items()},
            group_id=str(item["group_id"]),
            provenance=dict(item.get("provenance") or {}),
            accepted_plans=tuple(
                tuple(str(name) for name in plan) for plan in item.get("accepted_plans") or []
            ),
            partial_order=tuple((str(edge[0]), str(edge[1])) for edge in item.get("partial_order") or []),
            negative_ids=tuple(str(x) for x in item.get("negative_ids") or []),
            ambiguous=bool(item.get("ambiguous", False)),
            compatibility_context=dict(item.get("compatibility_context") or {}),
        )
        for item in data.get("queries") or []
    )
    return CorpusManifest(
        corpus_id=str(data["corpus_id"]),
        artifacts=artifacts,
        queries=queries,
        label_origin=str(data["label_origin"]),
        dataset_revision=str(data["dataset_revision"]),
        license=str(data["license"]),
        content_identity_sha256=str(data["content_identity_sha256"]),
        schema=str(data.get("schema", SCHEMA_CORPUS)),
    )


def parse_prediction(data: dict[str, Any]) -> Prediction:
    return Prediction(
        query_id=str(data["query_id"]),
        candidate_ids=tuple(str(x) for x in data.get("candidate_ids") or []),
        selection=data.get("selection"),
        abstained=bool(data["abstained"]),
        policy_id=str(data["policy_id"]),
        calibration_id=data.get("calibration_id"),
        compatibility_exclusions=tuple(str(x) for x in data.get("compatibility_exclusions") or []),
        stage_timings_ms={str(k): float(v) for k, v in dict(data.get("stage_timings_ms") or {}).items()},
        task_verifier_result=data.get("task_verifier_result"),
        schema=str(data.get("schema", SCHEMA_PREDICTION)),
    )


def parse_result(data: dict[str, Any]) -> ResultManifest:
    return ResultManifest(
        result_id=str(data["result_id"]),
        experiment_sha256=str(data["experiment_sha256"]),
        prediction_digests=tuple(str(x) for x in data.get("prediction_digests") or []),
        predictions_sha256=str(data["predictions_sha256"]),
        aggregates=dict(data.get("aggregates") or {}),
        unevaluated=tuple(dict(x) for x in data.get("unevaluated") or []),
        invalid_skipped=tuple(dict(x) for x in data.get("invalid_skipped") or []),
        supersedes=tuple(str(x) for x in data.get("supersedes") or []),
        schema=str(data.get("schema", SCHEMA_RESULT)),
    )


def parse_claim(data: dict[str, Any]) -> Claim:
    ci = data.get("confidence_interval")
    interval: tuple[float, float] | None
    if ci is None:
        interval = None
    else:
        interval = (float(ci[0]), float(ci[1]))
    return Claim(
        claim_id=str(data["claim_id"]),
        text_location=str(data["text_location"]),
        metric=str(data["metric"]),
        value=data.get("value"),
        unit=str(data["unit"]),
        population_split=str(data["population_split"]),
        result_digest=str(data["result_digest"]),
        confidence_interval=interval,
        evidence_class=str(data["evidence_class"]),
        status=str(data["status"]),
        limitations=str(data["limitations"]),
        superseding_claim=data.get("superseding_claim"),
        schema=str(data.get("schema", SCHEMA_CLAIM)),
    )


# Re-export constants for callers that import from manifests.
__all__ = [
    "CLAIM_STATUSES",
    "EVIDENCE_CLASSES",
    "LABEL_ORIGINS",
    "SCHEMA_CLAIM",
    "SCHEMA_CORPUS",
    "SCHEMA_EXPERIMENT",
    "SCHEMA_PREDICTION",
    "SCHEMA_RESULT",
    "SPLITS",
    "ArtifactRef",
    "Claim",
    "CorpusManifest",
    "ExperimentManifest",
    "Prediction",
    "QueryRecord",
    "ResultManifest",
    "parse_claim",
    "parse_corpus",
    "parse_experiment",
    "parse_prediction",
    "parse_result",
]
