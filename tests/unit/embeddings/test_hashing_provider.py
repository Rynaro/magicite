from __future__ import annotations

import numpy as np

from magicite.embeddings.hashing_provider import get_embedder


def test_embed_is_deterministic() -> None:
    e1 = get_embedder(dim=128)
    e2 = get_embedder(dim=128)
    v1 = e1.embed("rollback proton for a steam game")
    v2 = e2.embed("rollback proton for a steam game")
    assert np.array_equal(v1, v2)


def test_embed_is_l2_normalised() -> None:
    e = get_embedder(dim=128)
    v = e.embed("some reasonably long piece of text to embed")
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_similar_text_scores_higher_than_unrelated() -> None:
    e = get_embedder(dim=256)
    query = e.embed("rollback proton for a steam game")
    close = e.embed("rollback ge-proton for steam after a regression")
    far = e.embed("bake a loaf of sourdough bread")
    assert float(np.dot(query, close)) > float(np.dot(query, far))


def test_empty_text_returns_zero_vector_without_error() -> None:
    e = get_embedder(dim=64)
    v = e.embed("")
    assert v.shape == (64,)
    assert float(np.linalg.norm(v)) == 0.0


def test_embed_batch_matches_embed_one_at_a_time() -> None:
    e = get_embedder(dim=64)
    texts = ["alpha beta", "gamma delta epsilon"]
    batch = e.embed_batch(texts)
    for i, t in enumerate(texts):
        assert np.array_equal(batch[i], e.embed(t))
