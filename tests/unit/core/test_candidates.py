"""S05 candidate generation unit tests (AC-S05-01, AC-S05-02)."""

from __future__ import annotations

import numpy as np
import pytest

from magicite.core.candidates import (
    CandidateConfig,
    RetrievalIndex,
    generate,
    indexed_entry_from_projection,
    reciprocal_rank_fuse,
)
from magicite.core.index_generation import (
    IndexFingerprint,
    fts5_available,
    model_artifact_digest,
    project_artifact,
)
from magicite.embeddings.hashing_provider import get_embedder

pytestmark = pytest.mark.skipif(not fts5_available(), reason="SQLite FTS5 unavailable")


def _fingerprint(dim: int = 64) -> IndexFingerprint:
    return IndexFingerprint(
        provider="hashing",
        model_artifact_digest=model_artifact_digest(model_name=f"hashing-{dim}", dim=dim),
        dimension=dim,
    )


def _projection(
    *,
    engram_id: str,
    name: str,
    body_text: str,
    intent_does: str = "generic helper",
    intent_use_when: str = "generic cases",
    positive: list[str] | None = None,
    revision: int = 1,
):
    return project_artifact(
        {
            "frontmatter": {
                "id": engram_id,
                "name": name,
                "version": revision,
                "intent": {"does": intent_does, "use_when": intent_use_when, "not_when": None},
                "routing": {"positive": positive or [], "negative": [], "body_digest": "0" * 64},
            },
            "body_text": body_text,
        }
    )


def test_full_body_exact_terms() -> None:
    """AC-S05-01: raw prose / exact error token distinguishes the match."""
    embedder = get_embedder(dim=64)
    needle = "E_PROTON_PREFIX_CORRUPT"
    prose_hit = _projection(
        engram_id="egr_aaa11111",
        name="prefix-repair",
        body_text=(
            "## Pitfalls\n"
            f"- When the host reports {needle}, wipe the local prefix cache before retrying.\n"
            "- Do not skip the digest check."
        ),
        intent_does="repair wine prefixes",
        intent_use_when="prefix looks broken",
    )
    prose_miss = _projection(
        engram_id="egr_bbb22222",
        name="prefix-inspect",
        body_text=(
            "## Pitfalls\n"
            "- When the host reports a generic failure, collect logs only.\n"
            "- Do not mutate the prefix."
        ),
        intent_does="repair wine prefixes",
        intent_use_when="prefix looks broken",
    )
    # Numbered-steps-only twin: same intent, no distinguishing prose token.
    steps_only = _projection(
        engram_id="egr_ccc33333",
        name="prefix-steps",
        body_text="## Procedure\n1. Inspect the prefix.\n2. Retry the launch.",
        intent_does="repair wine prefixes",
        intent_use_when="prefix looks broken",
    )

    entries = {}
    for proj in (prose_hit, prose_miss, steps_only):
        vec = embedder.embed(proj.full_text)
        entries[proj.engram_id] = indexed_entry_from_projection(proj, dense_vec=vec)

    index = RetrievalIndex.build_in_memory(
        generation_id="gen_test_body",
        snapshot_id="snap_test_body",
        fingerprint=_fingerprint(64),
        entries=entries,
    )
    try:
        batch = generate(
            f"host reports {needle}",
            index,
            CandidateConfig(sources=("sparse", "trigger", "dense"), top_k=3),
            embedder=embedder,
            eligible_ids=frozenset(entries),
        )
        assert batch.candidates, "expected at least one candidate"
        assert batch.candidates[0].id == "egr_aaa11111"
        # Sparse component must surface the exact-token hit.
        sparse_ids = [h.engram_id for h in batch.components["sparse"]]
        assert "egr_aaa11111" in sparse_ids
    finally:
        index.close()


def test_rrf_ties() -> None:
    """AC-S05-02: equal component ranks → stable ID ordering across repeats."""
    lists = {
        "dense": ["egr_bbbbbbbb", "egr_aaaaaaaa"],
        "sparse": ["egr_aaaaaaaa", "egr_bbbbbbbb"],
    }
    # Both IDs get 1/(60+1) + 1/(60+2) = identical fused scores.
    first = reciprocal_rank_fuse(lists, k=60)
    second = reciprocal_rank_fuse(lists, k=60)
    assert first == second
    scores = {eid: score for eid, score in first}
    assert scores["egr_aaaaaaaa"] == pytest.approx(scores["egr_bbbbbbbb"])
    # Tie break: lexicographically smaller ID first when scores equal.
    assert [eid for eid, _ in first] == ["egr_aaaaaaaa", "egr_bbbbbbbb"]

    # Build two artifacts whose dense/sparse ranks can be forced equal via
    # identical component lists fed through generate's fusion path.
    a = _projection(
        engram_id="egr_aaaaaaaa",
        name="alpha-skill",
        body_text="shared token ZZZ_SHARED_TOKEN appears here for both",
        positive=["ZZZ_SHARED_TOKEN"],
    )
    b = _projection(
        engram_id="egr_bbbbbbbb",
        name="beta-skill",
        body_text="shared token ZZZ_SHARED_TOKEN appears here for both",
        positive=["ZZZ_SHARED_TOKEN"],
    )
    # Identical dense vectors → equal dense ranks after ID sort of scores.
    shared_vec = np.ones(64, dtype=np.float32)
    shared_vec /= float(np.linalg.norm(shared_vec))
    entries = {
        a.engram_id: indexed_entry_from_projection(a, dense_vec=shared_vec.copy()),
        b.engram_id: indexed_entry_from_projection(b, dense_vec=shared_vec.copy()),
    }
    index = RetrievalIndex.build_in_memory(
        generation_id="gen_ties",
        snapshot_id="snap_ties",
        fingerprint=_fingerprint(64),
        entries=entries,
    )
    try:
        cfg = CandidateConfig(sources=("dense", "sparse", "trigger"), top_k=10)
        batches = [
            generate("ZZZ_SHARED_TOKEN", index, cfg, query_vec=shared_vec, eligible_ids=frozenset(entries))
            for _ in range(5)
        ]
        orderings = [[c.id for c in batch.candidates] for batch in batches]
        assert all(o == orderings[0] for o in orderings)
        assert orderings[0][0] == "egr_aaaaaaaa"
    finally:
        index.close()
