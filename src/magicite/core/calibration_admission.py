"""Typed operator attestation for calibration, protected by existing policy custody.

Schema validation checks bindings, not statistical truth. Current evaluation
consumer reports cannot satisfy this future qualification contract.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import routing_policy
from magicite.core.calibration import CalibrationArtifact
from magicite.errors import InvalidInputError

SCHEMA = "magicite/calibration-qualification/1"
REPORT_SCHEMA = "magicite/qualified-calibration-report/1"
SUBJECT_FIELDS = frozenset(
    {
        "artifact_digest",
        "base_policy_id",
        "base_policy_digest",
        "config_digest",
        "model_digest",
        "registry_digest",
        "generation",
        "snapshot",
        "schema",
        "tokenizer",
        "custody_digest",
        "score_semantics",
        "mechanism_digest",
    }
)
SOURCE_FIELDS = frozenset({"source_commit", "candidate_digest"})


def canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("calibration bundle must be finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise InvalidInputError("duplicate calibration JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise InvalidInputError("calibration JSON unreadable or malformed") from exc
    if not isinstance(value, dict):
        raise InvalidInputError("calibration JSON must be an object")
    canonical_digest(value)
    return value


def _exact(value: Any, fields: frozenset[str] | set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise InvalidInputError(f"invalid {label} fields")
    return value


def _strings(value: dict[str, Any], label: str) -> None:
    if any(not isinstance(v, str) or not v for v in value.values()):
        raise InvalidInputError(f"{label} identities must be nonempty strings")


def validate(artifact_body: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    artifact = CalibrationArtifact.from_dict(artifact_body)
    if artifact.evaluation_budget_digest is not None:
        raise InvalidInputError("evaluation-budget calibration cannot authorize ordinary runtime")
    body = _exact(
        evidence,
        {"schema", "status", "qualifying", "classification", "gates", "subject", "source", "fit", "final"},
        "qualification",
    )
    if (
        body["schema"] != SCHEMA
        or body["status"] != "PASS"
        or body["qualifying"] is not True
        or body["classification"] != "qualified_calibration"
        or body["gates"] != {"E2": "PASS", "E3": "PASS"}
    ):
        raise InvalidInputError("calibration requires complete qualifying E2/E3 PASS attestation")
    subject = _exact(body["subject"], SUBJECT_FIELDS, "subject")
    source = _exact(body["source"], SOURCE_FIELDS, "source")
    _strings(subject, "subject")
    _strings(source, "source")
    if (
        subject["artifact_digest"] != artifact.digest
        or subject["base_policy_id"] != artifact.policy_id
        or subject["base_policy_digest"] != artifact.policy_digest
        or subject["config_digest"] != artifact.config_digest
    ):
        raise InvalidInputError("calibration artifact/qualification binding mismatch")
    anchors = []
    for phase in ("fit", "final"):
        anchor = _exact(body[phase], {"report", "sha256"}, phase)
        report = _exact(
            anchor["report"],
            {
                "schema",
                "phase",
                "status",
                "qualifying",
                "classification",
                "gates",
                "subject",
                "source",
                "input_digest",
                "fit_digest",
            },
            phase + " report",
        )
        if (
            anchor["sha256"] != canonical_digest(report)
            or report["schema"] != REPORT_SCHEMA
            or report["phase"] != phase
            or report["status"] != "PASS"
            or report["qualifying"] is not True
            or report["classification"] != "qualified_calibration"
            or report["gates"] != body["gates"]
            or report["subject"] != subject
            or report["source"] != source
            or not isinstance(report["input_digest"], str)
            or not report["input_digest"]
        ):
            raise InvalidInputError("nonqualifying or inconsistent underlying calibration report")
        if phase == "fit" and report["fit_digest"] != source["candidate_digest"]:
            raise InvalidInputError("fit candidate commitment mismatch")
        if phase == "final" and report["fit_digest"] != body["fit"]["sha256"]:
            raise InvalidInputError("final does not bind the immutable fit report")
        anchors.append(report["input_digest"])
    if anchors[0] == anchors[1]:
        raise InvalidInputError("fit and final input identities must differ")
    # Copy through canonical JSON so callers cannot mutate stored reviewed bodies.
    return json.loads(json.dumps({"artifact": artifact.to_dict(), "evidence": body}, allow_nan=False))


def runtime_subject(
    cfg: Config, conn: sqlite3.Connection, embedder: Any, artifact: CalibrationArtifact
) -> dict[str, str]:
    from magicite.core import router, trust

    generation, snapshot, schema, tokenizer, _ = router.pin_index_identity(conn)
    if not all((generation, snapshot, schema, tokenizer)):
        raise InvalidInputError("calibration requires pinned generation/snapshot/schema/tokenizer")
    auth = trust.authenticated_snapshot(cfg)
    from magicite.core.trust_custodian import HEAD_FIELDS

    history_head = {
        key: auth.head.get(key)
        for key in (*HEAD_FIELDS, "policy_digest", "pending_record_id", "legacy_reconciliation")
    }
    provider = getattr(embedder, "_inner", embedder)
    cache = getattr(provider, "_cache_dir", None)
    files = {}
    if cache:
        cache_path = Path(cache)
        if not cache_path.is_dir():
            raise InvalidInputError("calibration model cache unavailable")
        for path in sorted(cache_path.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(cache_path))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if cfg.embedding_provider != "hashing" and not files:
        raise InvalidInputError("calibration requires observable immutable model artifact bytes")
    model = {
        "name": embedder.model_name,
        "dim": embedder.dim,
        "provider": type(provider).__module__ + "." + type(provider).__qualname__,
        "files": files,
    }
    package = Path(__file__).resolve().parents[1]
    mechanism = {
        str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.rglob("*.py"))
    }
    return {
        "artifact_digest": artifact.digest,
        "base_policy_id": artifact.policy_id,
        "base_policy_digest": routing_policy.compute_policy_digest(artifact.policy_id, cfg),
        "config_digest": routing_policy.compute_config_digest(cfg),
        "model_digest": canonical_digest(model),
        "registry_digest": router._registry_digest(conn),
        "generation": str(generation),
        "snapshot": str(snapshot),
        "schema": str(schema),
        "tokenizer": str(tokenizer),
        "custody_digest": canonical_digest({"head": history_head, "policy": auth.policy}),
        "score_semantics": "pre-abstention-ordered-slate/1:" + artifact.policy_id,
        "mechanism_digest": canonical_digest(mechanism),
    }


def check_live(
    cfg: Config, conn: sqlite3.Connection, embedder: Any, bundle: dict[str, Any]
) -> CalibrationArtifact:
    checked = validate(bundle["artifact"], bundle["evidence"])
    artifact = CalibrationArtifact.from_dict(checked["artifact"])
    live = runtime_subject(cfg, conn, embedder, artifact)
    expected = checked["evidence"]["subject"]
    if live != expected:
        raise InvalidInputError(
            "calibration live identity or current custody drift: "
            + ", ".join(k for k in live if live[k] != expected[k]),
            details={"changed_bindings": sorted(k for k in live if live[k] != expected[k])},
        )
    return artifact
