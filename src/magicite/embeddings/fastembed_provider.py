"""``fastembed`` ONNX embedding provider (CR-6, Risk R4).

The v1 default provider (spec §1 dependency floor: ``fastembed>=0.4``,
"ONNX local embeddings, no torch"): ``BAAI/bge-small-en-v1.5``, dim 384,
baked into the runtime image at build time (spec Assumption A2). ``torch``
stays banned (AC-031) -- ``fastembed`` ships its own ONNX runtime and
never pulls it in transitively (verified by ``uv.lock``/CI's dependency
audit, not this module).

**Serve-time network safety -- and a load-bearing correction.** An earlier
version of this module passed ``lazy_load=True`` to ``fastembed
.TextEmbedding`` and documented that as sufficient to guarantee no
network call at server-boot time. That was wrong, and worth recording
here rather than quietly fixing: as of ``fastembed`` 0.8.0,
``OnnxTextEmbedding.__init__`` calls ``self.download_model(...)``
*unconditionally*, before it ever checks ``self.lazy_load`` --
``lazy_load`` only defers ``load_onnx_model()`` (building the ONNX
``InferenceSession``), not the cache-check/download step. Constructing
``TextEmbedding(...)`` therefore *always* touches the filesystem cache
(and, absent ``local_files_only``, the network) immediately, regardless
of ``lazy_load``.

This module now enforces the guarantee itself instead of delegating it to
a fastembed kwarg that does not provide it: :class:`FastEmbedProvider`
never calls ``factory(...)`` in ``__init__`` at all. The underlying
``TextEmbedding`` is constructed on first use (``embed()``/
``embed_batch()``), memoized after that. Since the only place this class
is constructed is once, at server boot (``mcp/app.py::build_state``), and
every actual embedding happens inside a subsequent tool call, this is
what makes "no network call at import or at serve time" (R4) true for
real rather than by an unverified assumption about a dependency's kwarg
semantics.

``MAGICITE_EMBEDDING_OFFLINE=1`` (``Config.embedding_offline``) is
threaded through to fastembed's own ``local_files_only`` kwarg (which
fastembed also derives from ``HF_HUB_OFFLINE``, see
``fastembed.common.model_management.ModelManagement.download_model``):
with it set, a cache miss raises immediately rather than ever attempting
a network call -- the guarantee a hardened, network-disabled container
(spec §8, AC-026) depends on.

``factory`` is dependency-injected (default: the real
``fastembed.TextEmbedding``) so this module is unit-testable without a
baked model or network access -- the acceptance/unit suite always runs
against ``MAGICITE_EMBEDDING_PROVIDER=hashing`` (CR-6); this provider's
own tests inject a stub factory instead of touching fastembed for real.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

import numpy as np

#: docs/04's routing-block example uses ``bge-m3``; the *default* model this
#: provider bakes and ships is bge-small-en-v1.5 (spec §1 pyproject
#: dependency floor, Assumption A2) -- both are legal per CR-6 as long as
#: ``embedding.model`` records whichever one actually produced the vector
#: (``core/registry.py::_embed_and_store`` -> ``storage.durable
#: .set_embedding_ref``, keyed off ``embedder.model_name``, not a constant).
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384


class _TextEmbeddingLike(Protocol):
    """The slice of ``fastembed.TextEmbedding``'s surface this module uses."""

    def embed(self, documents: str | Iterable[str], **kwargs: Any) -> Iterable[np.ndarray]: ...


TextEmbeddingFactory = Callable[..., _TextEmbeddingLike]


class FastEmbedModelUnavailableError(RuntimeError):
    """Raised when the baked/cached model cannot be loaded (offline miss,
    or -- in online mode -- any download failure). Never swallowed: a
    silent fallback to a different embedding space would violate CR-6
    ("the file records whichever [model] produced the vector")."""


def _default_factory(**kwargs: Any) -> _TextEmbeddingLike:
    # Imported lazily (not at module import time): `fastembed` pulls in
    # onnxruntime, which is legitimately heavy but must never be the
    # reason a hot-path-adjacent module fails to import in an environment
    # that only ever uses the `hashing` provider (e.g. this repo's own
    # test suite, CR-6).
    from fastembed import TextEmbedding

    return TextEmbedding(**kwargs)


class FastEmbedProvider:
    """``Embedder`` for the local ONNX ``BAAI/bge-small-en-v1.5`` model.

    Construction is cheap and touches nothing but this object's own
    attributes -- see the module docstring for why that has to be
    self-enforced (deferred ``factory(...)``) rather than assumed from
    fastembed's ``lazy_load`` kwarg.
    """

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL_NAME,
        dim: int = DEFAULT_DIM,
        offline: bool = False,
        cache_dir: str | None = None,
        factory: TextEmbeddingFactory = _default_factory,
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self._offline = offline
        self._cache_dir = cache_dir
        self._factory = factory
        self._model: _TextEmbeddingLike | None = None

    def _ensure_model(self) -> _TextEmbeddingLike:
        if self._model is None:
            try:
                self._model = self._factory(
                    model_name=self.model_name,
                    cache_dir=self._cache_dir,
                    local_files_only=self._offline,
                )
            except Exception as exc:
                raise FastEmbedModelUnavailableError(
                    f"could not construct fastembed model {self.model_name!r} "
                    f"(offline={self._offline}): {exc}"
                ) from exc
        return self._model

    @staticmethod
    def _normalize(vec: np.ndarray) -> np.ndarray:
        vec = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 1e-9 else vec

    def embed(self, text: str) -> np.ndarray:
        model = self._ensure_model()
        try:
            (vec,) = list(model.embed([text]))
        except Exception as exc:
            raise FastEmbedModelUnavailableError(
                f"fastembed embed() failed for model {self.model_name!r} "
                f"(offline={self._offline}): {exc}"
            ) from exc
        return self._normalize(vec)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = self._ensure_model()
        try:
            vecs = list(model.embed(texts))
        except Exception as exc:
            raise FastEmbedModelUnavailableError(
                f"fastembed embed() failed for model {self.model_name!r} "
                f"(offline={self._offline}): {exc}"
            ) from exc
        return np.stack([self._normalize(v) for v in vecs])


def get_embedder(
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    dim: int = DEFAULT_DIM,
    offline: bool = False,
    cache_dir: str | None = None,
) -> FastEmbedProvider:
    return FastEmbedProvider(model_name=model_name, dim=dim, offline=offline, cache_dir=cache_dir)


def fetch_model(
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    cache_dir: str | None = None,
) -> str:
    """``magicite fetch-model``: pre-download the ONNX model for offline use
    (spec §1 CLI surface). Deliberately *not* offline here -- fetching is
    the one legitimate network-touching operation, run once by a
    `pip install` user (or the Docker build) before flipping
    ``MAGICITE_EMBEDDING_OFFLINE=1`` for every subsequent boot."""
    from fastembed import TextEmbedding

    TextEmbedding(model_name=model_name, cache_dir=cache_dir)
    return model_name
