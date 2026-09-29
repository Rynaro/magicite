# S05 — Full-text indexes and hybrid candidates

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S02.
Merge after: S02, S03.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a user, I want exact identifiers and full procedure content to contribute to retrieval under a reproducible model.

Current dense scan is router.py:357-384 (H). registry.py:89-104 embeds numbered steps but omits raw prose/name/pitfalls/examples (H). source_sha256 covers body while projection includes frontmatter at registry.py:220-245 (H).

## Scope and contract

C3, C11. Pure generator and generation builder; S07 integrates router, S03 integrates durable pointers/migrations. Default RRF k=60, max 100 per source, ID tie break. Reranker provider seam only; no mandatory model acquisition.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/candidates.py`
- `src/magicite/core/index_generation.py`
- `src/magicite/embeddings/*`
- `tests/unit/core/test_candidates.py`
- `tests/integration/test_index_generation.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Create full-content projection including preserved prose and exact symbols, with deterministic bounded chunking.
2. Build dense/sparse/trigger candidate providers over immutable generation metadata; expose component scores.
3. Implement eligibility-mask filtering/refill and bounded RRF union independent of route orchestration.
4. Add staged model/index generation validation and atomic pointer API; reject model-name reuse with changed digest.

## Acceptance Criteria

### AC-S05-01 (event-driven)
GIVEN fixtures whose only distinguishing text is raw prose or an exact error token
WHEN candidate generation runs
THEN the matching artifact SHALL be retrieved
VERIFY: new tests/unit/core/test_candidates.py::test_full_body_exact_terms

### AC-S05-02 (event-driven)
GIVEN equal component ranks and pinned inputs
WHEN fusion repeats
THEN candidate ordering SHALL be stable by ID
VERIFY: new test_candidates.py::test_rrf_ties

### AC-S05-03 (event-driven)
GIVEN a frontmatter-only edit or same-name model artifact change
WHEN index validation runs
THEN the stale generation SHALL be rejected
VERIFY: new tests/integration/test_index_generation.py::test_fingerprint_invalidates

### AC-S05-04 (event-driven)
GIVEN a partially built generation and a failed build
WHEN routing pins an index
THEN only a complete published generation SHALL be visible
VERIFY: new test_index_generation.py::test_atomic_swap_and_rollback

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Atomically select the previous complete generation. A dense-only fallback must identify its policy and degraded sparse capability; never mix vectors across fingerprints.
