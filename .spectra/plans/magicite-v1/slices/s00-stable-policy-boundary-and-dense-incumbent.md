# S00 — Stable policy boundary and dense incumbent

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: none; start immediately.
Merge after: none.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As an operator, I want a dependable nonadaptive default whose answers do not change with popularity or Dream runs.

Production blends dense, graph and plasticity unconditionally at src/magicite/core/router.py:399-459 and :488-493; config.py:136-139 and :247 enable that blend/community behavior (H). Dream updates strengths at core/dream.py:246-294 (H).

## Scope and contract

C0, C4, C7. Default dense-v1 uses eligible cosine similarity and stable ID ties. Preserve legacy adaptive behavior only behind an explicit experimental policy selection with visible diagnostics. Do not delete historical state or remove research baselines.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/router.py`
- `src/magicite/config.py`
- `src/magicite/core/dream.py`
- `tests/unit/core/test_router*.py`
- `tests/unit/core/test_dream*.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Introduce explicit policy identity and stable/experimental dispatch with dense-v1 as the default.
2. Separate stable scoring inputs from historical node/edge state; isolate Dream writes from active stable policy semantics.
3. Stop raw route-query logging immediately; S09 later provides receipt persistence and historical cleanup.
4. Produce a policy-selection ADR and fixed-fixture parity tests; hand router/config ownership to S07 after merge.

## Acceptance Criteria

### AC-S00-01 (event-driven)
GIVEN an identical pinned query and registry
WHEN usage, strength and community state vary
THEN stable routing SHALL return the same semantic result
VERIFY: new tests/unit/core/test_policy_boundary.py::test_stable_ignores_adaptation

### AC-S00-02 (event-driven)
GIVEN stable default configuration
WHEN Dream runs or checkpoints historical strength
THEN the active stable policy digest SHALL remain unchanged
VERIFY: new test_policy_boundary.py::test_dream_cannot_change_stable_policy

### AC-S00-03 (event-driven)
GIVEN an explicit experimental policy selection
WHEN routing completes
THEN the decision SHALL identify the experimental policy
VERIFY: new test_policy_boundary.py::test_experimental_is_explicit

### AC-S00-04 (event-driven)
GIVEN a query containing a secret sentinel
WHEN route is invoked
THEN persistent route events SHALL omit the raw query
VERIFY: new test_policy_boundary.py::test_no_raw_query_logging

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Reselect the pinned dense incumbent; retain historical learned state without applying it. A release that restores implicit adaptation is not an acceptable rollback.
