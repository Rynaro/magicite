"""Label-blind actual-router adapter and pre-execution model byte guards.

These helpers establish a local diagnostic's inputs; they do not authenticate
an upstream model publisher or qualify an official retrieval/performance gate.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import router
from magicite.core.routing_policy import KNOWN_POLICY_IDS
from magicite.embeddings.fastembed_provider import DEFAULT_MODEL_NAME, FastEmbedProvider


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError("unsafe relative input path")
    result = (root / path).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("input escapes owned root")
    return result


def model_files(cache: Path) -> dict[str, str]:
    cache = cache.resolve()
    files = {}
    for path in sorted(cache.rglob("*")):
        if ".locks" in path.parts or path.name == "CACHEDIR.TAG":
            continue
        if path.is_file():
            relative = str(path.relative_to(cache))
            files[relative] = sha256(_contained(cache, relative))
    required = {
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    }
    if not required.issubset({Path(name).name for name in files}):
        raise ValueError("required model/tokenizer/config bytes missing")
    return files


def freeze_model(cache: Path) -> dict[str, Any]:
    files = model_files(cache)
    revisions = [path.read_text().strip() for path in cache.rglob("refs/main")]
    return {
        "schema": "magicite/observed-model/1",
        "model_name": DEFAULT_MODEL_NAME,
        "files": files,
        "observed_revisions": revisions,
        "libraries": {
            name: importlib.metadata.version(name)
            for name in ("fastembed", "onnxruntime", "numpy", "huggingface-hub")
        },
        "publisher_authenticated": False,
        "scope": "Expected bytes fixed for this downloaded diagnostic, not a publisher trust pin.",
    }


def verify_model(cache: Path, expected: dict[str, Any]) -> None:
    if expected.get("schema") != "magicite/observed-model/1" or not expected.get("files"):
        raise ValueError("model manifest required before execution")
    if expected.get("model_name") != DEFAULT_MODEL_NAME or model_files(cache) != expected["files"]:
        raise ValueError("model bytes changed or missing before execution")


def runtime_queries(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"query_id", "query_text", "compatibility_context"}
    if not value or any(set(row) != allowed for row in value):
        raise ValueError("runtime queries must contain only query identity, text and context")
    if any(
        not isinstance(row["query_id"], str)
        or not row["query_id"]
        or not isinstance(row["query_text"], str)
        or not row["query_text"].strip()
        or not isinstance(row["compatibility_context"], dict)
        for row in value
    ):
        raise ValueError("invalid runtime query")
    if len({row["query_id"] for row in value}) != len(value):
        raise ValueError("duplicate runtime query identity")
    return value


def verify_inventory(root: Path, rows: list[dict[str, Any]]) -> list[Path]:
    from magicite.engram import parser

    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("full unique corpus inventory required")
    paths = []
    for row in rows:
        path = _contained(root, row["path"])
        if not path.is_file() or sha256(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("corpus body digest/length mismatch")
        artifact, _ = parser.load_artifact_file(path, registry_root=path.parent)
        if artifact.id != row["id"] or artifact.name != row["name"]:
            raise ValueError("corpus body identity mismatch")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate corpus body path")
    return paths


class ProductionEmbedder(FastEmbedProvider):
    """Count actual production calls without substituting vectors or a factory."""

    def __init__(self, cache: Path) -> None:
        super().__init__(cache_dir=str(cache), offline=True)
        self.embed_calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> Any:
        self.embed_calls += 1
        return super().embed(text)

    def embed_batch(self, texts: list[str]) -> Any:
        self.batch_calls += 1
        return super().embed_batch(texts)


class ActualRouter:
    """Invoke actual production route; annotation fields are never accepted."""

    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        embedder: FastEmbedProvider,
        *,
        rank_depth: int = 5,
        comparison_budget: Any = None,
        evaluation_context: Any = None,
    ) -> None:
        if not isinstance(embedder, FastEmbedProvider):
            raise ValueError("production diagnostic requires actual FastEmbedProvider")
        if cfg.routing_policy not in KNOWN_POLICY_IDS:
            raise ValueError("unsupported actual routing policy")
        if isinstance(rank_depth, bool) or not isinstance(rank_depth, int) or rank_depth < 1:
            raise ValueError("positive rank depth required")
        self.cfg, self.conn, self.embedder = cfg, conn, embedder
        self.rank_depth = rank_depth
        self.comparison_budget = comparison_budget
        self.evaluation_context = evaluation_context
        if comparison_budget is not None and comparison_budget.output_k != rank_depth:
            raise ValueError("comparison output depth mismatch")

    def predict(self, query: dict[str, Any], *, raw_trace: bool = False) -> dict[str, Any]:
        runtime_queries([query])
        before = router._cached_route_index.cache_info()
        calls_before = getattr(self.embedder, "embed_calls", None)
        batches_before = getattr(self.embedder, "batch_calls", None)
        outcome = router.route(
            self.cfg,
            self.conn,
            self.embedder,
            query=query["query_text"],
            context=query["compatibility_context"],
            k=self.rank_depth,
            **(
                {"_evaluation_context": self.evaluation_context}
                if self.evaluation_context is not None
                else {}
            ),
            **({"comparison_budget": self.comparison_budget} if self.comparison_budget is not None else {}),
        )
        after = router._cached_route_index.cache_info()
        expected_policy = (
            self.evaluation_context.policy_id
            if self.evaluation_context is not None
            else self.cfg.routing_policy
        )
        if outcome.policy_id != expected_policy or outcome.decision is None:
            raise ValueError("actual policy dispatch or decision missing")
        decision = outcome.decision
        if decision.status == "error" and not raw_trace:
            raise ValueError("actual router operational error: " + str(decision.operational_error))
        trace = {
            "query_id": query["query_id"],
            "policy_id": outcome.policy_id,
            "policy_family": outcome.policy_family,
            "policy_digest": outcome.policy_digest,
            "config_digest": decision.config_digest,
            "status": decision.status,
            "candidate_ids": [row.id for row in outcome.candidates],
            "scores": [row.score for row in outcome.candidates],
            "selected_ids": list(decision.selected_ids),
            "abstained": decision.status == "abstained",
            "exclusions": [asdict(row) for row in decision.exclusions],
            "selected_content_digests": decision.selected_content_digests,
            "model_digest": decision.model_digest,
            "model_digest_semantics": "Raw router name/config identity, not downloaded-byte authenticity",
            "registry_digest": decision.registry_digest,
            "index_generation_id": decision.index_generation_id,
            "rank_depth": self.rank_depth,
            "confidence": decision.confidence.value,
            "calibration_digest": decision.calibration_digest,
            "confidence_calibration_id": decision.confidence.calibration_id,
            "production_active_identity": router._resolve_active_policy(self.cfg)[1],
            "evaluation_only": self.evaluation_context is not None,
            "comparison_budget_digest": self.comparison_budget.digest
            if self.comparison_budget is not None
            else None,
            "comparison_budget": self.comparison_budget.to_dict()
            if self.comparison_budget is not None
            else None,
            "candidate_work": dict(decision.truncations),
            "confidence_reason": (
                "Frozen evaluation probability; integrity only, no operator authority"
                if self.evaluation_context is not None
                else "Authenticated compatible top1 probability"
            )
            if decision.confidence.value is not None
            else "No usable compatible probability for this result",
            "cache": {
                "route_index_hits": after.hits - before.hits,
                "route_index_misses": after.misses - before.misses,
                "production_embed_calls": (getattr(self.embedder, "embed_calls", 0) - calls_before)
                if calls_before is not None
                else None,
                "production_batch_calls": (getattr(self.embedder, "batch_calls", 0) - batches_before)
                if batches_before is not None
                else None,
                "embedding_cache": "No adapter query cache; underlying ONNX runtime remains loaded",
                "subject_projection": "Live subject cache may be warm; no fabricated hit counter.",
            },
        }
        if self.comparison_budget is not None:
            from magicite.core.comparison_budget import comparison_config_digest
            from magicite.eval.digests import sha256_json

            trace["comparison_config_digest"] = comparison_config_digest(self.cfg)

            eligible, _, _ = router._evaluate_route_eligibility(
                self.cfg,
                self.conn,
                router._fetch_candidates(self.conn, self.embedder.model_name),
                route_context=router.RouteContext(),
                server_policy=router.DEFAULT_SERVER_POLICY,
            )
            trace["eligibility_digest"] = sha256_json(sorted(str(r["id"]) for r in eligible))
            trace["body_availability_digest"] = sha256_json(
                sorted((str(r["id"]), str(r["content_sha256"]), str(r["path"])) for r in eligible)
            )
            trace["query_context_digest"] = sha256_json(query)
            from magicite.core.candidates import RetrievalIndex
            from magicite.core.index_generation import IndexCatalog

            index = RetrievalIndex.from_catalog(IndexCatalog(self.conn), decision.index_generation_id)
            trace["iteration_order_digest"] = sha256_json(list(index.entries))
            trace["source_digest"] = sha256_json(
                {
                    str(p.relative_to(Path(__file__).parents[1])): sha256(p)
                    for p in sorted(Path(__file__).parents[1].rglob("*.py"))
                }
            )
            trace["mechanism_digest"] = sha256_json(
                {
                    "policy": outcome.policy_id,
                    "budget": self.comparison_budget.to_dict(),
                    "algorithm": "indexed-candidates/1;RRF60",
                    "source": {
                        str(p.relative_to(Path(__file__).parents[1])): sha256(p)
                        for p in (Path(router.__file__), Path(router.candidates_mod.__file__))
                    },
                }
            )
        if raw_trace:
            trace.update(
                {
                    "raw_candidate_ids": [row.id for row in decision.raw_candidates],
                    "raw_scores": [row.score for row in decision.raw_candidates],
                    "query_fingerprint": decision.query_fingerprint,
                    "snapshot_id": decision.snapshot_id,
                    "schema_digest": decision.schema_digest,
                    "tokenizer_digest": decision.tokenizer_digest,
                    "reason_codes": list(decision.reason_codes),
                    "operational_error": decision.operational_error,
                }
            )
        return trace


def descriptive_quality(predictions: list[dict[str, Any]], oracle: list[dict[str, Any]]) -> dict[str, Any]:
    """Scoring-only labels; no ranking or abstention is produced here."""
    labels = {row["query_id"]: row for row in oracle}
    if set(labels) != {row["query_id"] for row in predictions} or len(predictions) != len(labels):
        raise ValueError("prediction/label coverage mismatch")
    positive = selected = selected_correct = false_selected = negative = hit1 = hit5 = 0
    reciprocal = 0.0
    for row in predictions:
        label = labels[row["query_id"]]
        relevant = {key for key, value in label["relevance"].items() if value > 0}
        chosen = row["selected_ids"]
        selected += bool(chosen)
        if relevant:
            positive += 1
            ids = row["candidate_ids"]
            hit1 += bool(ids and ids[0] in relevant)
            hit5 += bool(relevant.intersection(ids[:5]))
            rank = next((i for i, value in enumerate(ids, 1) if value in relevant), None)
            reciprocal += 1 / rank if rank else 0
            selected_correct += bool(chosen and chosen[0] in relevant)
        else:
            negative += 1
            false_selected += bool(chosen)
    n = len(predictions)
    return {
        "queries": n,
        "answerable": positive,
        "no_match": negative,
        "hit_at_1": {
            "numerator": hit1,
            "denominator": positive,
            "value": hit1 / positive if positive else None,
        },
        "hit_at_5": {
            "numerator": hit5,
            "denominator": positive,
            "value": hit5 / positive if positive else None,
        },
        "mrr_available_rank": {
            "numerator": reciprocal,
            "denominator": positive,
            "value": reciprocal / positive if positive else None,
        },
        "selection_coverage": {"numerator": selected, "denominator": n, "value": selected / n},
        "selective_accuracy": {
            "numerator": selected_correct,
            "denominator": selected,
            "value": selected_correct / selected if selected else None,
        },
        "no_match_false_selections": {
            "numerator": false_selected,
            "denominator": negative,
            "value": false_selected / negative if negative else None,
        },
        "qualification": "UNEVALUATED",
        "scope": "Descriptive development diagnostic, not E3 qualification.",
    }
