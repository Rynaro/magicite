"""fastembed_provider.py unit tests -- always against an injected stub
factory (never the real ``fastembed.TextEmbedding``): this repo's whole
test suite is CR-6-offline (tests/conftest.py), and R4 explicitly forbids
a network call at import/serve time, so these tests prove the *wiring*
(deferred construction, ``local_files_only``, normalization, error
propagation) without ever downloading or loading the real ~130MB ONNX
model.

The deferred-construction tests below are the regression coverage for a
real bug this module used to have: passing ``lazy_load=True`` to
``fastembed.TextEmbedding`` does NOT defer the network/cache-touching
``download_model()`` call (only the ONNX session load) -- see
fastembed_provider.py's module docstring for the full story. Construction
must make *zero* factory calls; only the first ``embed()``/``embed_batch()``
may.
"""

from __future__ import annotations

import numpy as np
import pytest

from magicite.embeddings import fastembed_provider as fe_mod


class _StubTextEmbedding:
    def __init__(self, *, dim: int = 4, fail: bool = False) -> None:
        self._dim = dim
        self._fail = fail
        self.embed_calls: list[list[str]] = []

    def embed(self, documents):
        if self._fail:
            raise RuntimeError("stub failure")
        docs = list(documents)
        self.embed_calls.append(docs)
        # deterministic, not-yet-normalised vectors so _normalize is exercised.
        return [np.full(self._dim, float(len(d) + 1)) for d in docs]


def _stub_factory(*, calls: list[dict], embedding=None, **kwargs):
    calls.append(kwargs)
    return embedding or _StubTextEmbedding()


def test_construction_never_calls_the_factory() -> None:
    """R4 (the correction): construction must be network/disk-free even
    though the underlying fastembed dependency's own ``lazy_load`` kwarg
    does not provide that guarantee -- this provider enforces it itself
    by not calling ``factory(...)`` until the first embed."""
    calls: list[dict] = []
    fe_mod.FastEmbedProvider(factory=lambda **kw: _stub_factory(calls=calls, **kw))
    assert calls == []


def test_first_embed_call_constructs_the_model_exactly_once() -> None:
    calls: list[dict] = []
    provider = fe_mod.FastEmbedProvider(
        dim=4, factory=lambda **kw: _stub_factory(calls=calls, **kw)
    )
    assert calls == []
    provider.embed("hi")
    assert len(calls) == 1
    provider.embed("again")
    provider.embed_batch(["a", "b"])
    assert len(calls) == 1  # memoized -- never re-constructed


def test_first_embed_passes_offline_flag_as_local_files_only() -> None:
    calls: list[dict] = []
    provider = fe_mod.FastEmbedProvider(
        offline=True, dim=4, factory=lambda **kw: _stub_factory(calls=calls, **kw)
    )
    provider.embed("hi")
    assert calls[0]["local_files_only"] is True


def test_offline_false_by_default() -> None:
    calls: list[dict] = []
    provider = fe_mod.FastEmbedProvider(dim=4, factory=lambda **kw: _stub_factory(calls=calls, **kw))
    provider.embed("hi")
    assert calls[0]["local_files_only"] is False


def test_model_name_and_dim_default_to_bge_small() -> None:
    provider = fe_mod.FastEmbedProvider(factory=lambda **kw: _StubTextEmbedding())
    assert provider.model_name == "BAAI/bge-small-en-v1.5"
    assert provider.dim == 384


def test_embed_returns_l2_normalized_vector() -> None:
    stub = _StubTextEmbedding(dim=4)
    provider = fe_mod.FastEmbedProvider(dim=4, factory=lambda **kw: stub)
    vec = provider.embed("hi")
    assert vec.shape == (4,)
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-6
    assert stub.embed_calls == [["hi"]]


def test_embed_batch_empty_never_constructs_the_model() -> None:
    calls: list[dict] = []
    provider = fe_mod.FastEmbedProvider(
        dim=4, factory=lambda **kw: _stub_factory(calls=calls, **kw)
    )
    out = provider.embed_batch([])
    assert out.shape == (0, 4)
    assert calls == []


def test_embed_batch_normalizes_every_row() -> None:
    provider = fe_mod.FastEmbedProvider(dim=4, factory=lambda **kw: _StubTextEmbedding(dim=4))
    out = provider.embed_batch(["a", "bb", "ccc"])
    assert out.shape == (3, 4)
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms, 1.0)


def test_construction_failure_raises_typed_error_on_first_use() -> None:
    def failing_factory(**kwargs):
        raise RuntimeError("model unavailable")

    provider = fe_mod.FastEmbedProvider(factory=failing_factory)
    with pytest.raises(fe_mod.FastEmbedModelUnavailableError):
        provider.embed("hi")


def test_embed_failure_raises_typed_error_not_swallowed() -> None:
    provider = fe_mod.FastEmbedProvider(factory=lambda **kw: _StubTextEmbedding(fail=True))
    with pytest.raises(fe_mod.FastEmbedModelUnavailableError):
        provider.embed("hi")
