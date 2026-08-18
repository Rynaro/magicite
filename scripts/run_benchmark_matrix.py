#!/usr/bin/env python3
"""Run the v0.3 cold/index-miss/index-hit route benchmark matrix.

The runner is deliberately executable both from a source checkout and inside
the production image.  ``--provider production`` selects Magicite's default
offline FastEmbed provider; it never downloads a missing model.  The synthetic
registry vectors are generated deterministically so the measured provider work
is the query embedding plus the real routing pipeline, not corpus ingestion.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
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
from magicite.storage import db as db_mod
from magicite.storage import ephemeral as ephemeral_mod

DEFAULT_SIZES = (1000, 10000)
DEFAULT_CALLS = 20
DEFAULT_WARMUP = 2
DEFAULT_BUDGET_MS = 100.0
QUERY = "rollback proton for a steam game after a bad update"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("hashing", "production"),
        default="hashing",
        help="production uses the offline FastEmbed provider configured by Magicite",
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=list(DEFAULT_SIZES))
    parser.add_argument("--calls", type=int, default=DEFAULT_CALLS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--budget-ms", type=float, default=DEFAULT_BUDGET_MS)
    parser.add_argument(
        "--environment-label",
        default=os.environ.get("MAGICITE_BENCHMARK_ENVIRONMENT", "local-workspace"),
        help="evidence label such as local-workspace or production-container",
    )
    parser.add_argument("--output", type=Path, help="write the JSON result to this path")
    args = parser.parse_args()
    if any(size < 1 for size in args.sizes):
        parser.error("--sizes values must be positive")
    if args.calls < 2:
        parser.error("--calls must be at least 2 for a percentile")
    if args.warmup < 1:
        parser.error("--warmup must be positive")
    if args.budget_ms <= 0:
        parser.error("--budget-ms must be positive")
    return args


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _inside_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("container") is not None


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
    """Exclude Tier-C counters/session identity while checking route equality."""
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


def _percentiles(durations_s: list[float]) -> dict[str, float]:
    durations_ms = np.asarray(durations_s, dtype=np.float64) * 1000.0
    return {
        "calls": int(durations_ms.size),
        "p50_ms": round(float(np.percentile(durations_ms, 50)), 3),
        "p95_ms": round(float(np.percentile(durations_ms, 95)), 3),
        "max_ms": round(float(np.max(durations_ms)), 3),
    }


def _timed_route(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    session_id: str,
) -> tuple[float, router_mod.RouteOutcome]:
    started = time.perf_counter()
    outcome = router_mod.route(
        cfg,
        conn,
        embedder,
        query=QUERY,
        k=5,
        session_id=session_id,
    )
    return time.perf_counter() - started, outcome


def _budget_result(name: str, measured_ms: float, limit_ms: float) -> dict[str, Any]:
    return {
        "name": name,
        "limit_ms": limit_ms,
        "measured_ms": measured_ms,
        "passed": measured_ms < limit_ms,
    }


def _budget_name(route_class: str, size: int, limit_ms: float) -> str:
    rendered_limit = f"{limit_ms:g}ms"
    return f"route-{route_class}-{size}-{rendered_limit}"


def _measure_size(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    size: int,
    calls: int,
    warmup: int,
    budget_ms: float,
) -> dict[str, Any]:
    session_id = f"benchmark-{size}"
    router_mod._cached_route_index.cache_clear()
    if isinstance(embedder, CachingEmbedder):
        embedder.clear()

    cold_s, cold_outcome = _timed_route(cfg, conn, embedder, session_id=session_id)

    router_mod._cached_route_index.cache_clear()
    _, uncached_outcome = _timed_route(cfg, conn, embedder, session_id=session_id)
    _, cached_outcome = _timed_route(cfg, conn, embedder, session_id=session_id)
    semantic_equal = _semantic_signature(uncached_outcome) == _semantic_signature(cached_outcome)
    if not semantic_equal:
        raise AssertionError("cached and uncached routing outcomes differ")

    miss_durations: list[float] = []
    for _ in range(calls):
        router_mod._cached_route_index.cache_clear()
        duration, _ = _timed_route(cfg, conn, embedder, session_id=session_id)
        miss_durations.append(duration)

    router_mod._cached_route_index.cache_clear()
    for _ in range(warmup):
        _timed_route(cfg, conn, embedder, session_id=session_id)
    hit_durations = [
        _timed_route(cfg, conn, embedder, session_id=session_id)[0] for _ in range(calls)
    ]

    cold_ms = round(cold_s * 1000.0, 3)
    miss = _percentiles(miss_durations)
    hit = _percentiles(hit_durations)
    return {
        "size": size,
        "semantic_equality": semantic_equal,
        "cold": {
            "latency_ms": cold_ms,
            "budget": _budget_result(
                _budget_name("cold", size, budget_ms), cold_ms, budget_ms
            ),
        },
        "miss": {
            **miss,
            "budget": _budget_result(
                _budget_name("index-miss-p95", size, budget_ms),
                miss["p95_ms"],
                budget_ms,
            ),
        },
        "hit": {
            **hit,
            "budget": _budget_result(
                _budget_name("index-hit-p95", size, budget_ms),
                hit["p95_ms"],
                budget_ms,
            ),
        },
        "cold_top_candidate": cold_outcome.candidates[0].id
        if cold_outcome.candidates
        else None,
    }


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    provider_name = "fastembed" if args.provider == "production" else "hashing"
    result: dict[str, Any] = {
        "schema": "magicite-benchmark-matrix/1",
        "recorded_at": _now(),
        "status": "running",
        "provider_requested": args.provider,
        "provider": provider_name,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "label": args.environment_label,
            "runtime_is_container": _inside_container(),
            "container_image_digest": os.environ.get("MAGICITE_CONTAINER_IMAGE_DIGEST"),
        },
        "parameters": {
            "sizes": args.sizes,
            "calls": args.calls,
            "warmup": args.warmup,
            "budget_ms": args.budget_ms,
        },
        "measurements": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="magicite-benchmark-") as temp_dir:
            project_root = Path(temp_dir)
            cfg = Config(project_root=project_root)
            cfg.embedding_provider = provider_name
            cfg.embedding_offline = True
            cfg.ensure_dirs()
            embedder = get_embedder(cfg)
            for index, size in enumerate(args.sizes):
                db_path = project_root / f"benchmark-{size}.db"
                conn = db_mod.connect(db_path)
                try:
                    _build_synthetic_registry(
                        conn,
                        model_name=embedder.model_name,
                        dim=embedder.dim,
                        n=size,
                        seed=1234 + index,
                    )
                    result["measurements"].append(
                        _measure_size(
                            cfg,
                            conn,
                            embedder,
                            size=size,
                            calls=args.calls,
                            warmup=args.warmup,
                            budget_ms=args.budget_ms,
                        )
                    )
                finally:
                    conn.close()
        result["status"] = "measured"
        return result, 0
    except Exception as exc:
        result["status"] = "unavailable"
        result["error"] = f"{type(exc).__name__}: {exc}"
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
