#!/usr/bin/env python3
"""Run declared E6 benchmark profiles (S14 / evaluation.md).

Default ``--profile ci-smoke`` is CI-feasible: synthetic registry, hashing
or production provider, completeness + semantic equality. Dedicated-runner
budget enforcement requires ``--envelope-mode budget`` with
``--provider production`` on the reference hardware.

External/real corpora and hybrid paired comparisons that cannot run offline
are recorded as UNEVALUATED with operator commands — never fabricated PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from magicite.config import Config
from magicite.core import router as router_mod
from magicite.embeddings import get_embedder
from magicite.embeddings.cache import CachingEmbedder
from magicite.eval.envelopes import validate_envelope
from magicite.eval.profiles import (
    PROFILES,
    build_profile_result_skeleton,
    get_profile,
)
from magicite.eval.scale import latency_percentiles_ms, path_size_gib, process_rss_gib
from magicite.eval.unevaluated import unevaluated_catalog
from magicite.storage import db as db_mod
from magicite.storage import ephemeral as ephemeral_mod

# Legacy defaults retained for callers that still pass --sizes.
DEFAULT_SIZES = (1000, 10000)
DEFAULT_CALLS = 20
DEFAULT_WARMUP = 2
QUERY_TEMPLATES = (
    "rollback proton for a steam game after a bad update",
    "fix wine prefix permissions for a broken launcher",
    "install a verified skill for offline documentation generation",
    "compose a plan for dependency-ordered packaging",
    "abstain when no eligible skill matches the query",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("hashing", "production"),
        default="hashing",
        help="production uses the offline FastEmbed provider; never downloads models",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="ci-smoke",
        help="declared E6 / CI profile (default: ci-smoke)",
    )
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=None,
        help="legacy size list; overrides profile corpus_artifacts when set",
    )
    parser.add_argument("--calls", type=int, default=None, help="measured calls (overrides profile)")
    parser.add_argument("--warmup", type=int, default=None, help="warmup calls (overrides profile)")
    parser.add_argument(
        "--repetitions",
        type=int,
        default=None,
        help="clean-process repetitions (overrides profile)",
    )
    parser.add_argument(
        "--budget-ms",
        type=float,
        default=None,
        help="legacy single latency budget; ignored when profile budgets apply",
    )
    parser.add_argument(
        "--envelope-mode",
        choices=("completeness", "budget", "none"),
        default="completeness",
        help="completeness=shared CI; budget=dedicated runner; none=skip check",
    )
    parser.add_argument(
        "--opt-in-exploratory",
        action="store_true",
        help="required to run exploratory-50k",
    )
    parser.add_argument(
        "--environment-label",
        default=os.environ.get("MAGICITE_BENCHMARK_ENVIRONMENT", "local-workspace"),
        help="evidence label such as local-workspace or dedicated-linux-amd64-4c-16g",
    )
    parser.add_argument(
        "--project-root-for-lock",
        type=Path,
        default=None,
        help="path used to hash uv.lock for fingerprint (default: cwd)",
    )
    parser.add_argument(
        "--corpus-manifest",
        type=Path,
        default=None,
        help="optional real CorpusManifest/1; absence of real 10k stays UNEVALUATED",
    )
    parser.add_argument("--output", type=Path, help="write the JSON result to this path")
    args = parser.parse_args()
    if args.sizes is not None and any(size < 1 for size in args.sizes):
        parser.error("--sizes values must be positive")
    if args.calls is not None and args.calls < 2:
        parser.error("--calls must be at least 2 for a percentile")
    if args.warmup is not None and args.warmup < 1:
        parser.error("--warmup must be positive")
    return args


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _build_synthetic_registry(
    conn: sqlite3.Connection,
    *,
    model_name: str,
    dim: int,
    n: int,
    seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    now = _now()
    ids = [f"egr_{i:05d}" for i in range(n)]
    names = [f"synthetic-skill-{i:05d}" for i in range(n)]

    conn.executemany(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, storage_strength, s_decayed_at, excitability,
          identity_sha256, content_sha256, body_sha256, file_mtime_ns, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?, 0.0, ?, 0.05, ?,?,?, 0, ?, ?)
        """,
        [
            (
                engram_id,
                name,
                f"{name}.egr.md",
                "engram/0.2",
                1,
                "authored",
                "verified",
                "nascent",
                f"does {name}",
                f"use when {name}",
                now,
                engram_id,
                engram_id,
                engram_id,
                now,
                now,
            )
            for engram_id, name in zip(ids, names, strict=True)
        ],
    )

    cluster_count = min(20, n)
    centers = rng.normal(size=(cluster_count, dim)).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    clusters = rng.integers(0, cluster_count, size=n)
    noise = rng.normal(scale=0.3, size=(n, dim)).astype(np.float32)
    vectors = centers[clusters] + noise
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    for engram_id, vector in zip(ids, vectors, strict=True):
        ephemeral_mod.upsert_embedding(
            conn,
            engram_id=engram_id,
            model_name=model_name,
            dim=dim,
            vec=vector.astype(np.float32),
            source_sha256=engram_id,
        )

    conn.executemany(
        "INSERT INTO engram_community (engram_id, community_id, algo, computed_at) "
        "VALUES (?,?,?,?)",
        [
            (engram_id, int(clusters[index]), "label_propagation", now)
            for index, engram_id in enumerate(ids)
        ],
    )

    edge_types = ("composes", "depends_on", "co_activation", "similar_to")
    edge_rows: list[tuple[Any, ...]] = []
    for index, source_id in enumerate(ids):
        for target_raw in rng.choice(n, size=min(5, n), replace=False):
            target_index = int(target_raw)
            if target_index == index:
                continue
            edge_rows.append(
                (
                    source_id,
                    names[target_index],
                    ids[target_index],
                    edge_types[int(rng.integers(0, len(edge_types)))],
                    float(rng.uniform(0.1, 0.9)),
                    now,
                    now,
                )
            )
        if index % 97 == 0 and n > 1:
            target_index = int(rng.integers(0, n - 1))
            if target_index >= index:
                target_index += 1
            edge_rows.append(
                (
                    source_id,
                    names[target_index],
                    ids[target_index],
                    "inhibits",
                    float(rng.uniform(0.3, 0.9)),
                    now,
                    now,
                )
            )
    conn.executemany(
        """
        INSERT OR IGNORE INTO edge (
          src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
          evidence_count, provenance, first_observed, dangling
        ) VALUES (?,?,?,?,?,?, 3, 'derived', ?, 0)
        """,
        edge_rows,
    )
    conn.commit()


