"""In-process LRU cache wrapping any :class:`Embedder` (spec §1 layout).

``route()`` is a hot path with a latency budget (AC-011: "embed <=50ms"
of the <100ms p95 total, spec §3.3). For the two providers that actually
pay real inference cost per call -- ``fastembed`` (ONNX forward pass) and
``ollama`` (an HTTP round trip) -- a session that re-issues the same or a
near-identical query string (a very common interactive pattern: a host
retries `route()` after a failed `signal_outcome`, or calls it once to
preview and again to confirm) should not re-pay that cost. ``hashing`` is
already cheap enough that wrapping it is a no-op in practice, but it costs
nothing to make caching provider-agnostic.

Deliberately *not* used to cache ``embed_batch()`` (``register()``/
``sync()``'s bulk path): those calls are almost always over distinct
engram bodies, so a bounded cache would just add bookkeeping overhead for
a near-zero hit rate; nothing stops a caller from wrapping the batch path
too, this module simply doesn't do it by default.
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np

from magicite.embeddings.base import Embedder

DEFAULT_MAXSIZE = 256


class CachingEmbedder:
    """``Embedder`` decorator: memoizes ``embed(text)`` by exact string match.

    A plain LRU (``OrderedDict`` move-to-end + evict-oldest) keyed on the
    literal query text -- no normalization, no fuzzy matching: the cache
    is a latency optimization, not a semantic index, so it must never
    change what a call returns versus the wrapped embedder.
    """

    def __init__(self, inner: Embedder, *, maxsize: int = DEFAULT_MAXSIZE) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._inner = inner
        # Plain instance attributes (not @property) so this class matches
        # the Embedder Protocol's structural shape exactly -- a read-only
        # property satisfies "has a `model_name`" at runtime but not
        # mypy's protocol-variable check, which expects an assignable
        # attribute.
        self.model_name = inner.model_name
        self.dim = inner.dim
        self._maxsize = maxsize
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def embed(self, text: str) -> np.ndarray:
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            self.hits += 1
            return cached
        self.misses += 1
        vec = self._inner.embed(text)
        self._cache[text] = vec
        self._cache.move_to_end(text)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
        return vec

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        return self._inner.embed_batch(texts)

    def clear(self) -> None:
        self._cache.clear()
        self.hits = 0
        self.misses = 0
