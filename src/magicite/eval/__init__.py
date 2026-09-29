"""Evaluation package public surface (S01 contract fingerprints).

Downstream consumers (S07 policy selection, S10 promotion evidence, S13 CI
wiring, S14 scale runs, S16 release aggregation) should import schema IDs
and validators from here rather than reaching into private modules.
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
    "canonical_json_bytes",
    "sha256_bytes",
    "sha256_json",
    "sha256_path",
]
