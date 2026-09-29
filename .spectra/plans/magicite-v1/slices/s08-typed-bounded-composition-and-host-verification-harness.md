# S08 — Typed bounded composition and host verification harness

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S02, S06.
Merge after: S02, S04, S06, S07.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a host, I want a bounded valid dependency plan with clear unmet requirements before I execute anything.

Composition.py:74-119 already bounds closure; :161-208 deliberately breaks cycles; :247-263 computes completeness confidence (H). Current independent 24-case artifact tests structural expansion only, docs/evaluation/v0.3-results.json:24-38 (H).

## Scope and contract

C5. Consume C1 host/artifact kinds and exact-revision relations.requires/before/supersedes; build against fixture implementations of C1/C2; merge production integration after S04/S07. Preserve legacy diagnostics explicitly, but cycles/missing required nodes can never be marked valid. Host execution harness belongs to evaluation fixtures and is not a Magicite runtime executor.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/composition.py`
- `tests/unit/core/test_composition.py`
- `tests/fixtures/composition-v1/*`
- `tests/integration/test_composition_v1.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Implement typed capabilities, provider resolution, conflicts, alternatives and stable ordering.
2. Revalidate all nodes under one snapshot/eligibility policy; propagate explicit unsatisfied, ambiguity and budget errors.
3. Publish Plan/1 with hashes, required permissions and diagnostic validity independent of efficacy.
4. Extend independent structural corpus for multiple valid partial orders and add sandboxed host-side task verifier fixtures.

## Acceptance Criteria

### AC-S08-01 (event-driven)
GIVEN an eligible winner with a denied or missing required dependency
WHEN composition runs
THEN the plan SHALL be invalid
VERIFY: extend tests/unit/core/test_composition.py::test_denied_dependency_invalid

### AC-S08-02 (event-driven)
GIVEN a cycle or exceeded node/depth/edge limit
WHEN composition runs
THEN the plan SHALL contain no executable prefix
VERIFY: new tests/integration/test_composition_v1.py::test_cycle_and_budget

### AC-S08-03 (event-driven)
GIVEN two satisfying producers with no explicit preference
WHEN composition runs
THEN the result SHALL report ambiguous_provider
VERIFY: new test_composition_v1.py::test_ambiguous_alternatives

### AC-S08-04 (event-driven)
GIVEN an independently authored task with a deterministic host verifier
WHEN the evaluation harness executes the plan
THEN the report SHALL distinguish structural validity from verified task outcome
VERIFY: new test_composition_v1.py::test_host_verifier_report

### AC-S08-05 (event-driven)
GIVEN known-empty artifact inventory, B requiring artifact X and eligible A producing X
WHEN composition selects B
THEN the valid plan SHALL order A before B
VERIFY: new tests/integration/test_composition_v1.py::test_producer_satisfies_consumer with denied-host-tool paired case

### AC-S08-06 (event-driven)
GIVEN selected exact revisions with before or supersedes relationships
WHEN the planner consumes the shared S02 fixtures
THEN relation behavior SHALL match C1 ordering and replacement-conflict semantics
VERIFY: new tests/integration/test_composition_v1.py::test_shared_relation_semantics

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Use single-skill routing or return invalid-plan diagnostics if typed composition is disabled. Never revert to cycle-breaking plans labeled executable.
