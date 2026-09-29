"""Pure candidate generation: dense / sparse / trigger + RRF fusion (C3 / S05).

``generate(query, index, config) -> CandidateBatch/1`` is independent of route
orchestration (S07) and eligibility predicates (S06). Callers pass an eligible-ID
mask; S05 tests use an all-eligible mask.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

from magicite.core.index_generation import (
    FTS5UnavailableError,
    FullContentProjection,
    GenerationMeta,
    GenerationStore,
    IndexedEntry,
    IndexFingerprint,
    fts5_available,
    open_sparse_connection,
    tokenize_words,
)
from magicite.embeddings.base import Embedder
from magicite.embeddings.reranker import DEFAULT_RERANK_LIMIT

DEFAULT_RRF_K = 60
DEFAULT_PER_SOURCE_LIMIT = 100
DEFAULT_SCAN_BUDGET = 1000


@dataclass(frozen=True)
class ComponentHit:
    engram_id: str
    rank: int
    score: float


@dataclass(frozen=True)
class Candidate:
    """Candidate/1 (C3). Scores are not probabilities."""

    id: str
    revision: int
    fused_score: float
    body_digest: str
    snapshot_id: str
    dense_rank: int | None = None
    sparse_rank: int | None = None
    trigger_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    trigger_score: float | None = None
    asset_digest: str | None = None
    schema_version: Literal["Candidate/1"] = "Candidate/1"


@dataclass(frozen=True)
class CandidateBatch:
    """CandidateBatch/1 (C3)."""

    candidates: tuple[Candidate, ...]
    truncations: Mapping[str, int]
    reason_codes: tuple[str, ...]
    components: Mapping[str, tuple[ComponentHit, ...]]
    snapshot_id: str
    generation_id: str
    schema_version: Literal["CandidateBatch/1"] = "CandidateBatch/1"


@dataclass(frozen=True)
class CandidateConfig:
    rrf_k: int = DEFAULT_RRF_K
    per_source_limit: int = DEFAULT_PER_SOURCE_LIMIT
    scan_budget: int = DEFAULT_SCAN_BUDGET
    sources: tuple[str, ...] = ("dense", "sparse", "trigger")
    dense_only_fallback: bool = False
    top_k: int = DEFAULT_PER_SOURCE_LIMIT


@dataclass
class RetrievalIndex:
    """Immutable in-memory view of one published (or test-constructed) generation."""

    generation_id: str
    snapshot_id: str
    fingerprint: IndexFingerprint
    entries: dict[str, IndexedEntry]
    sparse_conn: sqlite3.Connection | None = None
    _owns_sparse: bool = False

    @classmethod
    def from_generation_meta(
        cls,
        meta: GenerationMeta,
        *,
        sparse_conn: sqlite3.Connection | None = None,
    ) -> RetrievalIndex:
        return cls(
            generation_id=meta.generation_id,
            snapshot_id=meta.snapshot_id,
            fingerprint=meta.fingerprint,
            entries=dict(meta.entries),
            sparse_conn=sparse_conn,
        )

    @classmethod
    def from_store(cls, store: GenerationStore, generation_id: str | None = None) -> RetrievalIndex:
        meta = store.pin(generation_id)
        conn = open_sparse_connection(store, meta.generation_id)
        return cls(
            generation_id=meta.generation_id,
            snapshot_id=meta.snapshot_id,
            fingerprint=meta.fingerprint,
            entries=dict(meta.entries),
            sparse_conn=conn,
            _owns_sparse=True,
        )

    @classmethod
    def build_in_memory(
        cls,
        *,
        generation_id: str,
        snapshot_id: str,
        fingerprint: IndexFingerprint,
        entries: Mapping[str, IndexedEntry],
    ) -> RetrievalIndex:
        """Construct a retrieval index with an ephemeral FTS5 table (unit tests)."""
        if not fts5_available():
            raise FTS5UnavailableError("SQLite FTS5 is unavailable")
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE idx_fts USING fts5("
            "engram_id UNINDEXED, title, intent, triggers, body, "
            "tokenize='unicode61')"
        )
        for engram_id, entry in entries.items():
            p = entry.projection
            conn.execute(
                "INSERT INTO idx_fts(engram_id, title, intent, triggers, body) VALUES (?,?,?,?,?)",
                (engram_id, p.title, p.intent_text, p.triggers_text, p.body_text),
            )
        conn.commit()
        return cls(
            generation_id=generation_id,
            snapshot_id=snapshot_id,
            fingerprint=fingerprint,
            entries=dict(entries),
            sparse_conn=conn,
            _owns_sparse=True,
        )

    def close(self) -> None:
        if self._owns_sparse and self.sparse_conn is not None:
            self.sparse_conn.close()
            self.sparse_conn = None


def reciprocal_rank_fuse(
    ranked_lists: Mapping[str, Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """RRF: sum 1/(k + rank) across lists; ranks start at 1; ties by stable ID (C3)."""
    scores: dict[str, float] = {}
    for ids in ranked_lists.values():
        for rank, engram_id in enumerate(ids, start=1):
            scores[engram_id] = scores.get(engram_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _escape_fts5_token(token: str) -> str:
    """Quote a token for FTS5 MATCH so exact symbols survive."""
    cleaned = token.replace('"', '""')
    return f'"{cleaned}"'


def _fts5_query(query: str) -> str | None:
    tokens = tokenize_words(query)
    if not tokens:
        # Preserve exact non-word tokens (error codes like E_FOO) via symbol scan.
        raw = query.strip()
        if not raw:
            return None
        return _escape_fts5_token(raw)
    # AND of quoted tokens keeps exact identifier matches reproducible.
    return " AND ".join(_escape_fts5_token(t) for t in tokens)


def dense_candidates(
    query_vec: np.ndarray,
    index: RetrievalIndex,
    *,
    limit: int = DEFAULT_PER_SOURCE_LIMIT,
    eligible_ids: frozenset[str] | None = None,
    scan_budget: int = DEFAULT_SCAN_BUDGET,
) -> tuple[list[ComponentHit], int]:
    """Cosine (dot of L2 vectors) over dense rows; eligibility mask + refill."""
    scored: list[tuple[str, float]] = []
    scanned = 0
    for engram_id, entry in index.entries.items():
        if scanned >= scan_budget:
            break
        scanned += 1
        if entry.dense_vec is None:
            continue
        if eligible_ids is not None and engram_id not in eligible_ids:
            continue
        score = float(np.dot(query_vec, entry.dense_vec))
        scored.append((engram_id, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    truncated = max(0, len(scored) - limit)
    hits = [
        ComponentHit(engram_id=eid, rank=rank, score=score)
        for rank, (eid, score) in enumerate(scored[:limit], start=1)
    ]
    return hits, truncated


def sparse_candidates(
    query: str,
    index: RetrievalIndex,
    *,
    limit: int = DEFAULT_PER_SOURCE_LIMIT,
    eligible_ids: frozenset[str] | None = None,
    scan_budget: int = DEFAULT_SCAN_BUDGET,
) -> tuple[list[ComponentHit], int]:
    """FTS5 BM25 over title/intent/triggers/body; max-chunk aggregation is per-artifact row."""
    if index.sparse_conn is None:
        raise FTS5UnavailableError("sparse index connection is not available")
    match = _fts5_query(query)
    if match is None:
        return [], 0
    # Fetch a scan-budget window then filter eligibility so we can refill.
    fetch_n = min(max(limit * 4, limit), scan_budget)
    sql = (
        "SELECT engram_id, bm25(idx_fts) AS score "
        "FROM idx_fts WHERE idx_fts MATCH ? "
        "ORDER BY bm25(idx_fts), engram_id LIMIT ?"
    )
    rows = list(index.sparse_conn.execute(sql, (match, fetch_n)))
    hits: list[ComponentHit] = []
    for engram_id, raw_score in rows:
        if eligible_ids is not None and engram_id not in eligible_ids:
            continue
        # bm25() is lower-is-better (more negative ≈ better); invert for display.
        score = -float(raw_score)
        hits.append(ComponentHit(engram_id=str(engram_id), rank=0, score=score))
        if len(hits) >= limit:
            break
    # Assign ranks after eligibility filtering (stable by score then id).
    hits.sort(key=lambda h: (-h.score, h.engram_id))
    ranked = [
        ComponentHit(engram_id=h.engram_id, rank=i, score=h.score)
        for i, h in enumerate(hits[:limit], start=1)
    ]
    truncated = max(0, len(hits) - limit)
    return ranked, truncated


def trigger_candidates(
    query: str,
    index: RetrievalIndex,
    *,
    limit: int = DEFAULT_PER_SOURCE_LIMIT,
    eligible_ids: frozenset[str] | None = None,
    scan_budget: int = DEFAULT_SCAN_BUDGET,
) -> tuple[list[ComponentHit], int]:
    """Positive-trigger / exact-symbol overlap. Negative triggers are features only."""
    q_lower = query.lower()
    q_tokens = {t.lower() for t in tokenize_words(query)}
    scored: list[tuple[str, float]] = []
    scanned = 0
    for engram_id, entry in index.entries.items():
        if scanned >= scan_budget:
            break
        scanned += 1
        if eligible_ids is not None and engram_id not in eligible_ids:
            continue
        p = entry.projection
        score = 0.0
        for line in p.triggers_text.splitlines():
            term = line[2:].strip() if line[:2] in ("+ ", "- ") else line.strip()
            if not term:
                continue
            polarity = 1.0 if not line.startswith("- ") else 0.0  # negatives never boost
            if polarity <= 0:
                continue
            if term.lower() in q_lower or term.lower() in q_tokens:
                score += 1.0
            elif any(tok and tok in q_lower for tok in term.lower().split()):
                score += 0.5
        for sym in p.symbols:
            if sym.lower() in q_lower or sym in query:
                score += 1.5
        # Exact error-token / prose needle in body.
        if q_lower and q_lower in p.body_text.lower():
            score += 2.0
        if score > 0:
            scored.append((engram_id, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    truncated = max(0, len(scored) - limit)
    hits = [
        ComponentHit(engram_id=eid, rank=rank, score=score)
        for rank, (eid, score) in enumerate(scored[:limit], start=1)
    ]
    return hits, truncated


def _embed_query_text(query: str, index: RetrievalIndex, embedder: Embedder) -> np.ndarray:
    # Query embedding uses the same embedder identity as the generation fingerprint.
    return embedder.embed(query)


def generate(
    query: str,
    index: RetrievalIndex,
    config: CandidateConfig | None = None,
    *,
    embedder: Embedder | None = None,
    query_vec: np.ndarray | None = None,
    eligible_ids: frozenset[str] | None = None,
) -> CandidateBatch:
    """Pure ``generate(query, snapshot/index, config) -> CandidateBatch/1`` (C3)."""
    cfg = config or CandidateConfig()
    if cfg.per_source_limit < 1 or cfg.per_source_limit > 1000:
        raise ValueError("per_source_limit must be in [1, 1000]")

    truncations: dict[str, int] = {}
    reason_codes: list[str] = []
    components: dict[str, tuple[ComponentHit, ...]] = {}
    ranked_lists: dict[str, list[str]] = {}

    sources = list(cfg.sources)
    if "sparse" in sources and index.sparse_conn is None:
        if cfg.dense_only_fallback:
            sources = [s for s in sources if s != "sparse"]
            reason_codes.append("degraded_sparse_capability")
            reason_codes.append("dense_only_fallback")
        else:
            raise FTS5UnavailableError(
                "sparse source requested but FTS5 index is unavailable; "
                "set dense_only_fallback=True for an explicit fallback",
            )

    if "dense" in sources:
        if query_vec is None:
            if embedder is None:
                raise ValueError("dense source requires embedder or query_vec")
            query_vec = _embed_query_text(query, index, embedder)
        dense_hits, dense_trunc = dense_candidates(
            query_vec,
            index,
            limit=cfg.per_source_limit,
            eligible_ids=eligible_ids,
            scan_budget=cfg.scan_budget,
        )
        truncations["dense"] = dense_trunc
        components["dense"] = tuple(dense_hits)
        ranked_lists["dense"] = [h.engram_id for h in dense_hits]

    if "sparse" in sources:
        sparse_hits, sparse_trunc = sparse_candidates(
            query,
            index,
            limit=cfg.per_source_limit,
            eligible_ids=eligible_ids,
            scan_budget=cfg.scan_budget,
        )
        truncations["sparse"] = sparse_trunc
        components["sparse"] = tuple(sparse_hits)
        ranked_lists["sparse"] = [h.engram_id for h in sparse_hits]

    if "trigger" in sources:
        trigger_hits, trigger_trunc = trigger_candidates(
            query,
            index,
            limit=cfg.per_source_limit,
            eligible_ids=eligible_ids,
            scan_budget=cfg.scan_budget,
        )
        truncations["trigger"] = trigger_trunc
        components["trigger"] = tuple(trigger_hits)
        ranked_lists["trigger"] = [h.engram_id for h in trigger_hits]

    fused = reciprocal_rank_fuse(ranked_lists, k=cfg.rrf_k)
    if eligible_ids is not None:
        fused = [(eid, score) for eid, score in fused if eid in eligible_ids]

    # Starvation signal when the mask filtered everything despite scan budget.
    if eligible_ids is not None and not fused and index.entries:
        any_eligible = any(eid in eligible_ids for eid in index.entries)
        if any_eligible:
            reason_codes.append("candidate_budget_exhausted")
        else:
            reason_codes.append("no_eligible_candidates")

    # Component lookup helpers
    dense_by_id = {h.engram_id: h for h in components.get("dense", ())}
    sparse_by_id = {h.engram_id: h for h in components.get("sparse", ())}
    trigger_by_id = {h.engram_id: h for h in components.get("trigger", ())}

    candidates: list[Candidate] = []
    for engram_id, fused_score in fused[: cfg.top_k]:
        entry = index.entries.get(engram_id)
        if entry is None:
            continue
        d = dense_by_id.get(engram_id)
        s = sparse_by_id.get(engram_id)
        t = trigger_by_id.get(engram_id)
        candidates.append(
            Candidate(
                id=engram_id,
                revision=entry.projection.revision,
                fused_score=fused_score,
                body_digest=entry.body_digest,
                snapshot_id=index.snapshot_id,
                dense_rank=d.rank if d else None,
                sparse_rank=s.rank if s else None,
                trigger_rank=t.rank if t else None,
                dense_score=d.score if d else None,
                sparse_score=s.score if s else None,
                trigger_score=t.score if t else None,
                asset_digest=entry.asset_digest,
            )
        )

    return CandidateBatch(
        candidates=tuple(candidates),
        truncations=truncations,
        reason_codes=tuple(reason_codes),
        components=components,
        snapshot_id=index.snapshot_id,
        generation_id=index.generation_id,
    )


class Reranker(Protocol):
    """Optional local reranker seam (C3). No mandatory model acquisition."""

    model_id: str
    model_digest: str

    def rerank(
        self,
        query: str,
        candidates: Sequence[Candidate],
        *,
        token_budget: int,
        timeout_s: float,
    ) -> Sequence[Candidate]:
        ...


def apply_reranker(
    reranker: Reranker,
    query: str,
    batch: CandidateBatch,
    *,
    token_budget: int = 2048,
    timeout_s: float = 2.0,
    limit: int = DEFAULT_RERANK_LIMIT,
) -> CandidateBatch:
    """Rerank an already-eligible slate (≤20 by default). Failures are caller-handled."""
    slate = batch.candidates[:limit]
    reranked = tuple(reranker.rerank(query, slate, token_budget=token_budget, timeout_s=timeout_s))
    return CandidateBatch(
        candidates=reranked,
        truncations=batch.truncations,
        reason_codes=batch.reason_codes,
        components=batch.components,
        snapshot_id=batch.snapshot_id,
        generation_id=batch.generation_id,
    )


def indexed_entry_from_projection(
    projection: FullContentProjection,
    *,
    dense_vec: np.ndarray | None = None,
    body_digest: str | None = None,
    asset_digest: str | None = None,
) -> IndexedEntry:
    return IndexedEntry(
        projection=projection,
        dense_vec=dense_vec,
        body_digest=body_digest or projection.projection_sha256,
        asset_digest=asset_digest,
    )
