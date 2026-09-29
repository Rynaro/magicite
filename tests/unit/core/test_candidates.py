"""S05 candidate generation unit tests (AC-S05-01, AC-S05-02 + review fixes)."""

from __future__ import annotations

import numpy as np
import pytest

from magicite.core.candidates import (
    CandidateConfig,
    RetrievalIndex,
    build_fts5_query,
    dense_candidates,
    generate,
    indexed_entry_from_projection,
    reciprocal_rank_fuse,
    sparse_candidates,
)
from magicite.core.index_generation import (
    IndexFingerprint,
    fts5_available,
    model_artifact_digest,
    project_artifact,
)
from magicite.embeddings.hashing_provider import get_embedder
from magicite.engram.digests import routing_body_digest

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
    body_digest: str | None = None,
):
    digest = body_digest or routing_body_digest(body_text)
    return project_artifact(
        {
            "frontmatter": {
                "id": engram_id,
                "name": name,
                "version": revision,
                "intent": {"does": intent_does, "use_when": intent_use_when, "not_when": None},
                "routing": {
                    "positive": positive or [],
                    "negative": [],
                    "body_digest": digest,
                },
            },
            "body_text": body_text,
            "raw_body_text": body_text,
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
        sparse_ids = [h.engram_id for h in batch.components["sparse"]]
        assert "egr_aaa11111" in sparse_ids
    finally:
        index.close()


def test_body_digest_uses_routing_body_digest_not_projection() -> None:
    """BLOCKER: Candidate/IndexedEntry body_digest is normative routing.body_digest."""
    body = "## Procedure\n1. do the thing with E_FOO token\n"
    proj = _projection(engram_id="egr_dddddddd", name="digest-skill", body_text=body)
    assert proj.body_digest == routing_body_digest(body)
    assert proj.body_digest != proj.projection_sha256
    entry = indexed_entry_from_projection(proj, dense_vec=np.ones(64, dtype=np.float32))
    assert entry.resolved_body_digest() == proj.body_digest
    assert entry.resolved_body_digest() != proj.projection_sha256

    index = RetrievalIndex.build_in_memory(
        generation_id="gen_digest",
        snapshot_id="snap_digest",
        fingerprint=_fingerprint(64),
        entries={proj.engram_id: entry},
    )
    try:
        batch = generate(
            "E_FOO",
            index,
            CandidateConfig(sources=("sparse",), top_k=1),
            eligible_ids=frozenset({proj.engram_id}),
        )
        assert batch.candidates[0].body_digest == proj.body_digest
        assert batch.candidates[0].projection_sha256 == proj.projection_sha256
    finally:
        index.close()


def test_rrf_ties() -> None:
    """AC-S05-02: equal component ranks → stable ID ordering across repeats."""
    lists = {
        "dense": ["egr_bbbbbbbb", "egr_aaaaaaaa"],
        "sparse": ["egr_aaaaaaaa", "egr_bbbbbbbb"],
    }
    first = reciprocal_rank_fuse(lists, k=60)
    second = reciprocal_rank_fuse(lists, k=60)
    assert first == second
    scores = {eid: score for eid, score in first}
    assert scores["egr_aaaaaaaa"] == pytest.approx(scores["egr_bbbbbbbb"])
    assert [eid for eid, _ in first] == ["egr_aaaaaaaa", "egr_bbbbbbbb"]

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


def test_eligibility_mask_refill_skips_ineligible() -> None:
    """MAJOR: buried eligible hits refill; ineligible never appear in components."""
    # Build many ineligible docs that match the query strongly, then one eligible.
    entries: dict = {}
    for i in range(40):
        eid = f"egr_{i:08x}"
        body = f"UNIQUE_REFILL_TOKEN noise document number {i}"
        proj = _projection(engram_id=eid, name=f"noise-{i}", body_text=body)
        # Higher dense score for lower i via larger magnitude before norm — use
        # distinct vectors biased by index so ineligible rank above eligible.
        vec = np.zeros(64, dtype=np.float32)
        vec[0] = 1.0 + (40 - i) * 0.01
        vec = vec / float(np.linalg.norm(vec))
        entries[eid] = indexed_entry_from_projection(proj, dense_vec=vec)

    eligible_id = "egr_00000027"  # buried among ineligible (i=39)
    eligible_ids = frozenset({eligible_id})
    index = RetrievalIndex.build_in_memory(
        generation_id="gen_mask",
        snapshot_id="snap_mask",
        fingerprint=_fingerprint(64),
        entries=entries,
    )
    try:
        qvec = entries[eligible_id].dense_vec
        assert qvec is not None
        dense_hits, _ = dense_candidates(
            qvec,
            index,
            limit=5,
            eligible_ids=eligible_ids,
            scan_budget=100,
        )
        assert [h.engram_id for h in dense_hits] == [eligible_id]
        sparse_hits, _ = sparse_candidates(
            "UNIQUE_REFILL_TOKEN",
            index,
            limit=5,
            eligible_ids=eligible_ids,
            scan_budget=100,
        )
        assert sparse_hits
        assert all(h.engram_id == eligible_id for h in sparse_hits)

        batch = generate(
            "UNIQUE_REFILL_TOKEN",
            index,
            CandidateConfig(sources=("dense", "sparse", "trigger"), top_k=5, scan_budget=100),
            query_vec=qvec,
            eligible_ids=eligible_ids,
        )
        for source, hits in batch.components.items():
            leaked = [h.engram_id for h in hits if h.engram_id not in eligible_ids]
            assert not leaked, f"{source} leaked ineligible ids: {leaked}"
        assert all(c.id in eligible_ids for c in batch.candidates)
        assert batch.candidates[0].id == eligible_id
    finally:
        index.close()


@pytest.mark.parametrize(
    ("query", "expected_substr"),
    [
        ("E_FOO*", '"E_FOO"*'),
        ("NEAR", '"NEAR"'),
        ('hello "world"', '"hello" AND "world"'),
        ("body:title", '"body:title"'),
        ("AND OR NOT", '"AND" AND "OR" AND "NOT"'),
        ("foo\x00bar", '"foo" AND "bar"'),
        ('say "hi"', '"say" AND "hi"'),
    ],
)
def test_fts5_query_escaping(query: str, expected_substr: str) -> None:
    """MAJOR/MINOR: prefix stars, operators, quotes, NUL, column filters escaped."""
    built = build_fts5_query(query)
    assert built is not None
    assert expected_substr in built or built == expected_substr
    # Prefix star must survive outside the quotes.
    if query.rstrip("\x00").endswith("*") and query.strip() not in {"*", ""}:
        assert built.endswith("*")
        assert '"*"' not in built


@pytest.mark.parametrize("query", ["*", "   *  ", "", "   ", "\x00", "**"])
def test_fts5_operator_only_query_yields_no_match(query: str) -> None:
    """Bare/empty/operator-only queries must not produce a MATCH expression."""
    assert build_fts5_query(query) is None


def test_sparse_bare_star_returns_no_candidates() -> None:
    """Sparse path: bare ``*`` → no MATCH → empty candidate list (not ``\"\"*``)."""
    proj = _projection(
        engram_id="egr_eeeeeeee",
        name="star-noise",
        body_text="something searchable UNIQUE_STAR_BODY",
    )
    entries = {proj.engram_id: indexed_entry_from_projection(proj, dense_vec=np.ones(64, dtype=np.float32))}
    index = RetrievalIndex.build_in_memory(
        generation_id="gen_star",
        snapshot_id="snap_star",
        fingerprint=_fingerprint(64),
        entries=entries,
    )
    try:
        hits, trunc = sparse_candidates("*", index, limit=5, scan_budget=50)
        assert hits == []
        assert trunc == 0
    finally:
        index.close()
