"""Deterministic, offline "hashing trick" embedder (CR-6: ``hashing`` provider).

No model, no network, no torch — pure Python + numpy. This is the
provider ``MAGICITE_EMBEDDING_PROVIDER=hashing`` selects for CI and for
every test in this repo (``tests/conftest.py``'s frozen embedder
fixture). It implements the standard feature-hashing vectorizer (Weinberger
et al. 2009): each word unigram/bigram hashes (via BLAKE2b, stable across
interpreter runs and platforms — unlike Python's salted ``hash()``) to a
bucket in ``[0, dim)`` with a sign bit drawn from a second hash byte, so
collisions partially cancel instead of only ever adding. The result is
L2-normalised, so cosine similarity is a plain dot product.

This is deliberately simple: it exists to make ``core/router.py`` and the
acceptance suite exercisable without a model download, not to be a good
embedding in the ML sense. ``fastembed_provider``/``ollama_provider``
(M2) replace it in production.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

from magicite.embeddings.base import Embedder

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")

DEFAULT_DIM = 256


def _tokens(text: str) -> list[str]:
    words = _TOKEN_RE.findall(text.lower())
    if not words:
        return []
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    return words + bigrams


def _hash_bucket_and_sign(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=9).digest()
    bucket = int.from_bytes(digest[:8], "big") % dim
    sign = 1.0 if digest[8] & 1 == 0 else -1.0
    return bucket, sign


class HashingEmbedder:
    """Deterministic feature-hashing embedder. Same input -> same vector, always."""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.model_name = f"hashing-{dim}"
        self.dim = dim

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in _tokens(text):
            bucket, sign = _hash_bucket_and_sign(token, self.dim)
            vec[bucket] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 1e-9:
            vec = vec / norm
        return vec

    def embed(self, text: str) -> np.ndarray:
        return self._embed_one(text)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._embed_one(t) for t in texts])


def get_embedder(dim: int = DEFAULT_DIM) -> Embedder:
    return HashingEmbedder(dim=dim)
