# S10 — Experimental shadow policy and reviewed promotion

## Assignment

Status: ready to assign subject to dependency gates. Priority: P1 experimental, optional for GA.
Build after: S01, S07, S09.
Merge after: S01, S07, S09.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a researcher, I want to test candidate policies without silently changing production ranking.

Dream mutates current node/edge strength at core/dream.py:246-294 and :349-368 (H). Existing aggregated lifecycle support is a retention-window approximation at core/lifecycle.py:153-174 (H).

## Scope and contract

C7 and evaluation.md. Optional for GA; S00 containment is mandatory. No claim of improved adaptation absent passing evidence. Reuse S07 policy_store for all activation; supply router/config patches to S07 and public binding patches to S11 as serialized follow-ups, without independently editing their files. If this slice is deferred, disable its commands/config and mark them unavailable, not partially implemented.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/policy_learning.py`
- `src/magicite/core/learning_transitions.py` (consumes S07 policy_store; no independent store)
- `src/magicite/core/dream.py`
- `tests/unit/core/test_policy_learning.py`
- `tests/integration/test_policy_promotion.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Build immutable candidate policy artifacts from partitioned evidence; do not write incumbent parameters.
2. Add shadow replay and OPE with overlap/ESS reporting; reject deterministic unsupported comparisons.
3. Use reviewed transition manifests and atomic active-pointer compare-and-swap; implement kill switch and exact rollback.
4. Add canary only after fixed/off-policy gates, with predeclared exposure and sequential-safe or fixed-sample rules.

## Acceptance Criteria

### AC-S10-01 (event-driven)
GIVEN a shadow candidate and a pinned incumbent
WHEN shadow scoring runs
THEN incumbent route results SHALL remain unchanged
VERIFY: new tests/unit/core/test_policy_learning.py::test_shadow_isolation

### AC-S10-02 (event-driven)
GIVEN logs with zero action support for candidate decisions
WHEN OPE runs
THEN the result SHALL be insufficient_support
VERIFY: new test_policy_learning.py::test_zero_support

### AC-S10-03 (event-driven)
GIVEN a promotion with stale approval digest
WHEN activation is attempted
THEN activation SHALL be rejected
VERIFY: new tests/integration/test_policy_promotion.py::test_stale_approval

### AC-S10-04 (event-driven)
GIVEN a canary crossing its frozen rollback rule
WHEN rollback executes
THEN the active policy SHALL equal the pinned prior incumbent
VERIFY: new test_policy_promotion.py::test_exact_rollback

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Atomic kill switch to prior nonadaptive incumbent. Retain rejected candidate and contrary result artifacts; never merge learned state across incompatible partitions.
