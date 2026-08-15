"""``get_embedder(cfg)`` -- the one provider-selection point (CR-6).

``mcp/app.py``/``__main__.py`` call this instead of importing a specific
provider module directly, so ``Config.embedding_provider`` (and its
``MAGICITE_EMBEDDING_PROVIDER`` env override) is the single place that
decides which of ``hashing`` (deterministic, offline, tests-and-CI) /
``fastembed`` (default, ONNX, R4) / ``ollama`` (opt-in) actually runs.
"""

from __future__ import annotations

from magicite.embeddings.base import Embedder
from magicite.embeddings.cache import CachingEmbedder
from magicite.errors import InvalidInputError

#: provider name -> Config field it is honestly named after (for error messages only).
_KNOWN_PROVIDERS = ("hashing", "fastembed", "ollama")


def get_embedder(cfg: object, *, cache: bool = True) -> Embedder:
    """Resolve ``cfg.embedding_provider`` to a live :class:`Embedder`.

    ``cfg`` is typed loosely (``object``) rather than importing
    ``magicite.config.Config`` to avoid a spurious import-order dependency
    for callers that already have a config duck-type in hand; every field
    accessed here is read via ``getattr`` with the same default the real
    ``Config`` dataclass carries.
    """
    provider = str(getattr(cfg, "embedding_provider", "fastembed"))
    embedder: Embedder

    if provider == "hashing":
        from magicite.embeddings.hashing_provider import get_embedder as _hashing

        embedder = _hashing(dim=int(getattr(cfg, "embedding_dim", 256)))
        # The hashing provider is already sub-microsecond; caching it only
        # adds bookkeeping. Never wrap it.
        return embedder

    if provider == "fastembed":
        from magicite.embeddings.fastembed_provider import get_embedder as _fastembed

        embedder = _fastembed(offline=bool(getattr(cfg, "embedding_offline", False)))
    elif provider == "ollama":
        from magicite.embeddings.ollama_provider import get_embedder as _ollama

        embedder = _ollama(
            model_name=str(getattr(cfg, "ollama_model", "bge-m3")),
            host=str(getattr(cfg, "ollama_host", "http://localhost:11434")),
            dim=int(getattr(cfg, "embedding_dim", 256)),
        )
    else:
        raise InvalidInputError(
            f"unknown embedding provider {provider!r}",
            hint=f"MAGICITE_EMBEDDING_PROVIDER must be one of {_KNOWN_PROVIDERS}",
        )

    if cache:
        maxsize = int(getattr(cfg, "embedding_cache_size", 256))
        return CachingEmbedder(embedder, maxsize=maxsize)
    return embedder
