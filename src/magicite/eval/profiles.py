"""Benchmark profile manifests (evaluation.md E6 / AC-S14-01).

A declared profile names corpus scale, cache states, and the hardware /
model fingerprint fields every result MUST record. Budgets are the
preregistered targets from evaluation.md — dedicated runners enforce
them; shared CI only checks manifest completeness.
"""

from __future__ import annotations

import hashlib
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CacheState = Literal[
    "cold_process",
    "cold_model",
    "cold_index",
    "warm_index",
    "hot_query_cache",
]

REQUIRED_CACHE_STATES: tuple[CacheState, ...] = (
    "cold_process",
    "cold_model",
    "cold_index",
    "warm_index",
    "hot_query_cache",
)

REQUIRED_FINGERPRINT_FIELDS: tuple[str, ...] = (
    "python",
    "platform",
    "machine",
    "processor",
    "os_release",
    "provider",
    "model_name",
    "model_digest",
    "dependency_lock_sha256",
    "runner_label",
    "runtime_is_container",
)

ProviderKind = Literal["hashing", "production"]


@dataclass(frozen=True)
class EnvelopeBudget:
    """Preregistered resource envelope for one profile (evaluation.md E6)."""

    warm_route_p95_ms: float | None
    warm_route_p99_ms: float | None
    process_rss_gib: float | None
    index_gib: float | None
    cold_ready_s: float | None
    index_build_s: float | None
    index_build_peak_rss_gib: float | None
    ga_support_claim: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "warm_route_p95_ms": self.warm_route_p95_ms,
            "warm_route_p99_ms": self.warm_route_p99_ms,
            "process_rss_gib": self.process_rss_gib,
            "index_gib": self.index_gib,
            "cold_ready_s": self.cold_ready_s,
            "index_build_s": self.index_build_s,
            "index_build_peak_rss_gib": self.index_build_peak_rss_gib,
            "ga_support_claim": self.ga_support_claim,
        }


@dataclass(frozen=True)
class BenchmarkProfile:
    """Declared scale profile for the matrix runner."""

    profile_id: str
    corpus_artifacts: int
    measured_queries_min: int
    warmup_queries: int
    repetitions: int
    budget: EnvelopeBudget
    opt_in: bool = False
    distinct_query_rotation: bool = True
    describe: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "corpus_artifacts": self.corpus_artifacts,
            "measured_queries_min": self.measured_queries_min,
            "warmup_queries": self.warmup_queries,
            "repetitions": self.repetitions,
            "opt_in": self.opt_in,
            "distinct_query_rotation": self.distinct_query_rotation,
            "required_cache_states": list(REQUIRED_CACHE_STATES),
            "required_fingerprint_fields": list(REQUIRED_FINGERPRINT_FIELDS),
            "budget": self.budget.to_dict(),
            "describe": self.describe,
        }


#: CI-feasible smoke profile — completeness + semantic equality only.
PROFILE_CI_SMOKE = BenchmarkProfile(
    profile_id="ci-smoke",
    corpus_artifacts=100,
    measured_queries_min=8,
    warmup_queries=2,
    repetitions=1,
    opt_in=False,
    budget=EnvelopeBudget(
        warm_route_p95_ms=None,  # CI does not enforce latency budgets
        warm_route_p99_ms=None,
        process_rss_gib=None,
        index_gib=None,
        cold_ready_s=None,
        index_build_s=None,
        index_build_peak_rss_gib=None,
        ga_support_claim=False,
    ),
    describe="CI-feasible synthetic smoke; completeness only, no GA claim",
)

PROFILE_SMALL_100 = BenchmarkProfile(
    profile_id="small-100",
    corpus_artifacts=100,
    measured_queries_min=1000,
    warmup_queries=50,
    repetitions=3,
    budget=EnvelopeBudget(
        warm_route_p95_ms=250.0,
        warm_route_p99_ms=None,
        process_rss_gib=1.0,
        index_gib=None,
        cold_ready_s=30.0,
        index_build_s=None,
        index_build_peak_rss_gib=None,
        ga_support_claim=False,
    ),
    describe="E6 small profile (100 artifacts)",
)

PROFILE_SMALL_1K = BenchmarkProfile(
    profile_id="small-1k",
    corpus_artifacts=1000,
    measured_queries_min=1000,
    warmup_queries=50,
    repetitions=3,
    budget=EnvelopeBudget(
        warm_route_p95_ms=250.0,
        warm_route_p99_ms=None,
        process_rss_gib=1.0,
        index_gib=None,
        cold_ready_s=30.0,
        index_build_s=None,
        index_build_peak_rss_gib=None,
        ga_support_claim=False,
    ),
    describe="E6 small profile (1,000 artifacts)",
)

PROFILE_SUPPORTED_10K = BenchmarkProfile(
    profile_id="supported-10k",
    corpus_artifacts=10_000,
    measured_queries_min=1000,
    warmup_queries=50,
    repetitions=3,
    budget=EnvelopeBudget(
        warm_route_p95_ms=1000.0,
        warm_route_p99_ms=2000.0,
        process_rss_gib=2.0,
        index_gib=2.0,
        cold_ready_s=30.0,
        index_build_s=30.0 * 60.0,
        index_build_peak_rss_gib=4.0,
        ga_support_claim=True,
    ),
    describe="E6 supported local scale (10,000 artifacts); dedicated runner",
)

