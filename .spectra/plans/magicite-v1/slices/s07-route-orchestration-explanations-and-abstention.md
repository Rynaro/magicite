# S07 — Route orchestration, explanations and abstention

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S00, S01, S04, S05, S06.
Merge after: S00, S01, S04, S05, S06.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a user, I want a useful selection or an honest abstention with reproducible reasons.

Router always returns ranked top-k when rows exist at router.py:485-523; composition order/confidence only at :525-538 (H). Existing pure pipeline and binding are reusable at router.py:110-127 and mcp/bind_retrieval.py:38-72 (H).

## Scope and contract

C2–C4, C11 and evaluation.md. Own final router/config integration; S11 owns public wrappers. Separate selection quality from operational errors. No calibrated confidence without compatible calibration evidence; the frozen strongest simple policy remains default if hybrid fails promotion. S07 owns the mandatory durable policy_store API, reviewed compare-and-swap activation and exact rollback even when S10 is absent.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/router.py`
- `src/magicite/core/calibration.py`
- `src/magicite/core/policy_store.py`
- `src/magicite/config.py`
- `tests/unit/core/test_router*.py`
- `tests/integration/test_route_end_to_end.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Wire snapshot pinning, eligibility, candidate refill, fusion and optional reranker fallback.
2. Implement RouteDecision/1 including component scores, bounded exclusions and complete identity fingerprints.
3. Fit abstention thresholds/margins only on calibration split; store calibration artifact and data provenance.
4. Run preregistered paired comparisons; deliver selected incumbent manifest or retain the frozen strongest simple incumbent with explicit failed/inconclusive evidence.

## Acceptance Criteria

### AC-S07-01 (event-driven)
GIVEN a quarantined artifact with strongest raw score
WHEN routing runs
THEN the artifact SHALL be absent from returned candidates and bodies
VERIFY: extend tests/integration/test_route_end_to_end.py::test_eligibility_before_rerank

### AC-S07-02 (event-driven)
GIVEN an unrelated query in the locked rejection set
WHEN calibrated routing runs
THEN the result SHALL obey the frozen abstention decision rule
VERIFY: new tests/unit/core/test_calibration.py::test_threshold_manifest

### AC-S07-03 (event-driven)
GIVEN pinned semantic inputs
WHEN routing repeats across rebuild
THEN semantic decision fields SHALL match
VERIFY: new tests/integration/test_route_reproducibility.py::test_rebuild_determinism

### AC-S07-04 (event-driven)
GIVEN a reranker timeout or missing required model
WHEN routing runs
THEN the result SHALL identify configured fallback or explicit operational error
VERIFY: new tests/unit/core/test_router_policy.py::test_fallback_identity

### AC-S07-05 (event-driven)
GIVEN S10 is not installed and reviewed simple/hybrid policy artifacts exist
WHEN activation and rollback use the expected-current API
THEN the active digest SHALL follow exactly the approved compare-and-swap transitions
VERIFY: new tests/integration/test_stable_policy_activation.py::test_activation_without_learning including stale-current rejection

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Restore the previous approved simple incumbent with its matching index/calibration/config manifest. Clear incompatible calibrations rather than reporting stale probabilities.
