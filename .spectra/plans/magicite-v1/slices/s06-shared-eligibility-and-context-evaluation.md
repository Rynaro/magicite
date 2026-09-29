# S06 — Shared eligibility and context evaluation

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S02.
Merge after: S02, S04.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a host, I want inapplicable or unauthorized skills excluded before they can influence ranking or plans.

Current context is boosts/exclusions/preferences at router.py:245-303 and :467-497, not compatibility filtering (H). Composition admits resolved dependencies at composition.py:100-114 without eligibility checks (H).

## Scope and contract

C2. Implement one pure evaluator consumed by routing, planner and body loading. Missing host/environment requirements fail closed; missing artifact prerequisites are reported for composition, not excluded before ranking. It receives a trust-decision projection from S04; construction can use contract fixtures before S04 merges. Explicit user exclusions are hard exclusions, including composition dependencies.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/eligibility.py`
- `src/magicite/core/context.py`
- `tests/unit/core/test_eligibility.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Define strict RouteContext and EligibilityResult types plus reason-code snapshots.
2. Implement version/platform/host/capability/tool/risk checks with explicit unknown semantics.
3. Intersect caller grants with server policy; preserve each exclusion reason without revealing denied bodies.
4. Publish consumer contract fixtures covering the same artifact through route, dependency and body paths.

## Acceptance Criteria

### AC-S06-01 (event-driven)
GIVEN a declared framework range and unknown host version
WHEN eligibility is evaluated
THEN the result SHALL be context_required
VERIFY: new tests/unit/core/test_eligibility.py::test_unknown_required_context

### AC-S06-02 (event-driven)
GIVEN request grants exceeding server permission policy
WHEN eligibility is evaluated
THEN the excess permission SHALL remain denied
VERIFY: new test_eligibility.py::test_request_cannot_elevate

### AC-S06-03 (event-driven)
GIVEN an archived or quarantined dependency behind an eligible winner
WHEN dependency eligibility is evaluated
THEN the dependency SHALL be excluded
VERIFY: new test_eligibility.py::test_transitive_denial

### AC-S06-04 (event-driven)
GIVEN malformed ranges and unsupported mandatory capability vocabulary
WHEN constraint validation runs
THEN the artifact SHALL not become eligible
VERIFY: new test_eligibility.py::test_unsupported_constraints

### AC-S06-05 (event-driven)
GIVEN consumer B requires artifact X and host artifact inventory is known-empty
WHEN pre-ranking eligibility runs
THEN B SHALL remain eligible with X listed in unsatisfied_artifacts
VERIFY: new tests/unit/core/test_eligibility.py::test_plannable_requirement_is_not_host_denial

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Fail closed on evaluator/version mismatch. Disabling a constraint is a policy amendment, never an emergency relevance fallback.
