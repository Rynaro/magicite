"""Canonical digests for evaluation evidence (evaluation.md E1).

Digest bytes are always SHA-256 over either raw file bytes or canonical
UTF-8 JSON (keys sorted, compact separators, ``ensure_ascii=True``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_EXPERIMENT = "magicite-experiment-manifest/1"
SCHEMA_CORPUS = "magicite-corpus-manifest/1"
SCHEMA_PREDICTION = "magicite-prediction/1"
SCHEMA_RESULT = "magicite-result-manifest/1"
SCHEMA_CLAIM = "magicite-claim/1"

EVIDENCE_CLASSES = frozenset({"historical", "synthetic", "structural", "retrieval", "host-task", "external"})
CLAIM_STATUSES = frozenset({"supported", "failed", "inconclusive", "superseded"})
LABEL_ORIGINS = frozenset({"author_created", "independent", "imported_official"})
SPLITS = frozenset({"development", "calibration", "final", "train", "test", "holdout"})


def canonical_json_bytes(value: Any) -> bytes:
    """UTF-8 JSON with lexically sorted keys and compact separators."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require_mapping(value: Any, locus: str) -> dict[str, Any] | str:
    if not isinstance(value, dict):
        return f"{locus} must be an object"
    return value
