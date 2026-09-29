"""Optional local reranker provider seam (C3 / S05).

No mandatory model acquisition for GA. Concrete providers may consume eligible
candidate slates from ``magicite.core.candidates``; this module stays free of a
hard import cycle with ``core`` so embedder selection remains isolated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_RERANK_LIMIT = 20


class RerankerProvider(Protocol):
    """Framework-free reranker protocol (mirrors Embedder discipline)."""

    model_id: str
    model_digest: str

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        *,
        token_budget: int,
        timeout_s: float,
    ) -> Sequence[Any]:
        ...


@dataclass
class NoOpReranker:
    """Identity reranker — records that no model was applied."""

    model_id: str = "noop"
    model_digest: str = "0" * 64

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        *,
        token_budget: int,
        timeout_s: float,
    ) -> Sequence[Any]:
        del query, token_budget, timeout_s
        return list(candidates)[:DEFAULT_RERANK_LIMIT]


def get_reranker(provider: str = "noop") -> RerankerProvider:
    """Resolve a reranker provider. Only ``noop`` ships in S05."""
    if provider == "noop":
        return NoOpReranker()
    raise ValueError(
        f"unknown reranker provider {provider!r}; S05 ships only 'noop' "
        f"(limit={DEFAULT_RERANK_LIMIT})"
    )


__all__ = [
    "DEFAULT_RERANK_LIMIT",
    "NoOpReranker",
    "RerankerProvider",
    "get_reranker",
]
