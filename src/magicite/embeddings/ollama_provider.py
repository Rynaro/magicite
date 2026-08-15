"""Ollama embedding provider (CR-6: opt-in, preserves docs/02's original
Ollama reading for operators who already run a local Ollama daemon).

Talks plain HTTP to a local ``ollama serve`` instance's ``/api/embed``
endpoint via the stdlib (``urllib.request``) -- deliberately *not* the
``ollama`` PyPI package, so selecting this provider never adds a new
mandatory or transitive dependency (Risk R4's ``torch``-ban discipline
extends to "don't grow the dependency surface for an opt-in path either").
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable

import numpy as np

DEFAULT_HOST = "http://localhost:11434"
#: docs/04's routing-block example: CR-6's "docs/04's bge-m3 example
#: remains legal" -- this is the model that reference names.
DEFAULT_MODEL = "bge-m3"
DEFAULT_TIMEOUT_S = 30.0

#: HTTP POST performer, dependency-injected so this module is unit-testable
#: without a running Ollama daemon. Signature: (url, payload_dict) -> parsed
#: JSON response dict.
Poster = Callable[[str, dict], dict]


class OllamaUnavailableError(RuntimeError):
    """Raised when the Ollama daemon is unreachable or returns an error."""


def _default_poster(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - local daemon only, never a remote URL
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8")))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OllamaUnavailableError(f"ollama request to {url!r} failed: {exc}") from exc


class OllamaProvider:
    """``Embedder`` backed by a local Ollama daemon's embeddings endpoint."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        dim: int,
        host: str = DEFAULT_HOST,
        poster: Poster = _default_poster,
    ) -> None:
        #: recorded verbatim into ``engram.embedding_model`` (CR-6) -- prefixed
        #: so a differently-embedded engram is unambiguously detectable even
        #: across providers that happen to share a bare model name.
        self.model_name = f"ollama:{model_name}"
        self.dim = dim
        self._raw_model = model_name
        self._host = host.rstrip("/")
        self._poster = poster

    @staticmethod
    def _normalize(vec: np.ndarray) -> np.ndarray:
        vec = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 1e-9 else vec

    def _embed_many(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        response = self._poster(
            f"{self._host}/api/embed", {"model": self._raw_model, "input": texts}
        )
        embeddings = response.get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            raise OllamaUnavailableError(
                f"ollama returned an unexpected embeddings payload for model {self._raw_model!r}: "
                f"{response!r}"
            )
        return np.stack([self._normalize(np.asarray(v, dtype=np.float32)) for v in embeddings])

    def embed(self, text: str) -> np.ndarray:
        return self._embed_many([text])[0]

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        return self._embed_many(texts)


def get_embedder(
    *, model_name: str = DEFAULT_MODEL, dim: int, host: str = DEFAULT_HOST
) -> OllamaProvider:
    return OllamaProvider(model_name=model_name, dim=dim, host=host)
