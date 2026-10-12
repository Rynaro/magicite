"""Abstention calibration artifacts (contracts.md C4 / S07).

Thresholds and margins are fitted **only** on the calibration split. Confidence
remains null when no compatible calibration artifact exists. Incompatible or
stale calibrations are cleared/ignored — never report stale probabilities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from magicite.config import Config
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.errors import InvalidInputError

CALIBRATION_SCHEMA = "CalibrationArtifact/1"
CALIBRATION_SCHEMA_V2 = "CalibrationArtifact/2"
EXPLANATION_VERSION = "abstention-rule/1"

#: Frozen decision rule id for the locked rejection-set gate (AC-S07-02).
FROZEN_ABSTENTION_RULE = "threshold-margin-v1"

#: Default locked rejection-set query texts used when no external corpus is
#: available. Fingerprints (never raw text) are stored on the artifact.
DEFAULT_REJECTION_QUERIES: tuple[str, ...] = (
    "no matching skill should abstain",
    "unrelated query with no relevant skill in the registry",
)


def _finite_number(value: int | float) -> bool:
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


@dataclass(frozen=True)
class CalibrationExample:
    """One calibration-split observation.

    ``query_fingerprint`` is an HMAC fingerprint — never raw query text.
    ``label_relevant`` is True when the query has at least one relevant skill.
    """

    query_fingerprint: str
    top_score: float
    margin: float
    label_relevant: bool
    split: Literal["calibration"] = "calibration"


@dataclass(frozen=True)
class CalibrationArtifact:
    """Durable calibration record with provenance + content digest."""

    calibration_id: str
    digest: str
    policy_id: str
    policy_digest: str
    config_digest: str
    score_threshold: float
    margin_threshold: float
    rule_id: str
    split: Literal["calibration"]
    rejection_query_fingerprints: tuple[str, ...]
    data_provenance: Mapping[str, Any]
    n_examples: int
    schema_version: str = CALIBRATION_SCHEMA

    evaluation_budget_digest: str | None = None
    probability_model: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "calibration_id": self.calibration_id,
            "digest": self.digest,
            "policy_id": self.policy_id,
            "policy_digest": self.policy_digest,
            "config_digest": self.config_digest,
            "score_threshold": self.score_threshold,
            "margin_threshold": self.margin_threshold,
            "rule_id": self.rule_id,
            "split": self.split,
            "rejection_query_fingerprints": list(self.rejection_query_fingerprints),
            "data_provenance": dict(self.data_provenance),
            "n_examples": self.n_examples,
        }
        if self.schema_version == CALIBRATION_SCHEMA_V2:
            result.update(
                evaluation_budget_digest=self.evaluation_budget_digest,
                probability_model=dict(self.probability_model)
                if self.probability_model is not None
                else None,
            )
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationArtifact:
        if not isinstance(data, Mapping):
            raise InvalidInputError("calibration must be a mapping")
        required = set(cls.__dataclass_fields__) - {"evaluation_budget_digest", "probability_model"}
        if data.get("schema_version") == CALIBRATION_SCHEMA_V2:
            required |= {"evaluation_budget_digest", "probability_model"}
        if not isinstance(data, Mapping) or set(data) != required:
            raise InvalidInputError("calibration fields must match the supported schema")
        for key in ("calibration_id", "digest", "policy_id", "policy_digest", "config_digest"):
            if not isinstance(data[key], str) or not data[key]:
                raise InvalidInputError(f"calibration {key} must be a nonempty string")
        if (
            data["schema_version"] not in (CALIBRATION_SCHEMA, CALIBRATION_SCHEMA_V2)
            or data["split"] != "calibration"
        ):
            raise InvalidInputError("unsupported calibration schema or split")
        if data["rule_id"] != FROZEN_ABSTENTION_RULE:
            raise InvalidInputError("unsupported calibration rule")
        for key in ("score_threshold", "margin_threshold"):
            value = data[key]
            if type(value) not in (int, float) or not _finite_number(value):
                raise InvalidInputError(f"calibration {key} must be finite numeric")
        if type(data["n_examples"]) is not int or data["n_examples"] <= 0:
            raise InvalidInputError("calibration count must be a positive integer")
        provenance = data["data_provenance"]
        if not isinstance(provenance, dict) or provenance.get("split") != "calibration":
            raise InvalidInputError("calibration provenance must identify calibration split")
        if type(provenance.get("n_examples")) is not int or provenance["n_examples"] != data["n_examples"]:
            raise InvalidInputError("calibration provenance count mismatch")
        if provenance.get("rule_id") != FROZEN_ABSTENTION_RULE:
            raise InvalidInputError("calibration provenance rule mismatch")
        negatives = provenance.get("n_non_relevant")
        examples = provenance.get("example_fingerprints")
        if (
            type(negatives) is not int
            or not 0 <= negatives <= data["n_examples"]
            or not isinstance(examples, list)
            or len(examples) != data["n_examples"]
            or any(not isinstance(x, str) or not x for x in examples)
        ):
            raise InvalidInputError("calibration provenance observations invalid")
        try:
            json.dumps(provenance, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("calibration provenance must be finite JSON") from exc
        fps = data["rejection_query_fingerprints"]
        if not isinstance(fps, list) or any(not isinstance(x, str) or not x for x in fps):
            raise InvalidInputError("calibration rejection fingerprints must be strings")
        if data["schema_version"] == CALIBRATION_SCHEMA_V2:
            import re

            budget = data["evaluation_budget_digest"]
            if budget is not None and (
                not isinstance(budget, str) or not re.fullmatch("[0-9a-f]{64}", budget)
            ):
                raise InvalidInputError("invalid evaluation budget digest")
            if data["probability_model"] is not None:
                validate_probability_model(data["probability_model"])
        if data["digest"] != compute_artifact_digest(data):
            raise InvalidInputError("calibration canonical digest mismatch")
        return cls(**{**data, "rejection_query_fingerprints": tuple(fps)})


@dataclass(frozen=True)
class AbstentionDecision:
    """Outcome of the frozen abstention decision rule."""

    abstain: bool
    reason_codes: tuple[str, ...]
    rule_id: str
    calibrated: bool
    confidence_value: float | None
    calibration_id: str | None
    calibration_digest: str | None


def calibration_dir(cfg: Config) -> Path:
    return cfg.data_dir / "calibration"


def calibration_path(cfg: Config, calibration_id: str | None = None) -> Path:
    """Active artifact path (``active.json``) or a named id file."""
    root = calibration_dir(cfg)
    if calibration_id is None:
        return root / "active.json"
    return root / f"{calibration_id}.json"


def compute_artifact_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 over canonical JSON excluding the digest field itself."""
    body = {k: v for k, v in payload.items() if k != "digest"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rejection_fingerprints(
    cfg: Config,
    queries: Sequence[str] = DEFAULT_REJECTION_QUERIES,
) -> tuple[str, ...]:
    """HMAC fingerprints for the locked rejection set (never persist raw text)."""
    key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return tuple(fingerprint_key_mod.query_fingerprint(q, key=key) for q in queries)


def fit_abstention(
    examples: Sequence[CalibrationExample],
    *,
    cfg: Config,
    policy_id: str,
    policy_digest: str,
    config_digest: str,
    calibration_id: str | None = None,
    rejection_queries: Sequence[str] = DEFAULT_REJECTION_QUERIES,
) -> CalibrationArtifact:
    """Fit score/margin thresholds on the calibration split only.

    Rule (frozen ``threshold-margin-v1``):
    - Score threshold = max top_score among non-relevant (no-match) examples,
      or 0.0 when none exist.
    - Margin threshold = max margin among non-relevant examples, or 0.0.
    - A candidate selection abstains when ``top_score <= score_threshold`` OR
      ``margin <= margin_threshold``.
    - Queries whose fingerprint is in the locked rejection set always abstain.
    """
    if not examples:
        raise InvalidInputError("calibration fit requires at least one calibration example")
    for ex in examples:
        if ex.split != "calibration":
            raise InvalidInputError(f"refusing non-calibration split {ex.split!r} in fit_abstention")

    non_relevant = [ex for ex in examples if not ex.label_relevant]
    if non_relevant:
        score_threshold = max(ex.top_score for ex in non_relevant)
        margin_threshold = max(ex.margin for ex in non_relevant)
    else:
        # No locked negatives in the split — fail closed to full abstention
        # rather than inventing a permissive threshold.
        score_threshold = max(ex.top_score for ex in examples)
        margin_threshold = max(ex.margin for ex in examples)

    rej_fps = rejection_fingerprints(cfg, rejection_queries)
    cid = calibration_id or f"cal_{hashlib.sha256(policy_digest.encode()).hexdigest()[:12]}"
    provenance = {
        "split": "calibration",
        "n_examples": len(examples),
        "n_non_relevant": len(non_relevant),
        "example_fingerprints": [ex.query_fingerprint for ex in examples],
        "rule_id": FROZEN_ABSTENTION_RULE,
        # Never store raw query text.
    }
    provisional = {
        "schema_version": CALIBRATION_SCHEMA,
        "calibration_id": cid,
        "policy_id": policy_id,
        "policy_digest": policy_digest,
        "config_digest": config_digest,
        "score_threshold": score_threshold,
        "margin_threshold": margin_threshold,
        "rule_id": FROZEN_ABSTENTION_RULE,
        "split": "calibration",
        "rejection_query_fingerprints": list(rej_fps),
        "data_provenance": provenance,
        "n_examples": len(examples),
    }
    digest = compute_artifact_digest(provisional)
    return CalibrationArtifact(
        calibration_id=cid,
        digest=digest,
        policy_id=policy_id,
        policy_digest=policy_digest,
        config_digest=config_digest,
        score_threshold=score_threshold,
        margin_threshold=margin_threshold,
        rule_id=FROZEN_ABSTENTION_RULE,
        split="calibration",
        rejection_query_fingerprints=rej_fps,
        data_provenance=provenance,
        n_examples=len(examples),
    )


def decide_abstention(
    *,
    query_fingerprint: str,
    top_score: float | None,
    margin: float | None,
    artifact: CalibrationArtifact | None,
    expected_policy_digest: str | None = None,
    expected_config_digest: str | None = None,
    fallback_score_threshold: float | None = None,
    fallback_margin_threshold: float | None = None,
    abstention_enabled: bool = True,
    rank_depth: int | None = None,
) -> AbstentionDecision:
    """Apply the frozen abstention rule.

    When a compatible calibration artifact exists, use its fitted thresholds
    (calibrated). Otherwise, if ``abstention_enabled`` and config fallback
    thresholds are provided, apply them as an **uncalibrated** rule (confidence
    remains null). When disabled / no thresholds, do not abstain for score/margin.
    """
    if not abstention_enabled:
        return AbstentionDecision(
            abstain=False,
            reason_codes=("abstention_disabled",),
            rule_id=FROZEN_ABSTENTION_RULE,
            calibrated=False,
            confidence_value=None,
            calibration_id=None,
            calibration_digest=None,
        )

    if artifact is not None:
        if expected_policy_digest is not None and artifact.policy_digest != expected_policy_digest:
            artifact = None
        elif expected_config_digest is not None and artifact.config_digest != expected_config_digest:
            artifact = None

    if artifact is None:
        # Uncalibrated fallback from Config knobs (N2). Defaults of 0.0 mean
        # "no threshold abstention" (legacy always-return); operators opt in by
        # setting a positive threshold.
        score_thr = float(fallback_score_threshold) if fallback_score_threshold is not None else 0.0
        margin_thr = float(fallback_margin_threshold) if fallback_margin_threshold is not None else 0.0
        if score_thr <= 0.0 and margin_thr <= 0.0:
            return AbstentionDecision(
                abstain=False,
                reason_codes=("uncalibrated",),
                rule_id=FROZEN_ABSTENTION_RULE,
                calibrated=False,
                confidence_value=None,
                calibration_id=None,
                calibration_digest=None,
            )
        reasons: list[str] = ["uncalibrated"]
        abstain = False
        if top_score is None:
            abstain = True
            reasons.append("no_eligible_candidate")
        else:
            if score_thr > 0.0 and top_score <= score_thr:
                abstain = True
                reasons.append("below_score_threshold")
            if margin is not None and margin_thr > 0.0 and margin <= margin_thr:
                abstain = True
                reasons.append("below_margin_threshold")
        return AbstentionDecision(
            abstain=abstain,
            reason_codes=tuple(dict.fromkeys(reasons if abstain else ["uncalibrated", "select"])),
            rule_id=FROZEN_ABSTENTION_RULE,
            calibrated=False,
            confidence_value=None,
            calibration_id=None,
            calibration_digest=None,
        )

    reasons = []
    abstain = False

    if query_fingerprint in artifact.rejection_query_fingerprints:
        abstain = True
        reasons.append("locked_rejection_set")

    if top_score is None:
        abstain = True
        reasons.append("no_eligible_candidate")
    else:
        if top_score <= artifact.score_threshold:
            abstain = True
            reasons.append("below_score_threshold")
        if margin is not None and margin <= artifact.margin_threshold:
            abstain = True
            reasons.append("below_margin_threshold")

    # Threshold margins are not calibrated probabilities.
    confidence: float | None = None
    if (
        not abstain
        and artifact.schema_version == CALIBRATION_SCHEMA_V2
        and artifact.probability_model is not None
        and margin is not None
    ):
        if rank_depth == artifact.probability_model["rank_depth"] and type(rank_depth) is int:
            confidence = probability_at_margin(artifact.probability_model, margin)
        else:
            reasons.append("probability_rank_depth_mismatch")

    if abstain and not reasons:
        reasons.append("abstain")

    return AbstentionDecision(
        abstain=abstain,
        reason_codes=tuple(reasons) if abstain else ("select", *reasons),
        rule_id=artifact.rule_id,
        calibrated=True,
        confidence_value=None if abstain else confidence,
        calibration_id=artifact.calibration_id,
        calibration_digest=artifact.digest,
    )


def _fsync_dir(directory: Path) -> None:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def save_calibration(cfg: Config, artifact: CalibrationArtifact, *, make_active: bool = True) -> Path:
    """Atomically persist a calibration artifact (tmp + fsync + rename + dir fsync)."""
    root = calibration_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    payload = artifact.to_dict()
    if payload["digest"] != compute_artifact_digest(payload):
        raise InvalidInputError("calibration digest mismatch on save")
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    named = calibration_path(cfg, artifact.calibration_id)
    _atomic_write_text(named, text)
    if make_active:
        _atomic_write_text(calibration_path(cfg), text)
    return named


def load_calibration(
    cfg: Config,
    *,
    calibration_id: str | None = None,
    expected_policy_digest: str | None = None,
    expected_config_digest: str | None = None,
) -> CalibrationArtifact | None:
    """Load active/named calibration; return None when missing or incompatible.

    Incompatible/stale artifacts are ignored (and cleared when active) rather
    than producing stale confidence values.
    """
    path = calibration_path(cfg, calibration_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        artifact = CalibrationArtifact.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, InvalidInputError):
        if calibration_id is None:
            clear_calibration(cfg)
        return None

    recomputed = compute_artifact_digest(artifact.to_dict())
    if not hmac_compare(artifact.digest, recomputed):
        if calibration_id is None:
            clear_calibration(cfg)
        return None

    if expected_policy_digest is not None and artifact.policy_digest != expected_policy_digest:
        if calibration_id is None:
            clear_calibration(cfg)
        return None
    if expected_config_digest is not None and artifact.config_digest != expected_config_digest:
        if calibration_id is None:
            clear_calibration(cfg)
        return None
    return artifact


def clear_calibration(cfg: Config) -> None:
    """Remove the active calibration pointer (named artifacts retained)."""
    path = calibration_path(cfg)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def hmac_compare(a: str, b: str) -> bool:
    import hmac as _hmac

    return _hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def score_margin(scores: Sequence[float]) -> float | None:
    """Top-1 minus top-2; ``None`` when fewer than two scores."""
    if len(scores) < 2:
        return float(scores[0]) if scores else None
    ordered = sorted((float(s) for s in scores), reverse=True)
    return ordered[0] - ordered[1]


__all__ = [
    "CALIBRATION_SCHEMA",
    "DEFAULT_REJECTION_QUERIES",
    "EXPLANATION_VERSION",
    "FROZEN_ABSTENTION_RULE",
    "AbstentionDecision",
    "CalibrationArtifact",
    "CalibrationExample",
    "calibration_dir",
    "calibration_path",
    "clear_calibration",
    "compute_artifact_digest",
    "decide_abstention",
    "fit_abstention",
    "load_calibration",
    "rejection_fingerprints",
    "save_calibration",
    "score_margin",
]


def validate_probability_model(model: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "method",
        "feature",
        "target",
        "x",
        "y",
        "n_examples",
        "calibration_input_digest",
        "rank_depth",
        "interpolation",
        "extrapolation",
        "ece_edges",
    }
    fixed = {
        "schema": "magicite/isotonic-top1-confidence/1",
        "method": "weighted-pool-adjacent-violators/1",
        "feature": "top1-minus-top2;singleton=top1",
        "target": "raw-top1-correctness",
        "interpolation": "linear",
        "extrapolation": "endpoint",
        "ece_edges": [i / 10 for i in range(11)],
    }
    if (
        not isinstance(model, Mapping)
        or set(model) != required
        or any(model[k] != v for k, v in fixed.items())
    ):
        raise InvalidInputError("unsupported probability model fields/semantics")
    import re

    if not isinstance(model["calibration_input_digest"], str) or not re.fullmatch(
        "[0-9a-f]{64}", model["calibration_input_digest"]
    ):
        raise InvalidInputError("probability calibration input digest invalid")
    if any(type(model[k]) is not int or model[k] <= 0 for k in ("n_examples", "rank_depth")):
        raise InvalidInputError("probability count/depth invalid")
    if any(type(v) not in (int, float) for v in model["ece_edges"]):
        raise InvalidInputError("ECE edges must be numeric")
    x, y = model["x"], model["y"]
    if (
        not isinstance(x, list)
        or not isinstance(y, list)
        or not x
        or len(x) != len(y)
        or any(type(v) not in (int, float) or not _finite_number(v) for v in x + y)
        or any(a >= b for a, b in zip(x, x[1:], strict=False))
        or any(not 0 <= v <= 1 for v in y)
        or any(a > b for a, b in zip(y, y[1:], strict=False))
    ):
        raise InvalidInputError("probability knots must be finite, increasing and monotone")


def fit_probability(
    margins: Sequence[float], correct: Sequence[bool], *, calibration_input_digest: str, rank_depth: int
) -> tuple[dict[str, Any] | None, str | None]:
    if (
        len(margins) != len(correct)
        or any(type(v) not in (int, float) or not _finite_number(v) for v in margins)
        or any(type(v) is not bool for v in correct)
    ):
        raise InvalidInputError("invalid probability calibration observations")
    if not margins or len(set(correct)) < 2:
        return None, "empty-or-one-class-top1-calibration"
    counts: dict[float, list[int]] = {}
    for x, y in zip(margins, correct, strict=True):
        pair = counts.setdefault(float(x), [0, 0])
        pair[0] += int(y)
        pair[1] += 1
    xs = sorted(counts)
    blocks: list[tuple[list[float], int, int]] = []
    for x in xs:
        yes, n = counts[x]
        blocks.append(([x], yes, n))
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append((left[0] + right[0], left[1] + right[1], left[2] + right[2]))
    fitted = {x: yes / n for keys, yes, n in blocks for x in keys}
    model = {
        "schema": "magicite/isotonic-top1-confidence/1",
        "method": "weighted-pool-adjacent-violators/1",
        "feature": "top1-minus-top2;singleton=top1",
        "target": "raw-top1-correctness",
        "x": xs,
        "y": [fitted[x] for x in xs],
        "n_examples": len(margins),
        "calibration_input_digest": calibration_input_digest,
        "rank_depth": rank_depth,
        "interpolation": "linear",
        "extrapolation": "endpoint",
        "ece_edges": [i / 10 for i in range(11)],
    }
    validate_probability_model(model)
    return model, None


def probability_at_margin(model: Mapping[str, Any], margin: float) -> float:
    validate_probability_model(model)
    if type(margin) not in (int, float) or not _finite_number(margin):
        raise InvalidInputError("finite probability feature required")
    from bisect import bisect_right

    x, y = model["x"], model["y"]
    index = bisect_right(x, margin)
    if index == 0:
        return float(y[0])
    if index == len(x):
        return float(y[-1])
    fraction = (margin - x[index - 1]) / (x[index] - x[index - 1])
    return float(y[index - 1] * (1 - fraction) + fraction * y[index])


def with_probability(
    artifact: CalibrationArtifact,
    model: Mapping[str, Any] | None,
    *,
    evaluation_budget_digest: str | None = None,
) -> CalibrationArtifact:
    payload = artifact.to_dict()
    payload.update(
        schema_version=CALIBRATION_SCHEMA_V2,
        probability_model=dict(model) if model is not None else None,
        evaluation_budget_digest=evaluation_budget_digest,
    )
    payload["digest"] = compute_artifact_digest(payload)
    return CalibrationArtifact.from_dict(payload)
