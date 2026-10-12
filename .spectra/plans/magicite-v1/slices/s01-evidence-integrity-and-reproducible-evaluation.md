# S01 — Evidence integrity and reproducible evaluation

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: none; start immediately.
Merge after: none.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a reviewer, I want every performance or quality claim to resolve to reproducible inputs and independent labels.

core-derived gold occurs at src/magicite/eval/bench.py:328-337; report lacks immutable manifest at :348-357 (H). docs/evaluation/v0.3-results.json:24-54 explicitly scopes structural evidence and carried-forward ranking (H). Metrics already exist in eval/metrics.py:28-57 (H).

## Scope and contract

evaluation.md defines schemas, splits, uncertainty and claims. Retain legacy results unchanged and labeled historical. No source-derived expected plans. This slice owns new manifest/result/claim schemas and pure validators; S13 wires their commands into CI.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/eval/*`
- `scripts/check_evaluation_results.py`
- `tests/unit/eval/*`
- `tests/integration/test_bench.py`
- `docs/evaluation/*`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Create versioned experiment, corpus, predictions, result and claim schemas with digest validation.
2. Separate gold selection labels, structural expected partial orders and host task outcomes; label author-created versus independent sources.
3. Extend existing metrics with paired bootstrap intervals and abstention coverage; preregister before final labels are opened.
4. Provide the official-split external-corpus adapter and an offline tiny adapter fixture; external download is an explicit command with license and digest record.
5. Replace circular Plan F1 reporting with a deprecated diagnostic label; publish one clearly new baseline run when execution occurs, without rewriting history.

## Acceptance Criteria

### AC-S01-01 (event-driven)
GIVEN a frozen manifest and local corpus
WHEN the runner repeats with identical pins
THEN per-query semantic predictions SHALL match
VERIFY: new tests/unit/eval/test_manifests.py::test_reproducible_predictions

### AC-S01-02 (event-driven)
GIVEN a corpus with train/test overlap or duplicate query IDs
WHEN validation runs
THEN the corpus SHALL be rejected
VERIFY: new test_manifests.py::test_leakage_rejected

### AC-S01-03 (event-driven)
GIVEN a result referencing changed labels or missing prediction bytes
WHEN claim validation runs
THEN the claim SHALL fail integrity validation
VERIFY: new test_manifests.py::test_claim_digest_failure

### AC-S01-04 (event-driven)
GIVEN an independent structural corpus
WHEN benchmark gold is loaded
THEN expected plans SHALL originate only from corpus annotations
VERIFY: extend tests/integration/test_bench.py with a production-planner sentinel that rejects calls during label loading

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Keep prior result manifests addressable; roll back runner version without erasing contrary results. New results supersede, never overwrite.
