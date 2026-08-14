"""``Embedder`` protocol (CR-6).

Three providers implement this: ``fastembed`` (default, ONNX, M2),
``ollama`` (opt-in, M2), ``hashing`` (deterministic, offline, M0+ and
every test). All three produce L2-normalised ``float32`` vectors of a
fixed dimension so ``core/router.py``'s cosine step never needs to know
which provider produced a given ``eph_embedding`` row.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Embedder(Protocol):
    """Framework-free (INV-1): no MCP import, ever."""

    model_name: str
    dim: int

    def embed(self, text: str) -> np.ndarray:
        """Embed a single string. Returns an L2-normalised float32 vector of shape (dim,)."""
        ...

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Embed many strings. Returns an L2-normalised float32 matrix of shape (len(texts), dim)."""
        ...
