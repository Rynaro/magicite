"""S05 index generation integration tests (AC-S05-03, AC-S05-04)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from magicite.core.index_generation import (
    FingerprintConflictError,
    GenerationStore,
    IncompleteGenerationError,
    IndexFingerprint,
    StaleGenerationError,
    fts5_available,
    model_artifact_digest,
    project_artifact,
)
from magicite.embeddings.hashing_provider import get_embedder
from magicite.engram import parse_artifact

pytestmark = pytest.mark.skipif(not fts5_available(), reason="SQLite FTS5 unavailable")

_SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "engram-v1"
    / "positive"
    / "sample-host-tooling.egr.md"
)


def _fp(*, model_name: str = "hashing-64", dim: int = 64, digest: str | None = None) -> IndexFingerprint:
    return IndexFingerprint(
        provider="hashing",
        model_artifact_digest=digest or model_artifact_digest(model_name=model_name, dim=dim),
        dimension=dim,
        model_revision="test",
    )


def _sample_projection(*, intent_does: str | None = None):
    raw = _SAMPLE.read_text(encoding="utf-8")
    if intent_does is not None:
        # Frontmatter-only edit: change intent.does, keep body identical.
        raw = raw.replace(
            'does: "Prepare a host toolchain for Proton prefix repair"',
            f'does: "{intent_does}"',
            1,
        )
    artifact, _ = parse_artifact(raw, relpath="sample-host-tooling.egr.md")
    return project_artifact(artifact)


def test_fingerprint_invalidates(tmp_path: Path) -> None:
    """AC-S05-03: frontmatter-only edit or same-name model digest change rejects."""
    store = GenerationStore(tmp_path / "idx")
    embedder = get_embedder(dim=64)
    proj = _sample_projection()
    fp = _fp(model_name=embedder.model_name, dim=64)

    builder = store.begin(snapshot_id="snap-1", fingerprint=fp, model_name=embedder.model_name)
    builder.add_entry(proj, dense_vec=embedder.embed(proj.full_text))
    builder.finalize()
    store.publish(builder.generation_id)

    # Frontmatter-only edit → projection digest changes → validation rejects.
    edited = _sample_projection(intent_does="Prepare a DIFFERENT toolchain blurb")
    assert edited.projection_sha256 != proj.projection_sha256
    assert edited.body_text == proj.body_text
    with pytest.raises(StaleGenerationError, match="projection digest"):
        store.validate_against_snapshot(builder.generation_id, [edited], expected_fingerprint=fp)

    # Same model name, different artifact digest → conflict at registration.
    other_digest = model_artifact_digest(model_name=embedder.model_name, dim=64, extra={"rev": "b"})
    assert other_digest != fp.model_artifact_digest
    fp_changed = _fp(model_name=embedder.model_name, dim=64, digest=other_digest)
    with pytest.raises(FingerprintConflictError, match="different artifact digest"):
        store.begin(
            snapshot_id="snap-2",
            fingerprint=fp_changed,
            model_name=embedder.model_name,
            generation_id="gen_should_fail",
        )

    # validate_against_snapshot also rejects when expected fingerprint digest drifted.
    with pytest.raises(StaleGenerationError, match="fingerprint digest mismatch"):
        store.validate_against_snapshot(
            builder.generation_id,
            [proj],
            expected_fingerprint=fp_changed,
        )


def test_atomic_swap_and_rollback(tmp_path: Path) -> None:
    """AC-S05-04: partial builds invisible; only complete published gens are pin-able."""
    store = GenerationStore(tmp_path / "idx")
    embedder = get_embedder(dim=64)
    proj = _sample_projection()
    vec = embedder.embed(proj.full_text)
    fp = _fp(model_name=embedder.model_name, dim=64)

    # Generation A: complete + published.
    a = store.begin(snapshot_id="snap-a", fingerprint=fp, model_name=embedder.model_name)
    a.add_entry(proj, dense_vec=vec)
    a.finalize()
    store.publish(a.generation_id)
    assert store.active_generation_id() == a.generation_id
    pinned = store.pin()
    assert pinned.generation_id == a.generation_id
    assert pinned.status == "complete"

    # Generation B: partial (entries added, not finalized) — not visible to pin/publish.
    b = store.begin(snapshot_id="snap-b", fingerprint=fp, generation_id="gen_partial")
    b.add_entry(proj, dense_vec=np.asarray(vec, dtype=np.float32))
    assert store.active_generation_id() == a.generation_id
    with pytest.raises(IncompleteGenerationError, match="not complete"):
        store.pin("gen_partial")
    with pytest.raises(IncompleteGenerationError, match="incomplete"):
        store.publish("gen_partial")

    # Finalize B and atomically swap.
    b.finalize()
    store.publish(b.generation_id)
    assert store.active_generation_id() == b.generation_id
    assert store.previous_generation_id() == a.generation_id

    # Rollback to A.
    store.rollback(expected_current=b.generation_id, prior_generation_id=a.generation_id)
    assert store.active_generation_id() == a.generation_id
    assert store.pin().generation_id == a.generation_id
