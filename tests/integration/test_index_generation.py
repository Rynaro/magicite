"""S05 index generation integration tests (AC-S05-03, AC-S05-04 + review fixes)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from magicite.core.index_generation import (
    FingerprintConflictError,
    GenerationStore,
    IncompleteGenerationError,
    IndexCatalog,
    IndexFingerprint,
    StaleGenerationError,
    fts5_available,
    model_artifact_digest,
    project_artifact,
    write_bytes_atomic,
    write_text_atomic,
)
from magicite.embeddings.hashing_provider import get_embedder
from magicite.engram import parse_artifact
from magicite.engram.digests import routing_body_digest
from magicite.engram.parser import split_frontmatter
from magicite.storage import db as db_mod
from magicite.storage import lease as lease_mod

pytestmark = pytest.mark.skipif(not fts5_available(), reason="SQLite FTS5 unavailable")

_SAMPLE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "engram-v1" / "positive" / "sample-host-tooling.egr.md"
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
        raw = raw.replace(
            'does: "Prepare a host toolchain for Proton prefix repair"',
            f'does: "{intent_does}"',
            1,
        )
    artifact, _ = parse_artifact(raw, relpath="sample-host-tooling.egr.md")
    _, body = split_frontmatter(raw)
    return project_artifact(artifact, raw_body_text=body)


def test_fingerprint_invalidates(tmp_path: Path) -> None:
    """AC-S05-03: frontmatter-only edit or same-name model digest change rejects."""
    conn = db_mod.connect(tmp_path / "idx.db")
    catalog = IndexCatalog(conn, model_registry_path=tmp_path / "models.json")
    embedder = get_embedder(dim=64)
    proj = _sample_projection()
    fp = _fp(model_name=embedder.model_name, dim=64)

    with lease_mod.writer_lease("s05-fingerprint"):
        gid = catalog.begin(snapshot_id="snap-1", fingerprint=fp, model_name=embedder.model_name)
        catalog.add_entry(gid, proj, dense_vec=embedder.embed(proj.full_text))
        catalog.complete(gid, [proj], expected_fingerprint=fp)
        catalog.publish(gid)

    edited = _sample_projection(intent_does="Prepare a DIFFERENT toolchain blurb")
    assert edited.projection_sha256 != proj.projection_sha256
    # Body bytes unchanged → normative routing.body_digest stable.
    assert edited.body_digest == proj.body_digest
    with pytest.raises(StaleGenerationError, match="projection digest"):
        catalog.validate_against_snapshot(gid, [edited], expected_fingerprint=fp)

    other_digest = model_artifact_digest(model_name=embedder.model_name, dim=64, extra={"rev": "b"})
    assert other_digest != fp.model_artifact_digest
    fp_changed = _fp(model_name=embedder.model_name, dim=64, digest=other_digest)
    with (
        lease_mod.writer_lease("s05-fingerprint-conflict"),
        pytest.raises(FingerprintConflictError, match="different artifact digest"),
    ):
        catalog.begin(
            snapshot_id="snap-2",
            fingerprint=fp_changed,
            model_name=embedder.model_name,
            generation_id="gen_should_fail",
        )

    with pytest.raises(StaleGenerationError, match="fingerprint digest mismatch"):
        catalog.validate_against_snapshot(gid, [proj], expected_fingerprint=fp_changed)

    conn.close()


def test_atomic_swap_and_rollback(tmp_path: Path) -> None:
    """AC-S05-04: partial builds invisible; only complete published gens are pin-able."""
    conn = db_mod.connect(tmp_path / "idx.db")
    catalog = IndexCatalog(conn, model_registry_path=tmp_path / "models.json")
    sidecar = GenerationStore(tmp_path / "sidecar")
    embedder = get_embedder(dim=64)
    proj = _sample_projection()
    vec = embedder.embed(proj.full_text)
    fp = _fp(model_name=embedder.model_name, dim=64)

    with lease_mod.writer_lease("s05-swap"):
        a = catalog.begin(snapshot_id="snap-a", fingerprint=fp, model_name=embedder.model_name)
        catalog.add_entry(a, proj, dense_vec=vec)
        catalog.complete(a, [proj], expected_fingerprint=fp)
        catalog.publish(a)
        sidecar.write_sidecar(
            a,
            snapshot_id="snap-a",
            fingerprint=fp,
            entries=catalog.pin(a).entries,
        )
        sidecar.mark_complete(a)

    assert catalog.active_generation_id() == a
    pinned = catalog.pin()
    assert pinned.generation_id == a
    assert pinned.status == "published"
    assert pinned.entries[proj.engram_id].resolved_body_digest() == proj.body_digest
    assert proj.body_digest == routing_body_digest(split_frontmatter(_SAMPLE.read_text())[1])

    with lease_mod.writer_lease("s05-swap-b"):
        b = catalog.begin(snapshot_id="snap-b", fingerprint=fp, generation_id="gen_partial")
        catalog.add_entry(b, proj, dense_vec=np.asarray(vec, dtype=np.float32))

    assert catalog.active_generation_id() == a
    with pytest.raises(IncompleteGenerationError, match="not complete"):
        catalog.pin("gen_partial")
    with lease_mod.writer_lease("s05-swap-b-pub"), pytest.raises(Exception, match="building|complete"):
        catalog.publish("gen_partial")

    with lease_mod.writer_lease("s05-swap-b-finish"):
        catalog.complete(b, [proj], expected_fingerprint=fp)
        catalog.publish(b)
        sidecar.write_sidecar(
            b,
            snapshot_id="snap-b",
            fingerprint=fp,
            entries=catalog.pin(b).entries,
        )
        sidecar.mark_complete(b)

    assert catalog.active_generation_id() == b
    assert catalog.previous_generation_id() == a

    with lease_mod.writer_lease("s05-swap-rollback"):
        catalog.rollback()
    assert catalog.active_generation_id() == a
    assert catalog.pin().generation_id == a
    conn.close()


def test_write_atomic_fsyncs_file_and_parent(tmp_path: Path) -> None:
    """MAJOR: atomic writes fsync content and parent directory."""
    target = tmp_path / "dir" / "marker.txt"
    synced_fds: list[int] = []
    real_fsync = os.fsync

    def tracking_fsync(fd: int) -> None:
        synced_fds.append(fd)
        real_fsync(fd)

    with mock.patch("magicite.core.index_generation.os.fsync", side_effect=tracking_fsync):
        write_text_atomic(target, "ok\n")
        write_bytes_atomic(tmp_path / "dir" / "blob.bin", b"abc")

    assert target.read_text(encoding="utf-8") == "ok\n"
    # At least one fsync per write (file) plus parent dir fsync.
    assert len(synced_fds) >= 4
