"""Evaluation package public surface (S01 → S14 ownership).

Downstream consumers (S07 policy selection, S10 promotion evidence, S13 CI
wiring, S16 release aggregation) should import schema IDs and validators
from here rather than reaching into private modules. S14 owns scale,
external corpora, and task-outcome evidence on top of the S01 foundation.
"""

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
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
    sha256_path,
)
from magicite.eval.manifests import (
    ArtifactRef,
    Claim,
    CorpusManifest,
    ExperimentManifest,
    Prediction,
    QueryRecord,
    ResultManifest,
)
from magicite.eval.unevaluated import unevaluated_catalog
from magicite.eval.verdicts import (
    Verdict,
    abstention_verdict,
    improvement_verdict,
    noninferiority_verdict,
    overall_promotion_verdict,
    reject_structural_efficacy_substitution,
    usefulness_verdict,
)

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
    "Verdict",
    "abstention_verdict",
    "canonical_json_bytes",
    "improvement_verdict",
    "noninferiority_verdict",
    "overall_promotion_verdict",
    "reject_structural_efficacy_substitution",
    "sha256_bytes",
    "sha256_json",
    "sha256_path",
    "unevaluated_catalog",
    "usefulness_verdict",
]
