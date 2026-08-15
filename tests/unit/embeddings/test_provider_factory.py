"""CR-6: ``embeddings.get_embedder(cfg)`` is the single provider-selection
point ``mcp/app.py``/``__main__.py`` call through."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from magicite import embeddings as embeddings_pkg
from magicite.embeddings.cache import CachingEmbedder
from magicite.embeddings.hashing_provider import HashingEmbedder
from magicite.errors import InvalidInputError


@dataclass
class _FakeCfg:
    embedding_provider: str = "hashing"
    embedding_offline: bool = False
    embedding_dim: int = 128
    embedding_cache_size: int = 32
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "bge-m3"


def test_hashing_provider_never_wrapped_in_cache() -> None:
    embedder = embeddings_pkg.get_embedder(_FakeCfg(embedding_provider="hashing"))
    assert isinstance(embedder, HashingEmbedder)
    assert embedder.dim == 128


def test_fastembed_provider_wrapped_in_cache_by_default(monkeypatch) -> None:
    calls: list[dict] = []

    class _Stub:
        model_name = "BAAI/bge-small-en-v1.5"
        dim = 384

        def embed(self, docs):
            return [[0.0] * 384 for _ in docs]

    def fake_get_embedder(*, offline):
        calls.append({"offline": offline})
        return _Stub()

    monkeypatch.setattr(
        "magicite.embeddings.fastembed_provider.get_embedder", fake_get_embedder
    )
    embedder = embeddings_pkg.get_embedder(_FakeCfg(embedding_provider="fastembed", embedding_offline=True))
    assert isinstance(embedder, CachingEmbedder)
    assert calls == [{"offline": True}]


def test_fastembed_cache_disabled_when_requested(monkeypatch) -> None:
    class _Stub:
        model_name = "BAAI/bge-small-en-v1.5"
        dim = 384

    monkeypatch.setattr(
        "magicite.embeddings.fastembed_provider.get_embedder", lambda *, offline: _Stub()
    )
    embedder = embeddings_pkg.get_embedder(_FakeCfg(embedding_provider="fastembed"), cache=False)
    assert not isinstance(embedder, CachingEmbedder)


def test_ollama_provider_selected_and_wrapped(monkeypatch) -> None:
    class _Stub:
        model_name = "ollama:bge-m3"
        dim = 1024

    captured = {}

    def fake_get_embedder(*, model_name, host, dim):
        captured.update(model_name=model_name, host=host, dim=dim)
        return _Stub()

    monkeypatch.setattr("magicite.embeddings.ollama_provider.get_embedder", fake_get_embedder)
    embedder = embeddings_pkg.get_embedder(_FakeCfg(embedding_provider="ollama"))
    assert isinstance(embedder, CachingEmbedder)
    assert captured == {"model_name": "bge-m3", "host": "http://localhost:11434", "dim": 128}


def test_unknown_provider_raises_invalid_input() -> None:
    with pytest.raises(InvalidInputError):
        embeddings_pkg.get_embedder(_FakeCfg(embedding_provider="not-a-real-provider"))