def _semantic_signature(outcome: router_mod.RouteOutcome) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "rank": candidate.rank,
                "id": candidate.id,
                "score": candidate.score,
                "diagnostics": candidate.diagnostics,
            }
            for candidate in outcome.candidates
        ],
        "composition_plan": outcome.composition_plan,
        "plan_confidence": outcome.plan_confidence,
        "registry_size": outcome.registry_size,
        "unresolved_context": outcome.unresolved_context,
    }


def _query_for_index(index: int) -> str:
    return QUERY_TEMPLATES[index % len(QUERY_TEMPLATES)] + f" [{index}]"


def _timed_route(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    query: str,
    session_id: str,
) -> tuple[float, router_mod.RouteOutcome]:
    started = time.perf_counter()
    outcome = router_mod.route(
        cfg,
        conn,
        embedder,
        query=query,
        k=5,
        session_id=session_id,
    )
    return time.perf_counter() - started, outcome


def _measure_profile(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    size: int,
    calls: int,
    warmup: int,
    db_path: Path,
) -> dict[str, Any]:
    session_id = f"benchmark-{size}"
    cache_states: dict[str, Any] = {
        "cold_process": {"identified": True, "measured": False, "latency_ms": None, "notes": ""},
        "cold_model": {"identified": True, "measured": False, "latency_ms": None, "notes": ""},
        "cold_index": {"identified": True, "measured": False, "latency_ms": None, "notes": ""},
        "warm_index": {"identified": True, "measured": False, "latency_ms": None, "notes": ""},
        "hot_query_cache": {"identified": True, "measured": False, "latency_ms": None, "notes": ""},
    }

    router_mod._cached_route_index.cache_clear()
    if isinstance(embedder, CachingEmbedder):
        embedder.clear()

    # Cold process / cold model / cold index: cleared caches, first route.
    cold_s, cold_outcome = _timed_route(
        cfg, conn, embedder, query=_query_for_index(0), session_id=session_id
    )
    cold_ms = round(cold_s * 1000.0, 3)
    cache_states["cold_process"] = {
        "identified": True,
        "measured": True,
        "latency_ms": cold_ms,
        "notes": "first route after process start in this measurement harness",
    }
    cache_states["cold_model"] = {
        "identified": True,
        "measured": True,
        "latency_ms": cold_ms,
        "notes": "embedder cache cleared; acquisition/download excluded",
    }
    cache_states["cold_index"] = {
        "identified": True,
        "measured": True,
        "latency_ms": cold_ms,
        "notes": "route index cache cleared",
    }

    router_mod._cached_route_index.cache_clear()
    _, uncached_outcome = _timed_route(
        cfg, conn, embedder, query=_query_for_index(0), session_id=session_id
    )
    _, cached_outcome = _timed_route(
        cfg, conn, embedder, query=_query_for_index(0), session_id=session_id
    )
    semantic_equal = _semantic_signature(uncached_outcome) == _semantic_signature(cached_outcome)
    if not semantic_equal:
        raise AssertionError("cached and uncached routing outcomes differ")

    # Distinct-query index-miss path.
    miss_durations: list[float] = []
    for index in range(calls):
        router_mod._cached_route_index.cache_clear()
        duration, _ = _timed_route(
            cfg, conn, embedder, query=_query_for_index(index), session_id=session_id
        )
        miss_durations.append(duration)

    # Warm index: warmup then measured warm routes (rotated queries).
    router_mod._cached_route_index.cache_clear()
    for index in range(warmup):
        _timed_route(cfg, conn, embedder, query=_query_for_index(index), session_id=session_id)
    warm_durations = [
        _timed_route(
            cfg, conn, embedder, query=_query_for_index(warmup + index), session_id=session_id
        )[0]
        for index in range(calls)
    ]
    warm = latency_percentiles_ms(warm_durations)
    cache_states["warm_index"] = {
        "identified": True,
        "measured": True,
        "latency_ms": warm["p95_ms"],
        "notes": f"after {warmup} warmups; distinct/rotated queries",
    }

    # Hot query cache: repeated identical query.
    hot_query = _query_for_index(0)
    for _ in range(max(1, warmup)):
        _timed_route(cfg, conn, embedder, query=hot_query, session_id=session_id)
    hot_durations = [
        _timed_route(cfg, conn, embedder, query=hot_query, session_id=session_id)[0]
        for _ in range(calls)
    ]
    hot = latency_percentiles_ms(hot_durations)
    cache_states["hot_query_cache"] = {
        "identified": True,
        "measured": True,
        "latency_ms": hot["p95_ms"],
        "notes": "repeated identical query after warmups",
    }

    miss = latency_percentiles_ms(miss_durations)
    rss = process_rss_gib()
    index_gib = path_size_gib(db_path)
    return {
        "size": size,
        "semantic_equality": semantic_equal,
        "cache_states": cache_states,
        "cold_ready_s": round(cold_s, 6),
        "warm_route": warm,
        "index_miss_route": miss,
        "hot_query_route": hot,
        "process_rss_gib": round(rss, 6),
        "index_gib": round(index_gib, 6),
        "index_build_s": None,  # synthetic preloaded vectors; build measured separately
        "index_build_peak_rss_gib": None,
        "payload_tokens": None,
        "cache_hit_rates": {
            "route_index": "warm_vs_miss_reported_separately",
            "query_embed_cache": "provider-dependent",
        },
        "truncations_fallbacks": [],
        "cold_top_candidate": cold_outcome.candidates[0].id if cold_outcome.candidates else None,
    }


