"""Performance envelope validation (evaluation.md E6 / AC-S14-02).

Shared CI validates **completeness** only. Dedicated runners validate
preregistered **budgets** for production-provider measurements. Hashing
provider timings must never be treated as production FastEmbed evidence.
"""

from __future__ import annotations

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
    if not isinstance(value, (int, float)):
        return [f"measurement {measured_key!r} must be numeric"]
    if float(value) > float(limit):
        return [f"{measured_key}={value}{unit} exceeds budget {limit}{unit}"]
    return []


def budget_errors(result: dict[str, Any], profile: BenchmarkProfile) -> list[str]:
    """Dedicated-runner gate against preregistered E6 envelopes."""
    errors = completeness_errors(result)
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


def validate_envelope(
    result: dict[str, Any],
    profile: BenchmarkProfile,
    *,
    mode: EnvelopeMode,
) -> EnvelopeCheck:
    if mode == "completeness":
        errors = completeness_errors(result)
        notes = (
            "shared CI validates completeness only; dedicated runner owns budgets",
        )
        return EnvelopeCheck(mode=mode, ok=not errors, errors=tuple(errors), notes=notes)
    if mode == "budget":
        errors = budget_errors(result, profile)
        notes = (
            "dedicated runner validates evaluation.md E6 envelopes for production provider",
        )
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
        reasons.append(
            f"n_candidates={n_candidates} < profile corpus size {profile_corpus_artifacts}"
        )

    return (len(reasons) == 0, reasons)
