"""Abstention calibration artifacts (contracts.md C4 / S07).

Thresholds and margins are fitted **only** on the calibration split. Confidence
remains null when no compatible calibration artifact exists. Incompatible or
stale calibrations are cleared/ignored — never report stale probabilities.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from magicite.config import Config
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.errors import InvalidInputError

CALIBRATION_SCHEMA = "CalibrationArtifact/1"
EXPLANATION_VERSION = "abstention-rule/1"

#: Frozen decision rule id for the locked rejection-set gate (AC-S07-02).
FROZEN_ABSTENTION_RULE = "threshold-margin-v1"

#: Default locked rejection-set query texts used when no external corpus is
#: available. Fingerprints (never raw text) are stored on the artifact.
DEFAULT_REJECTION_QUERIES: tuple[str, ...] = (
    "no matching skill should abstain",
    "unrelated query with no relevant skill in the registry",
)


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

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationArtifact:
        if data.get("schema_version") != CALIBRATION_SCHEMA:
            raise InvalidInputError(
                f"unsupported calibration schema {data.get('schema_version')!r}"
            )
        return cls(
            calibration_id=str(data["calibration_id"]),
            digest=str(data["digest"]),
            policy_id=str(data["policy_id"]),
            policy_digest=str(data["policy_digest"]),
            config_digest=str(data["config_digest"]),
            score_threshold=float(data["score_threshold"]),
            margin_threshold=float(data["margin_threshold"]),
            rule_id=str(data["rule_id"]),
            split="calibration",
            rejection_query_fingerprints=tuple(
                str(x) for x in (data.get("rejection_query_fingerprints") or ())
            ),
            data_provenance=dict(data.get("data_provenance") or {}),
            n_examples=int(data.get("n_examples") or 0),
        )


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
            raise InvalidInputError(
                f"refusing non-calibration split {ex.split!r} in fit_abstention"
            )

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
) -> AbstentionDecision:
    """Apply the frozen abstention rule. Incompatible artifacts → uncalibrated."""
    if artifact is None:
        return AbstentionDecision(
            abstain=False,
            reason_codes=("uncalibrated",),
            rule_id=FROZEN_ABSTENTION_RULE,
            calibrated=False,
            confidence_value=None,
            calibration_id=None,
            calibration_digest=None,
        )

    if expected_policy_digest is not None and artifact.policy_digest != expected_policy_digest:
        return AbstentionDecision(
            abstain=False,
            reason_codes=("calibration_incompatible", "uncalibrated"),
            rule_id=FROZEN_ABSTENTION_RULE,
            calibrated=False,
            confidence_value=None,
            calibration_id=None,
            calibration_digest=None,
        )
    if expected_config_digest is not None and artifact.config_digest != expected_config_digest:
        return AbstentionDecision(
            abstain=False,
            reason_codes=("calibration_incompatible", "uncalibrated"),
            rule_id=FROZEN_ABSTENTION_RULE,
            calibrated=False,
            confidence_value=None,
            calibration_id=None,
            calibration_digest=None,
        )

    reasons: list[str] = []
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

    # Calibrated confidence: distance above thresholds, clipped to [0, 1].
    confidence: float | None = None
    if not abstain and top_score is not None:
        score_gap = max(0.0, top_score - artifact.score_threshold)
        margin_gap = max(0.0, (margin or 0.0) - artifact.margin_threshold)
        confidence = max(0.0, min(1.0, 0.5 * (score_gap + margin_gap)))

    if abstain and not reasons:
        reasons.append("abstain")

    return AbstentionDecision(
        abstain=abstain,
        reason_codes=tuple(reasons) if abstain else ("select",),
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
