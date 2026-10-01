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
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import router as router_mod
from magicite.core.index_generation import TOKENIZER_ID, tokenize_words
from magicite.embeddings import get_embedder
from magicite.embeddings.cache import CachingEmbedder
from magicite.eval.envelopes import compute_ga_eligibility, validate_envelope
from magicite.eval.profiles import (
    PROFILES,
    build_profile_result_skeleton,
    get_profile,
)
from magicite.eval.query_context import corpus_route_context
from magicite.eval.scale import latency_percentiles_ms, path_size_gib, process_rss_gib
from magicite.eval.unevaluated import unevaluated_catalog
from magicite.mcp.bind_retrieval import project_route_output
from magicite.storage import db as db_mod

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
    parser.add_argument("--single-process", action="store_true", help=argparse.SUPPRESS)
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
    parser.add_argument(
        "--support-run",
        type=Path,
        action="append",
        default=[],
        help="prior measured E6 corpus-stratum run for the combined support gate",
    )
    parser.add_argument(
        "--custody",
        choices=("protected", "disposable-simulated"),
        default="protected",
        help=(
            "protected requires enrolled custody and fails closed for the disposable "
            "benchmark registry; disposable-simulated uses an in-process evaluation "
            "custodian, is never GA-eligible and leaves deployment custody UNEVALUATED"
        ),
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


class _DisposableCustody:
    def __init__(self, store: Any, registry_id: str) -> None:
        self.store, self.registry_id = store, registry_id

    def call(self, operation: str, **arguments: Any) -> Any:
        return getattr(self.store, operation)(self.registry_id, **arguments)


def _attach_custody(project_root: Path, directory: Path, registry_id: str) -> None:
    """Route custody resolution for this disposable registry only to ``directory``."""
    from magicite.core import writer_guard
    from magicite.core.trust_custodian import CustodianStore

    provider = _DisposableCustody(CustodianStore.open(directory), registry_id)
    original = writer_guard.resolve_custody
    root = project_root.resolve()

    def resolve(candidate: Config) -> tuple[str, Any]:
        if candidate.project_root.resolve() == root:
            return registry_id, provider
        return original(candidate)

    writer_guard.resolve_custody = resolve  # type: ignore[assignment]


def _enroll_disposable_custody(cfg: Config, directory: Path) -> str:
    """Create an evaluation-only custodian for the disposable benchmark registry."""
    from magicite.core import trust
    from magicite.core.trust_custodian import CustodianStore
    from magicite.core.trust_journal import TrustJournal

    registry_id = "benchmark-" + hashlib.sha256(str(cfg.project_root.resolve()).encode()).hexdigest()[:24]
    store = CustodianStore.create(directory)
    try:
        store.enroll(registry_id, trust.default_policy().to_dict(), actor="benchmark-operator", reviewed=True)
    finally:
        store.close()
    _attach_custody(cfg.project_root, directory, registry_id)
    from magicite.core import writer_guard

    _, provider = writer_guard.resolve_custody(cfg)
    TrustJournal(cfg.data_dir / "trust/authority", registry_id, provider).initialize_reviewed_genesis()
    return registry_id


def _build_synthetic_registry(
    conn: sqlite3.Connection,
    *,
    model_name: str,
    dim: int,
    n: int,
    seed: int,
    cfg: Config,
    embedder: Any,
) -> None:
    """Generate actual admissible full bodies and build the production index path."""
    from magicite.core import registry

    for index in range(n):
        topic = QUERY_TEMPLATES[(index + seed) % len(QUERY_TEMPLATES)]
        name = f"synthetic-skill-{index:05d}"
        frontmatter = {
            "spec": "engram/0.2",
            "name": name,
            "id": f"egr_{index:08x}",
            "version": 1,
            "provenance": "authored",
            "intent": {
                "does": f"{topic}: task {index}",
                "use_when": topic,
                "not_when": "the request concerns a different task",
            },
            "triggers": {
                "positive": [topic, f"task {index}", f"procedure {index}"],
                "negative": ["unrelated task"],
            },
        }
        body = f"## Procedure\n1. Inspect task {index}.\n2. Complete {topic}.\n"
        (cfg.registry_dir / f"{name}.egr.md").write_text("---\n" + json.dumps(frontmatter) + "\n---\n" + body)
    outcome = registry.register(cfg, conn, embedder, path=str(cfg.registry_dir))
    if outcome.validation_errors or outcome.ingested != n:
        raise ValueError("synthetic artifacts failed production registration")
    # Registration grants nothing; explicit review binds exactly the bytes
    # generated above, so measured routes select rather than refuse.
    _review_all(cfg, conn, reason="isolated synthetic evaluation registry; no runtime trust transfer")


def _review_all(cfg: Config, conn: sqlite3.Connection, *, reason: str) -> None:
    from magicite.core import registry, writer_guard

    # One writer lease spans the batch; each review is still its own decision.
    with writer_guard.registry_writer_lease(cfg, conn).acquire():
        for row in conn.execute("SELECT id, content_sha256 FROM engram").fetchall():
            registry.review_approve(
                cfg,
                conn,
                engram_id=row["id"],
                expected_digest=row["content_sha256"],
                actor="benchmark-explicit-evaluation-review",
                reason=reason,
            )


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


def _query_for_index(index: int, queries: list[Any] | None = None) -> Any:
    if queries:
        return queries[index % len(queries)]
    return QUERY_TEMPLATES[index % len(QUERY_TEMPLATES)] + f" [{index}]"


def _build_registry_from_corpus_manifest(
    conn: sqlite3.Connection,
    *,
    corpus_manifest_path: Path,
    model_name: str,
    dim: int,
    embedder: Any,
    cfg: Config,
) -> tuple[dict[str, Any], list[Any]]:
    """Load digest-bound full artifact inventory through registry/trust domains.

    ArtifactRef role=skill denotes an .egr.md body; other refs can supply
    its relative resources. Evaluation admission is explicit and isolated.
    Labels alone cannot be used to synthesize a supposedly licensed corpus.
    """
    from magicite.core import registry
    from magicite.engram import parser
    from magicite.eval.external import verify_acquired_corpus_manifest
    from magicite.eval.runner import candidates_for_corpus

    corpus, errors = verify_acquired_corpus_manifest(corpus_manifest_path)
    if errors or corpus is None:
        raise ValueError("invalid --corpus-manifest: " + "; ".join(errors or ["unknown"]))
    skill_refs = [ref for ref in corpus.artifacts if ref.role == "skill"]
    if not skill_refs:
        raise ValueError("corpus requires digest-bound ArtifactRef role=skill bodies")
    root = corpus_manifest_path.parent.resolve()
    staging = cfg.project_root / "corpus-input"
    inventory = []
    paths = set()
    for ref in corpus.artifacts:
        relative = Path(ref.path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in ref.path:
            raise ValueError("unsafe artifact path")
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or str(relative) in paths:
            raise ValueError("escaping or duplicate artifact path")
        paths.add(str(relative))
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != ref.sha256 or (ref.byte_length is not None and len(content) != ref.byte_length):
            raise ValueError("artifact digest or byte length mismatch")
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if ref.role != "skill":
            runtime_target = cfg.registry_dir / relative
            if runtime_target.exists():
                raise ValueError("corpus resource collides with runtime state")
            runtime_target.parent.mkdir(parents=True, exist_ok=True)
            runtime_target.write_bytes(content)
        inventory.append({"path": ref.path, "sha256": digest, "byte_length": len(content), "role": ref.role})
    ids = set()
    names = set()
    for ref in skill_refs:
        artifact, _ = parser.load_artifact_file(staging / ref.path, registry_root=staging)
        if artifact.id in ids or artifact.name in names:
            raise ValueError("duplicate artifact identity")
        ids.add(artifact.id)
        names.add(artifact.name)
    if set(candidates_for_corpus(corpus)) - ids - names:
        raise ValueError("corpus label does not resolve to an artifact identity")
    # Domain intake assigns external/pending origin; explicit local review binds
    # these exact bytes for this disposable evaluation registry only.
    for ref in skill_refs:
        outcome = registry.register(
            cfg, conn, embedder, path=str((staging / ref.path).relative_to(cfg.project_root))
        )
        if outcome.validation_errors:
            raise ValueError("corpus artifact failed registry admission")
    _review_all(cfg, conn, reason="isolated digest-bound corpus evaluation; no runtime trust transfer")
    queries = [
        {"query_text": q.query_text, "compatibility_context": q.compatibility_context}
        for q in corpus.queries
        if q.query_text.strip()
    ]
    if not queries:
        raise ValueError("corpus has no queries")
    actual_count = conn.execute("SELECT COUNT(*) FROM engram").fetchone()[0]
    if actual_count != len(ids):
        raise ValueError("registered artifact count differs from declared inventory")
    return {
        "kind": "manifest",
        "path": str(corpus_manifest_path.resolve()),
        "content_identity_sha256": corpus.content_identity_sha256,
        "manifest_sha256": hashlib.sha256(corpus_manifest_path.read_bytes()).hexdigest(),
        "artifact_inventory_sha256": hashlib.sha256(
            json.dumps(
                sorted(inventory, key=lambda x: x["path"]), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "artifact_bytes": sum(item["byte_length"] for item in inventory),
        "actual_artifacts": True,
        "admission_policy": "explicit-isolated-evaluation-review",
        "corpus_id": corpus.corpus_id,
        "dataset_revision": corpus.dataset_revision,
        "license": corpus.license,
        "n_candidates": len(ids),
        "n_queries": len(queries),
        "ga_eligible": False,
    }, queries


def _timed_route(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    query: Any,
    session_id: str,
) -> tuple[float, router_mod.RouteOutcome]:
    started = time.perf_counter()
    text = query["query_text"] if isinstance(query, dict) else query
    context = (
        corpus_route_context(query.get("compatibility_context", {})) if isinstance(query, dict) else None
    )
    outcome = router_mod.route(
        cfg,
        conn,
        embedder,
        query=text,
        route_context=context,
        k=5,
        session_id=session_id,
    )
    project_route_output(outcome).model_dump_json()
    return time.perf_counter() - started, outcome


def _cold_process_ready(
    cfg: Config, db_path: Path, query: Any, provider: str, custody: list[str] | None = None
) -> float:
    """Wall time for a new serving process through first serialized route."""
    program = """
import sys, json
from pathlib import Path
from magicite.config import Config
from magicite.embeddings import get_embedder
from magicite.storage import db
from magicite.core import router
from magicite.mcp.bind_retrieval import project_route_output
from magicite.eval.query_context import corpus_route_context
root, database, query, provider, custody, script = json.loads(sys.argv[1])
if custody is not None:
    import importlib.util
    spec = importlib.util.spec_from_file_location('benchmark_matrix', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._attach_custody(Path(root), Path(custody[0]), custody[1])
cfg = Config(project_root=Path(root))
cfg.embedding_provider = provider
cfg.embedding_offline = True
conn = db.connect(Path(database))
try:
    text = query['query_text'] if isinstance(query, dict) else query
    context = None
    if isinstance(query, dict):
        context = corpus_route_context(query.get('compatibility_context', {}))
    outcome = router.route(cfg, conn, get_embedder(cfg), query=text, route_context=context,
                           k=5, session_id='cold-ready')
    project_route_output(outcome).model_dump_json()
finally:
    conn.close()
"""
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            json.dumps(
                [str(cfg.project_root), str(db_path), query, provider, custody, str(Path(__file__).resolve())]
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return time.perf_counter() - started


def _measure_profile(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    size: int,
    calls: int,
    warmup: int,
    db_path: Path,
    queries: list[Any] | None = None,
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
        cfg, conn, embedder, query=_query_for_index(0, queries), session_id=session_id
    )
    cold_ms = round(cold_s * 1000.0, 3)
    cache_states["cold_process"] = {
        "identified": True,
        "measured": False,
        "latency_ms": cold_ms,
        "notes": "first route after process start in this measurement harness",
    }
    cache_states["cold_model"] = {
        "identified": True,
        "measured": False,
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
        cfg, conn, embedder, query=_query_for_index(0, queries), session_id=session_id
    )
    _, cached_outcome = _timed_route(
        cfg, conn, embedder, query=_query_for_index(0, queries), session_id=session_id
    )
    semantic_equal = _semantic_signature(uncached_outcome) == _semantic_signature(cached_outcome)
    if not semantic_equal:
        raise AssertionError("cached and uncached routing outcomes differ")

    # Distinct-query index-miss path.
    miss_durations: list[float] = []
    for index in range(calls):
        router_mod._cached_route_index.cache_clear()
        duration, _ = _timed_route(
            cfg, conn, embedder, query=_query_for_index(index, queries), session_id=session_id
        )
        miss_durations.append(duration)

    # Warm index: warmup then measured warm routes (rotated queries).
    router_mod._cached_route_index.cache_clear()
    for index in range(warmup):
        _timed_route(cfg, conn, embedder, query=_query_for_index(index, queries), session_id=session_id)
    warm_durations = []
    warm_selected_routes = 0
    truncations_fallbacks = []
    payload_tokens = []
    for index in range(calls):
        duration, outcome = _timed_route(
            cfg, conn, embedder, query=_query_for_index(warmup + index, queries), session_id=session_id
        )
        warm_durations.append(duration)
        warm_selected_routes += bool(outcome.candidates)
        payload_tokens.append(len(tokenize_words(project_route_output(outcome).model_dump_json())))
        decision = outcome.decision
        if decision is not None and (decision.truncations or decision.fallback_identity):
            truncations_fallbacks.append(
                {
                    "query_index": index,
                    "truncations": dict(decision.truncations),
                    "fallback_identity": decision.fallback_identity,
                }
            )
    warm = latency_percentiles_ms(warm_durations)
    cache_states["warm_index"] = {
        "identified": True,
        "measured": True,
        "latency_ms": warm["p95_ms"],
        "notes": f"after {warmup} warmups; distinct/rotated queries",
    }

    # Hot query cache: repeated identical query.
    hot_query = _query_for_index(0, queries)
    for _ in range(max(1, warmup)):
        _timed_route(cfg, conn, embedder, query=hot_query, session_id=session_id)
    hot_durations = [
        _timed_route(cfg, conn, embedder, query=hot_query, session_id=session_id)[0] for _ in range(calls)
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
        "payload_tokens": max(payload_tokens, default=0),
        "warm_durations_s": warm_durations,
        "warm_selected_routes": warm_selected_routes,
        "cache_hit_rates": {
            "route_index": router_mod._cached_route_index.cache_info().hits
            / max(
                1,
                router_mod._cached_route_index.cache_info().hits
                + router_mod._cached_route_index.cache_info().misses,
            ),
            "query_embed_cache": embedder.hits / max(1, embedder.hits + embedder.misses)
            if isinstance(embedder, CachingEmbedder)
            else 0.0,
        },
        "truncations_fallbacks": truncations_fallbacks,
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
        "warm_durations_s": raw.get("warm_durations_s"),
    }


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    profile = get_profile(args.profile)
    if profile.opt_in and not args.opt_in_exploratory:
        raise SystemExit(f"profile {profile.profile_id!r} is opt-in; pass --opt-in-exploratory")

    calls = args.calls if args.calls is not None else profile.measured_queries_min
    # CI smoke keeps calls small; full measured_queries_min is for dedicated runners.
    if args.profile == "ci-smoke" and args.calls is None:
        calls = 8
    warmup = args.warmup if args.warmup is not None else profile.warmup_queries
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

    # Corpus provenance: synthetic runs are never GA-eligible.
    if args.corpus_manifest is None:
        result["corpus"] = {
            "kind": "synthetic",
            "ga_eligible": False,
            "ga_ineligible_reasons": ["corpus.kind=synthetic"],
            "path": None,
            "content_identity_sha256": None,
            "license": None,
            "n_candidates": None,
        }
        if profile.budget.ga_support_claim:
            result["ga_support_claim_status"] = "UNEVALUATED"
            result["ga_support_claim_reason"] = (
                "synthetic-only run (corpus.kind=synthetic); real licensed 10k "
                "corpus required for GA support claim"
            )
    else:
        if not args.corpus_manifest.is_file():
            raise FileNotFoundError(f"corpus manifest not found: {args.corpus_manifest}")
        result["corpus"] = {
            "kind": "manifest",
            "ga_eligible": False,
            "ga_ineligible_reasons": ["pending_measurement"],
            "path": str(args.corpus_manifest.resolve()),
            "content_identity_sha256": None,  # filled after load
            "license": None,
            "n_candidates": None,
        }

    result["custody"] = {"mode": args.custody, "deployment_qualification": "UNEVALUATED"}
    try:
        with (
            tempfile.TemporaryDirectory(prefix="magicite-benchmark-") as temp_dir,
            tempfile.TemporaryDirectory(prefix="magicite-benchmark-custody-") as custody_parent,
        ):
            project_root = Path(temp_dir).resolve()
            cfg = Config(project_root=project_root)
            cfg.embedding_provider = provider_name
            cfg.embedding_offline = True
            cfg.ensure_dirs()
            custody: list[str] | None = None
            if args.custody == "disposable-simulated":
                custody_dir = Path(custody_parent).resolve() / "private"
                custody = [str(custody_dir), _enroll_disposable_custody(cfg, custody_dir)]
            embedder = get_embedder(cfg)
            result["fingerprint"]["model_name"] = embedder.model_name
            model_digest = getattr(embedder, "model_digest", None) or getattr(
                embedder, "artifact_digest", None
            )
            result["fingerprint"]["model_digest"] = str(model_digest) if model_digest else "unavailable"

            size = sizes[0]
            db_path = project_root / f"benchmark-{size}.db"
            build_started = time.perf_counter()
            rss_before = process_rss_gib()
            conn = db_mod.connect(db_path)
            measure_queries: list[Any] | None = None
            try:
                if args.corpus_manifest is not None:
                    corpus_meta, measure_queries = _build_registry_from_corpus_manifest(
                        conn,
                        corpus_manifest_path=args.corpus_manifest,
                        model_name=embedder.model_name,
                        dim=embedder.dim,
                        embedder=embedder,
                        cfg=cfg,
                    )
                    result["corpus"].update(corpus_meta)
                    size = int(corpus_meta["n_candidates"])
                else:
                    _build_synthetic_registry(
                        conn,
                        model_name=embedder.model_name,
                        dim=embedder.dim,
                        n=size,
                        seed=1234,
                        cfg=cfg,
                        embedder=embedder,
                    )
                    rows = conn.execute("SELECT id, content_sha256 FROM engram ORDER BY id").fetchall()
                    result["corpus"]["n_candidates"] = len(rows)
                    result["corpus"]["actual_artifacts"] = True
                    result["corpus"]["artifact_inventory_sha256"] = hashlib.sha256(
                        json.dumps([tuple(row) for row in rows], separators=(",", ":")).encode()
                    ).hexdigest()
                    result["corpus"]["artifact_bytes"] = sum(
                        path.stat().st_size for path in cfg.registry_dir.rglob("*") if path.is_file()
                    )
                build_s = time.perf_counter() - build_started
                # Index construction may load the model; construct a fresh
                # provider so cold-ready includes model initialization on route.
                embedder = get_embedder(cfg)
                raw = _measure_profile(
                    cfg,
                    conn,
                    embedder,
                    size=size,
                    calls=calls,
                    warmup=warmup,
                    db_path=db_path,
                    queries=measure_queries,
                )
                conn.commit()
                cold_ready = _cold_process_ready(
                    cfg, db_path, _query_for_index(0, measure_queries), provider_name, custody
                )
                raw["cold_ready_s"] = cold_ready
                for cold_state in ("cold_process", "cold_model"):
                    raw["cache_states"][cold_state].update(
                        measured=True,
                        latency_ms=1000 * raw["cold_ready_s"],
                        notes="fresh serving subprocess through first serialized route; acquisition excluded",
                    )
                raw["index_build_s"] = round(build_s, 6)
                raw["index_build_peak_rss_gib"] = round(max(process_rss_gib(), rss_before), 6)
                inner = getattr(embedder, "_inner", embedder)
                runtime = getattr(inner, "_model", None)
                model = getattr(runtime, "model", None)
                model_dir = getattr(model, "_model_dir", None)
                if model_dir is not None:
                    model_root = Path(model_dir)
                    hashes = [
                        (str(path.relative_to(model_root)), hashlib.sha256(path.read_bytes()).hexdigest())
                        for path in sorted(model_root.rglob("*"))
                        if path.is_file()
                    ]
                    if hashes:
                        result["fingerprint"]["model_digest"] = hashlib.sha256(
                            json.dumps(hashes, separators=(",", ":")).encode()
                        ).hexdigest()
                result["fingerprint"]["embedding_dimensions"] = embedder.dim
                result["fingerprint"].update(
                    payload_tokenizer=TOKENIZER_ID,
                    payload_unit="lexical-word-tokens",
                    payload_surface="RouteOutput/1 JSON",
                    payload_aggregation="max-measured-responses",
                )
                result["cache_states"] = raw["cache_states"]
                result["measurements"] = _flatten_measurements(raw)
                result["measurements"]["artifact_body_bytes"] = result["corpus"]["artifact_bytes"]
                result["measurements"]["index_build_s"] = raw["index_build_s"]
                result["measurements"]["index_build_peak_rss_gib"] = raw["index_build_peak_rss_gib"]
                result["legacy_measurements"].append(raw)
            finally:
                conn.close()

        result["status"] = "measured"
        result["process_id"] = os.getpid()
        result["measured_queries"] = calls
        result["warmup_queries"] = warmup
        result["repetition_results"] = [
            {
                "process_id": os.getpid(),
                "measured_queries": calls,
                "warmup_queries": warmup,
                "status": "measured",
                "measurements": result["measurements"],
            }
        ]
        envelope_budget_ok: bool | None = None
        if args.envelope_mode != "none":
            check = validate_envelope(result, profile, mode=args.envelope_mode)  # type: ignore[arg-type]
            result["envelope_check"] = check.to_dict()
            if args.envelope_mode == "budget":
                envelope_budget_ok = check.ok
            if not check.ok and args.envelope_mode == "completeness":
                result["status"] = "incomplete"
                _apply_ga_eligibility(result, profile, args, envelope_budget_ok)
                return result, 2
            if not check.ok and args.envelope_mode == "budget":
                result["status"] = "budget_failed"
                _apply_ga_eligibility(result, profile, args, envelope_budget_ok)
                return result, 3
        _apply_ga_eligibility(result, profile, args, envelope_budget_ok)
        return result, 0
    except Exception as exc:
        result["status"] = "unavailable"
        result["error"] = f"{type(exc).__name__}: {exc}"
        # Ensure cache_states remain identified even on failure.
        _apply_ga_eligibility(result, profile, args, None)
        return result, 2


def _apply_ga_eligibility(
    result: dict[str, Any],
    profile: Any,
    args: argparse.Namespace,
    envelope_budget_ok: bool | None,
) -> None:
    corpus = result.get("corpus") or {}
    eligible, reasons = compute_ga_eligibility(
        provider=str(args.provider),
        profile_ga_support_claim=bool(profile.budget.ga_support_claim),
        corpus_kind=str(corpus.get("kind") or "synthetic"),
        envelope_mode=str(args.envelope_mode),
        envelope_budget_ok=envelope_budget_ok,
        corpus_path=corpus.get("path"),
        corpus_license=corpus.get("license"),
        n_candidates=corpus.get("n_candidates"),
        profile_corpus_artifacts=int(profile.corpus_artifacts),
        measured_result=result,
        support_runs=[json.loads(path.read_text()) for path in getattr(args, "support_run", [])],
    )
    if getattr(args, "custody", "protected") != "protected":
        eligible = False
        reasons = [*reasons, f"custody={args.custody!r} (deployment custody UNEVALUATED)"]
    corpus["ga_eligible"] = eligible
    corpus["ga_ineligible_reasons"] = reasons
    result["corpus"] = corpus
    if not eligible and profile.budget.ga_support_claim:
        result["ga_support_claim_status"] = "UNEVALUATED"
        result["ga_support_claim_reason"] = "; ".join(reasons) if reasons else "ineligible"


def _run_repetitions(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    profile = get_profile(args.profile)
    count = args.repetitions if args.repetitions is not None else profile.repetitions
    if count < 1:
        raise ValueError("repetitions must be positive")
    if args.single_process or count == 1:
        return _run(args)
    runs = []
    with tempfile.TemporaryDirectory(prefix="magicite-repetitions-") as directory:
        for index in range(count):
            output = Path(directory) / f"run-{index}.json"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                *sys.argv[1:],
                "--single-process",
                "--repetitions",
                "1",
                "--envelope-mode",
                "none",
                "--output",
                str(output),
            ]
            child = subprocess.run(command, capture_output=True, text=True, check=False)
            if not output.is_file():
                raise RuntimeError("benchmark child did not produce evidence")
            run = json.loads(output.read_text())
            if child.returncode != 0:
                return run, child.returncode
            runs.append(run)
    result = runs[0]
    result["repetition_results"] = [
        {
            "process_id": run["process_id"],
            "measured_queries": run["measured_queries"],
            "warmup_queries": run["warmup_queries"],
            "status": run["status"],
            "measurements": dict(run["measurements"]),
        }
        for run in runs
    ]
    result["parameters"]["repetitions"] = count
    result["parameters"]["envelope_mode"] = args.envelope_mode
    # Budget against the slowest/largest repetition; retain all distributions.
    result["between_run_variation"] = {}
    for key, value in list(result["measurements"].items()):
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            values = [run["measurements"][key] for run in runs]
            result["between_run_variation"][key] = {"min": min(values), "max": max(values)}
            result["measurements"][key] = max(values)
    budget_ok = None
    code = 0
    if args.envelope_mode != "none":
        check = validate_envelope(result, profile, mode=args.envelope_mode)
        result["envelope_check"] = check.to_dict()
        if args.envelope_mode == "budget":
            budget_ok = check.ok
        if not check.ok:
            result["status"] = "budget_failed" if args.envelope_mode == "budget" else "incomplete"
            code = 3 if args.envelope_mode == "budget" else 2
    _apply_ga_eligibility(result, profile, args, budget_ok)
    return result, code


def main() -> int:
    args = _parse_args()
    result, exit_code = _run_repetitions(args)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
