"""Performance envelope validation (evaluation.md E6 / AC-S14-02).

Shared CI validates **completeness** only. Dedicated runners validate
preregistered **budgets** for production-provider measurements. Hashing
provider timings must never be treated as production FastEmbed evidence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from magicite.eval.profiles import (
    BenchmarkProfile,
    EnvelopeBudget,
    profile_manifest_errors,
)

EnvelopeMode = Literal["completeness", "budget"]


@dataclass(frozen=True)
class EnvelopeCheck:
    mode: EnvelopeMode
    ok: bool
    errors: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "errors": list(self.errors),
            "notes": list(self.notes),
        }


_MEASUREMENT_KEYS = (
    "warm_route_p50_ms",
    "warm_route_p95_ms",
    "warm_route_p99_ms",
    "process_rss_gib",
    "index_gib",
    "cold_ready_s",
    "index_build_s",
    "index_build_peak_rss_gib",
    "payload_tokens",
    "cache_hit_rates",
    "truncations_fallbacks",
)


def completeness_errors(result: dict[str, Any]) -> list[str]:
    """Shared-CI gate: profile manifest + measurement key presence."""
    errors = profile_manifest_errors(result)
    measurements = result.get("measurements")
    if not isinstance(measurements, dict):
        errors.append("measurements must be an object")
        return errors
    for key in _MEASUREMENT_KEYS:
        if key not in measurements:
            errors.append(f"measurements missing key {key!r}")
    provider = (result.get("fingerprint") or {}).get("provider")
    if provider not in {"hashing", "production"}:
        errors.append("fingerprint.provider must be hashing or production")
    return errors


def _budget_field_errors(
    *,
    budget: EnvelopeBudget,
    measurements: dict[str, Any],
    field_name: str,
    measured_key: str,
    unit: str,
) -> list[str]:
    limit = getattr(budget, field_name)
    if limit is None:
        return []
    value = measurements.get(measured_key)
    if value is None:
        return [f"budget check missing measurement {measured_key!r}"]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return [f"measurement {measured_key!r} must be finite, nonnegative numeric"]
    if float(value) > float(limit):
        return [f"{measured_key}={value}{unit} exceeds budget {limit}{unit}"]
    return []


def budget_errors(result: dict[str, Any], profile: BenchmarkProfile) -> list[str]:
    """Dedicated-runner gate against preregistered E6 envelopes."""
    errors = completeness_errors(result)
    if (result.get("profile") or {}).get("profile_id") != profile.profile_id:
        errors.append("profile does not match preregistered envelope")
    fingerprint = result.get("fingerprint") or {}
    provider = fingerprint.get("provider")
    if provider != "production":
        errors.append(
            "budget validation requires fingerprint.provider=production "
            "(hashing timings are not production evidence)"
        )
        return errors

    measurements = result.get("measurements") or {}
    if not isinstance(measurements, dict):
        return errors

    errors.extend(measurement_provenance_errors(result, profile))
    budget = profile.budget
    checks = (
        ("warm_route_p95_ms", "warm_route_p95_ms", "ms"),
        ("warm_route_p99_ms", "warm_route_p99_ms", "ms"),
        ("process_rss_gib", "process_rss_gib", "GiB"),
        ("index_gib", "index_gib", "GiB"),
        ("cold_ready_s", "cold_ready_s", "s"),
        ("index_build_s", "index_build_s", "s"),
        ("index_build_peak_rss_gib", "index_build_peak_rss_gib", "GiB"),
    )
    for field_name, measured_key, unit in checks:
        errors.extend(
            _budget_field_errors(
                budget=budget,
                measurements=measurements,
                field_name=field_name,
                measured_key=measured_key,
                unit=unit,
            )
        )
    return errors


def _finite_nonnegative(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def measurement_provenance_errors(result: dict[str, Any], profile: BenchmarkProfile) -> list[str]:
    """Reject placeholders: a declared parameter is not a measured observation."""
    errors: list[str] = []
    fingerprint = result.get("fingerprint") or {}
    for key in ("model_digest", "dependency_lock_sha256"):
        value = fingerprint.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) or value == "0" * 64:
            errors.append(f"fingerprint.{key} must identify actual artifacts")
    for name, state in (result.get("cache_states") or {}).items():
        if not isinstance(state, dict) or state.get("measured") is not True:
            errors.append(f"cache state {name} has not been measured")
        elif not _finite_nonnegative(state.get("latency_ms")):
            errors.append(f"cache state {name} has invalid timing")
    measurements = result.get("measurements") or {}
    for key in ("payload_tokens", "warm_route_p50_ms"):
        value = measurements.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            errors.append(f"missing or invalid measurement {key}")
    rates = measurements.get("cache_hit_rates")
    if (
        not isinstance(rates, dict)
        or not rates
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
            for v in rates.values()
        )
    ):
        errors.append("cache hit rates must be measured numeric fractions")
    repetitions = result.get("repetition_results")
    if not isinstance(repetitions, list) or len(repetitions) < profile.repetitions:
        errors.append("missing clean-process repetitions")
    else:
        pids = set()
        for repetition in repetitions:
            if not isinstance(repetition, dict):
                errors.append("invalid repetition record")
                continue
            pid = repetition.get("process_id")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                errors.append("invalid process identity")
            else:
                pids.add(pid)
            for field, minimum in (
                ("measured_queries", profile.measured_queries_min),
                ("warmup_queries", profile.warmup_queries),
            ):
                count = repetition.get(field)
                if isinstance(count, bool) or not isinstance(count, int) or count < minimum:
                    errors.append(f"insufficient or invalid {field}")
            observations = (repetition.get("measurements") or {}).get("warm_durations_s")
            if (
                not isinstance(observations, list)
                or len(observations) != repetition.get("measured_queries")
                or not all(_finite_nonnegative(value) for value in observations)
            ):
                errors.append("measured count must match finite recorded observations")
            rep_measurements = repetition.get("measurements") or {}
            if (
                isinstance(observations, list)
                and observations
                and all(_finite_nonnegative(value) for value in observations)
            ):
                from magicite.eval.scale import latency_percentiles_ms

                percentiles = latency_percentiles_ms(observations)
                for percentile in ("p50", "p95", "p99"):
                    if rep_measurements.get(f"warm_route_{percentile}_ms") != percentiles[f"{percentile}_ms"]:
                        errors.append("reported percentiles differ from recorded observations")
            for key in (
                "warm_route_p95_ms",
                "warm_route_p99_ms",
                "process_rss_gib",
                "index_gib",
                "cold_ready_s",
                "index_build_s",
                "index_build_peak_rss_gib",
            ):
                errors.extend(
                    _budget_field_errors(
                        budget=profile.budget,
                        measurements=rep_measurements,
                        field_name=key,
                        measured_key=key,
                        unit="",
                    )
                )
            if repetition.get("status") != "measured":
                errors.append("incomplete repetition")
        if None in pids or len(pids) != len(repetitions):
            errors.append("repetitions must use distinct clean processes")
    return errors


def validate_envelope(
    result: dict[str, Any],
    profile: BenchmarkProfile,
    *,
    mode: EnvelopeMode,
) -> EnvelopeCheck:
    if mode == "completeness":
        errors = completeness_errors(result)
        notes = ("shared CI validates completeness only; dedicated runner owns budgets",)
        return EnvelopeCheck(mode=mode, ok=not errors, errors=tuple(errors), notes=notes)
    if mode == "budget":
        errors = budget_errors(result, profile)
        notes = ("dedicated runner validates evaluation.md E6 envelopes for production provider",)
        return EnvelopeCheck(mode=mode, ok=not errors, errors=tuple(errors), notes=notes)
    raise ValueError(f"unknown envelope mode {mode!r}")


__all__ = [
    "EnvelopeCheck",
    "EnvelopeMode",
    "budget_errors",
    "completeness_errors",
    "compute_ga_eligibility",
    "validate_envelope",
]


def compute_ga_eligibility(
    *,
    provider: str,
    profile_ga_support_claim: bool,
    corpus_kind: str,
    envelope_mode: str,
    envelope_budget_ok: bool | None,
    corpus_path: str | None,
    corpus_license: str | None,
    n_candidates: int | None,
    profile_corpus_artifacts: int,
    measured_result: dict[str, Any] | None = None,
    support_runs: list[dict[str, Any]] | None = None,
) -> tuple[bool, list[str]]:
    """Decide whether a matrix run may claim GA support evidence.

    ``ga_eligible`` is True only when **all** of the following hold:

    - ``provider == "production"``
    - the profile's ``budget.ga_support_claim`` is True
    - ``corpus.kind == "manifest"``
    - ``envelope_mode == "budget"`` and the budget envelope passed
    - the corpus is not a fixture (path not under a fixtures tree; license
      does not look fixture-only; candidate count meets the profile size)

    Otherwise returns False with a non-empty ``ga_ineligible_reasons`` list.
    """
    reasons: list[str] = []
    if provider != "production":
        reasons.append(f"provider={provider!r} (need production)")
    if not profile_ga_support_claim:
        reasons.append("profile.budget.ga_support_claim is false")
    if corpus_kind != "manifest":
        reasons.append(f"corpus.kind={corpus_kind!r} (need manifest)")
    if envelope_mode != "budget":
        reasons.append(f"envelope_mode={envelope_mode!r} (need budget)")
    elif envelope_budget_ok is not True:
        reasons.append("budget envelope did not pass")

    path_l = (corpus_path or "").replace("\\", "/").lower()
    if not corpus_path:
        reasons.append("corpus path missing")
    elif "/fixtures/" in path_l or path_l.endswith("/fixtures") or "skillret-tiny" in path_l:
        reasons.append(f"corpus path looks like a fixture: {corpus_path}")

    license_l = (corpus_license or "").lower()
    if not corpus_license:
        reasons.append("corpus license missing")
    elif "fixture" in license_l:
        reasons.append(f"corpus license looks fixture-only: {corpus_license!r}")

    if n_candidates is None:
        reasons.append("n_candidates missing")
    elif n_candidates < profile_corpus_artifacts:
        reasons.append(f"n_candidates={n_candidates} < profile corpus size {profile_corpus_artifacts}")

    # Caller assertions and filesystem location are not provenance. Require
    # actual validated E6 observations and both preregistered corpus strata.
    from magicite.eval.profiles import get_profile

    runs = list(support_runs or [])
    if measured_result is not None:
        runs.append(measured_result)
    if not runs:
        reasons.append("missing measured evidence; budget_ok assertion is insufficient")
    kinds = set()
    evidence_pins: tuple[Any, ...] | None = None
    for run in runs:
        try:
            profile = get_profile(run["profile"]["profile_id"])
            if not profile.budget.ga_support_claim or profile.corpus_artifacts != profile_corpus_artifacts:
                reasons.append("support run profile does not match the claimed supported scale")
                continue
            fingerprint = run.get("fingerprint") or {}
            pins = tuple(
                fingerprint.get(key)
                for key in (
                    "provider",
                    "model_name",
                    "model_digest",
                    "dependency_lock_sha256",
                    "runner_label",
                    "platform",
                    "machine",
                    "processor",
                    "os_release",
                )
            )
            if evidence_pins is not None and pins != evidence_pins:
                reasons.append("support runs have mismatched model, dependency or runner pins")
                continue
            evidence_pins = pins
            errors = budget_errors(run, profile)
            if errors or run.get("status") != "measured":
                reasons.append("support run does not satisfy measured E6 envelope")
                continue
            corpus = run.get("corpus") or {}
            kind = corpus.get("kind")
            count = corpus.get("n_candidates")
            if isinstance(count, bool) or not isinstance(count, int) or count < profile_corpus_artifacts:
                reasons.append("support run inventory is smaller than claimed scale")
                continue
            if corpus.get("actual_artifacts") is not True or not re.fullmatch(
                r"[0-9a-f]{64}", corpus.get("artifact_inventory_sha256", "")
            ):
                reasons.append("missing verified full artifact inventory")
                continue
            if kind == "manifest":
                if any(
                    not re.fullmatch(r"[0-9a-f]{64}", corpus.get(key, ""))
                    for key in ("manifest_sha256", "content_identity_sha256")
                ):
                    reasons.append("missing bound corpus manifest or label identity")
                    continue
                identity = " ".join(
                    str(corpus.get(key) or "") for key in ("corpus_id", "license", "dataset_revision")
                ).lower()
                if "fixture" in identity or "tiny" in identity or not corpus.get("license"):
                    reasons.append("fixture or unlicensed corpus cannot qualify by relocation")
                    continue
                if (
                    corpus.get("actual_artifacts") is not True
                    or not re.fullmatch(r"[0-9a-f]{64}", corpus.get("artifact_inventory_sha256", ""))
                    or corpus.get("n_candidates", 0) < profile_corpus_artifacts
                ):
                    reasons.append("missing verified full artifact inventory")
                    continue
            kinds.add(kind)
        except (KeyError, TypeError, ValueError):
            reasons.append("invalid support run")
    if not {"manifest", "synthetic"} <= kinds:
        reasons.append("both licensed real and synthetic measured E6 runs are required")
    return (len(reasons) == 0, reasons)