def _flatten_measurements(raw: dict[str, Any]) -> dict[str, Any]:
    warm = raw.get("warm_route") or {}
    return {
        "warm_route_p50_ms": warm.get("p50_ms"),
        "warm_route_p95_ms": warm.get("p95_ms"),
        "warm_route_p99_ms": warm.get("p99_ms"),
        "process_rss_gib": raw.get("process_rss_gib"),
        "index_gib": raw.get("index_gib"),
        "cold_ready_s": raw.get("cold_ready_s"),
        "index_build_s": raw.get("index_build_s"),
        "index_build_peak_rss_gib": raw.get("index_build_peak_rss_gib"),
        "payload_tokens": raw.get("payload_tokens"),
        "cache_hit_rates": raw.get("cache_hit_rates"),
        "truncations_fallbacks": raw.get("truncations_fallbacks"),
        "index_miss_route": raw.get("index_miss_route"),
        "hot_query_route": raw.get("hot_query_route"),
        "semantic_equality": raw.get("semantic_equality"),
        "size": raw.get("size"),
    }


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    profile = get_profile(args.profile)
    if profile.opt_in and not args.opt_in_exploratory:
        raise SystemExit(
            f"profile {profile.profile_id!r} is opt-in; pass --opt-in-exploratory"
        )

    calls = args.calls if args.calls is not None else max(2, min(profile.measured_queries_min, 32))
    # CI smoke keeps calls small; full measured_queries_min is for dedicated runners.
    if args.profile == "ci-smoke" and args.calls is None:
        calls = 8
    warmup = args.warmup if args.warmup is not None else min(profile.warmup_queries, 8)
    if args.profile != "ci-smoke" and args.warmup is None and args.calls is None:
        # Dedicated-style: honour profile mins when explicitly requested via profile
        # other than ci-smoke *and* operator raised calls; default stays bounded.
        warmup = min(profile.warmup_queries, 50)
        calls = max(calls, 20)

    sizes = args.sizes if args.sizes is not None else [profile.corpus_artifacts]
    provider_name = "fastembed" if args.provider == "production" else "hashing"
    project_root_for_lock = args.project_root_for_lock or Path.cwd()

    result = build_profile_result_skeleton(
        profile,
        provider=args.provider,  # type: ignore[arg-type]
        model_name="pending",
        model_digest=None,
        runner_label=args.environment_label,
        project_root=project_root_for_lock,
    )
    result["recorded_at"] = _now()
    result["provider_requested"] = args.provider
    result["provider"] = provider_name
    result["parameters"] = {
        "sizes": sizes,
        "calls": calls,
        "warmup": warmup,
        "repetitions": args.repetitions if args.repetitions is not None else profile.repetitions,
        "envelope_mode": args.envelope_mode,
        "corpus_manifest": str(args.corpus_manifest) if args.corpus_manifest else None,
    }
    result["legacy_measurements"] = []
    result["unevaluated"] = unevaluated_catalog()

    # Real licensed corpus absence stays UNEVALUATED (do not invent PASS).
    if args.corpus_manifest is None and profile.budget.ga_support_claim:
        # already in catalog; ensure status remains unevaluated for GA claim
        result["ga_support_claim_status"] = "UNEVALUATED"
        result["ga_support_claim_reason"] = (
            "synthetic-only run; real licensed 10k corpus required for GA support claim"
        )
    elif args.corpus_manifest is not None and not args.corpus_manifest.is_file():
        raise FileNotFoundError(f"corpus manifest not found: {args.corpus_manifest}")

    try:
        with tempfile.TemporaryDirectory(prefix="magicite-benchmark-") as temp_dir:
            project_root = Path(temp_dir)
            cfg = Config(project_root=project_root)
            cfg.embedding_provider = provider_name
            cfg.embedding_offline = True
            cfg.ensure_dirs()
            embedder = get_embedder(cfg)
            result["fingerprint"]["model_name"] = embedder.model_name
            model_digest = getattr(embedder, "model_digest", None) or getattr(
                embedder, "artifact_digest", None
            )
            result["fingerprint"]["model_digest"] = (
                str(model_digest) if model_digest else f"provider:{provider_name}:{embedder.model_name}"
            )

            size = sizes[0]
            db_path = project_root / f"benchmark-{size}.db"
            build_started = time.perf_counter()
            rss_before = process_rss_gib()
            conn = db_mod.connect(db_path)
            try:
                _build_synthetic_registry(
                    conn,
                    model_name=embedder.model_name,
                    dim=embedder.dim,
                    n=size,
                    seed=1234,
                )
                build_s = time.perf_counter() - build_started
                raw = _measure_profile(
                    cfg,
                    conn,
                    embedder,
                    size=size,
                    calls=calls,
                    warmup=warmup,
                    db_path=db_path,
                )
                raw["index_build_s"] = round(build_s, 6)
                raw["index_build_peak_rss_gib"] = round(
                    max(process_rss_gib(), rss_before), 6
                )
                result["cache_states"] = raw["cache_states"]
                result["measurements"] = _flatten_measurements(raw)
                result["measurements"]["index_build_s"] = raw["index_build_s"]
                result["measurements"]["index_build_peak_rss_gib"] = raw[
                    "index_build_peak_rss_gib"
                ]
                result["legacy_measurements"].append(raw)
            finally:
                conn.close()

        result["status"] = "measured"
        if args.envelope_mode != "none":
            check = validate_envelope(result, profile, mode=args.envelope_mode)  # type: ignore[arg-type]
            result["envelope_check"] = check.to_dict()
            if not check.ok and args.envelope_mode == "completeness":
                result["status"] = "incomplete"
                return result, 2
            if not check.ok and args.envelope_mode == "budget":
                result["status"] = "budget_failed"
                # Do not flip default policy; report failure only.
                return result, 3
        return result, 0
    except Exception as exc:
        result["status"] = "unavailable"
        result["error"] = f"{type(exc).__name__}: {exc}"
        # Ensure cache_states remain identified even on failure.
        return result, 2


def main() -> int:
    args = _parse_args()
    result, exit_code = _run(args)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
