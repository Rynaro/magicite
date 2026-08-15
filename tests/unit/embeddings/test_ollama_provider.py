"""ollama_provider.py unit tests -- always with an injected ``poster``
stub, never a real HTTP call: opt-in provider, no daemon running in CI."""

from __future__ import annotations

import numpy as np
import pytest

from magicite.embeddings import ollama_provider as ollama_mod


def _stub_poster(calls: list[tuple[str, dict]], response: dict):
    def poster(url: str, payload: dict) -> dict:
        calls.append((url, payload))
        return response

    return poster


def test_model_name_is_prefixed_for_provenance() -> None:
    provider = ollama_mod.OllamaProvider(model_name="bge-m3", dim=4, poster=_stub_poster([], {}))
    assert provider.model_name == "ollama:bge-m3"


def test_embed_posts_to_api_embed_with_model_and_input() -> None:
    calls: list[tuple[str, dict]] = []
    poster = _stub_poster(calls, {"embeddings": [[1.0, 0.0, 0.0, 0.0]]})
    provider = ollama_mod.OllamaProvider(model_name="bge-m3", dim=4, host="http://x:1234", poster=poster)

    vec = provider.embed("hello")

    assert calls[0][0] == "http://x:1234/api/embed"
    assert calls[0][1] == {"model": "bge-m3", "input": ["hello"]}
    assert vec.shape == (4,)
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-6


def test_embed_batch_normalizes_every_row() -> None:
    poster = _stub_poster([], {"embeddings": [[1, 0, 0, 0], [0, 2, 0, 0], [0, 0, 0, 3]]})
    provider = ollama_mod.OllamaProvider(dim=4, poster=poster)
    out = provider.embed_batch(["a", "b", "c"])
    assert out.shape == (3, 4)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)


def test_embed_batch_empty_never_posts() -> None:
    calls: list[tuple[str, dict]] = []
    provider = ollama_mod.OllamaProvider(dim=4, poster=_stub_poster(calls, {}))
    out = provider.embed_batch([])
    assert out.shape == (0, 4)
    assert calls == []


def test_mismatched_embeddings_count_raises() -> None:
    poster = _stub_poster([], {"embeddings": [[1, 0, 0, 0]]})  # only 1 for 2 inputs
    provider = ollama_mod.OllamaProvider(dim=4, poster=poster)
    with pytest.raises(ollama_mod.OllamaUnavailableError):
        provider.embed_batch(["a", "b"])


def test_missing_embeddings_key_raises() -> None:
    poster = _stub_poster([], {"error": "no such model"})
    provider = ollama_mod.OllamaProvider(dim=4, poster=poster)
    with pytest.raises(ollama_mod.OllamaUnavailableError):
        provider.embed("hi")


def test_default_poster_unreachable_host_raises_typed_error() -> None:
    provider = ollama_mod.OllamaProvider(dim=4, host="http://127.0.0.1:1")
    with pytest.raises(ollama_mod.OllamaUnavailableError):
        provider.embed("hi")
