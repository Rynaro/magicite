# S14 — Scale, external corpora and task-outcome evidence

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S01, S07, S08.
Merge after: S01, S07, S08.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a release reviewer, I want measured quality and resource envelopes at declared scales rather than generalized microbenchmark claims.

10k failures remain visible at docs/evaluation/v0.3-benchmarks.json:70-100 (H). run_benchmark_matrix.py:266-290 repeats one query; tests/integration/test_route_latency.py:1-27 uses synthetic hashing/nonblocking timing (H).

## Scope and contract

evaluation.md is the preregistration contract. Take eval ownership after S01. Distinguish synthetic throughput, official external retrieval and real host outcomes. Never substitute synthetic vectors or toy tasks for production quality claims.

Owned implementation surfaces (future work, not modified by this packet):

- `scripts/run_benchmark_matrix.py`
- `src/magicite/eval/*`
- `tests/unit/eval/*`
- `docs/evaluation/v1/*`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Run 100/1k/10k and exploratory 50k profiles with explicit cold/warm model/index/query caches, distinct and repeated queries.
2. Measure p50/p95/p99, RSS, disk/index size, build time and payload tokens using production and hashing providers separately.
3. Run pinned official SkillRet splits and independently labeled local temporal holdout; archive labels, licenses and exclusions.
4. Run paired no-skill/selected-skill/composed-plan tasks with deterministic host verifiers and compatibility failure slices.
5. Produce result and claim manifests, preserving failed/inconclusive/non-inferior outcomes and maximum tested support scale.

## Acceptance Criteria

### AC-S14-01 (event-driven)
GIVEN a declared benchmark profile
WHEN the matrix runs
THEN the result SHALL identify every cache state and hardware/model fingerprint
VERIFY: extend tests/unit/eval/test_bench_baselines.py::test_profile_manifest

### AC-S14-02 (event-driven)
GIVEN a claimed supported scale
WHEN release evidence is checked
THEN the complete production-provider envelope SHALL satisfy its preregistered budget
VERIFY: dedicated runner validates evaluation.md envelopes; shared CI validates completeness only

### AC-S14-03 (event-driven)
GIVEN a locked local or external final split
WHEN ranking quality is assessed
THEN the promotion verdict SHALL follow the preregistered paired interval rule
VERIFY: new tests/unit/eval/test_release_verdicts.py with hand-computed boundary cases

### AC-S14-04 (event-driven)
GIVEN an end-task usefulness claim
WHEN claim validation runs
THEN the evidence SHALL include paired host-verifier outcomes
VERIFY: new test_release_verdicts.py::test_no_structural_efficacy_substitution

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Retain the frozen strongest simple incumbent and narrow published support claims if gates fail; never tune on failed final labels then reuse them as untouched holdout. New experiment requires new held-out data.
