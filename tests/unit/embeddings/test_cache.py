from __future__ import annotations

import numpy as np
import pytest

from magicite.embeddings.cache import CachingEmbedder


class _CountingEmbedder:
    model_name = "counting-4"
    dim = 4

    def __init__(self) -> None:
        self.embed_calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> np.ndarray:
        self.embed_calls += 1
        return np.array([float(len(text)), 0.0, 0.0, 0.0], dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        self.batch_calls += 1
        return np.zeros((len(texts), 4), dtype=np.float32)


def test_repeated_query_hits_cache_not_inner() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner, maxsize=10)

    v1 = cached.embed("same query")
    v2 = cached.embed("same query")

    assert np.array_equal(v1, v2)
    assert inner.embed_calls == 1
    assert cached.hits == 1
    assert cached.misses == 1


def test_distinct_queries_each_miss() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner, maxsize=10)
    cached.embed("a")
    cached.embed("b")
    assert inner.embed_calls == 2
    assert cached.misses == 2


def test_lru_eviction_at_maxsize() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner, maxsize=2)
    cached.embed("a")
    cached.embed("b")
    cached.embed("c")  # evicts "a" (least recently used)
    assert len(cached._cache) == 2
    cached.embed("a")  # must miss again -- was evicted
    assert inner.embed_calls == 4


def test_move_to_end_protects_recently_used_entry_from_eviction() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner, maxsize=2)
    cached.embed("a")
    cached.embed("b")
    cached.embed("a")  # re-touch "a" -- "b" is now the LRU victim
    cached.embed("c")  # evicts "b", not "a"
    assert inner.embed_calls == 3  # a, b, c -- no re-miss on the second "a"
    cached.embed("a")
    assert inner.embed_calls == 3  # still cached


def test_embed_batch_passthrough_never_cached() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner, maxsize=10)
    cached.embed_batch(["x", "y"])
    cached.embed_batch(["x", "y"])
    assert inner.batch_calls == 2


def test_model_name_and_dim_proxy_the_inner_embedder() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner)
    assert cached.model_name == "counting-4"
    assert cached.dim == 4


def test_clear_resets_cache_and_stats() -> None:
    inner = _CountingEmbedder()
    cached = CachingEmbedder(inner)
    cached.embed("a")
    cached.embed("a")
    cached.clear()
    assert cached.hits == 0
    assert cached.misses == 0
    cached.embed("a")
    assert inner.embed_calls == 2


def test_maxsize_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CachingEmbedder(_CountingEmbedder(), maxsize=0)