PROFILE_EXPLORATORY_50K = BenchmarkProfile(
    profile_id="exploratory-50k",
    corpus_artifacts=50_000,
    measured_queries_min=1000,
    warmup_queries=50,
    repetitions=3,
    opt_in=True,
    budget=EnvelopeBudget(
        warm_route_p95_ms=None,
        warm_route_p99_ms=None,
        process_rss_gib=None,
        index_gib=None,
        cold_ready_s=None,
        index_build_s=None,
        index_build_peak_rss_gib=None,
        ga_support_claim=False,
    ),
    describe="E6 exploratory 50k — measure only; no GA claim until amended budget",
)

PROFILES: dict[str, BenchmarkProfile] = {
    p.profile_id: p
    for p in (
        PROFILE_CI_SMOKE,
        PROFILE_SMALL_100,
        PROFILE_SMALL_1K,
        PROFILE_SUPPORTED_10K,
        PROFILE_EXPLORATORY_50K,
    )
}


def get_profile(profile_id: str) -> BenchmarkProfile:
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        known = ", ".join(sorted(PROFILES))
        raise KeyError(f"unknown benchmark profile {profile_id!r}; known: {known}") from exc


def dependency_lock_sha256(project_root: Path | None = None) -> str:
    """SHA-256 of uv.lock (or empty digest placeholder when absent)."""
    root = project_root if project_root is not None else Path.cwd()
    lock = root / "uv.lock"
    if not lock.is_file():
        return "0" * 64
    return hashlib.sha256(lock.read_bytes()).hexdigest()


def hardware_model_fingerprint(
    *,
    provider: ProviderKind,
    model_name: str,
    model_digest: str | None,
    runner_label: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Collect every required fingerprint field (AC-S14-01)."""
    digest = model_digest if model_digest else "unavailable"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "os_release": platform.release(),
        "provider": provider,
        "model_name": model_name,
        "model_digest": digest,
        "dependency_lock_sha256": dependency_lock_sha256(project_root),
        "runner_label": runner_label,
        "runtime_is_container": Path("/.dockerenv").exists()
        or os.environ.get("container") is not None,
        "container_image_digest": os.environ.get("MAGICITE_CONTAINER_IMAGE_DIGEST"),
    }


def empty_cache_state_block() -> dict[str, Any]:
    """Skeleton identifying every required cache state before measurement."""
    return {
        state: {
            "identified": True,
            "measured": False,
            "latency_ms": None,
            "notes": "",
        }
        for state in REQUIRED_CACHE_STATES
    }


def build_profile_result_skeleton(
    profile: BenchmarkProfile,
    *,
    provider: ProviderKind,
    model_name: str,
    model_digest: str | None,
    runner_label: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Result shell that already names cache states + fingerprints."""
    return {
        "schema": "magicite-benchmark-profile-result/1",
        "profile": profile.to_dict(),
        "fingerprint": hardware_model_fingerprint(
            provider=provider,
            model_name=model_name,
            model_digest=model_digest,
            runner_label=runner_label,
            project_root=project_root,
        ),
        "cache_states": empty_cache_state_block(),
        "measurements": {},
        "unevaluated": [],
        "status": "declared",
    }


def profile_manifest_errors(result: dict[str, Any]) -> list[str]:
    """Completeness errors for AC-S14-01 (shared CI gate)."""
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["profile result must be an object"]
    if result.get("schema") != "magicite-benchmark-profile-result/1":
        errors.append("schema must be magicite-benchmark-profile-result/1")

    fingerprint = result.get("fingerprint")
    if not isinstance(fingerprint, dict):
        errors.append("fingerprint must be an object")
    else:
        for field_name in REQUIRED_FINGERPRINT_FIELDS:
            if field_name not in fingerprint:
                errors.append(f"fingerprint missing required field {field_name!r}")
            elif fingerprint[field_name] in (None, ""):
                errors.append(f"fingerprint.{field_name} must be non-empty")

    cache_states = result.get("cache_states")
    if not isinstance(cache_states, dict):
        errors.append("cache_states must be an object")
    else:
        for state in REQUIRED_CACHE_STATES:
            if state not in cache_states:
                errors.append(f"cache_states missing required state {state!r}")
            else:
                block = cache_states[state]
                if not isinstance(block, dict) or block.get("identified") is not True:
                    errors.append(f"cache_states.{state} must set identified=true")

    profile = result.get("profile")
    if not isinstance(profile, dict) or not profile.get("profile_id"):
        errors.append("profile.profile_id must be present")
    return errors


__all__ = [
    "REQUIRED_CACHE_STATES",
    "REQUIRED_FINGERPRINT_FIELDS",
    "PROFILES",
    "PROFILE_CI_SMOKE",
    "PROFILE_EXPLORATORY_50K",
    "PROFILE_SMALL_100",
    "PROFILE_SMALL_1K",
    "PROFILE_SUPPORTED_10K",
    "BenchmarkProfile",
    "CacheState",
    "EnvelopeBudget",
    "ProviderKind",
    "build_profile_result_skeleton",
    "dependency_lock_sha256",
    "empty_cache_state_block",
    "get_profile",
    "hardware_model_fingerprint",
    "profile_manifest_errors",
]
