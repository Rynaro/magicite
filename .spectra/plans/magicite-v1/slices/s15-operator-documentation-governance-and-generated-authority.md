# S15 — Operator documentation, governance and generated authority

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S11, S12, S13, S14.
Merge after: S11, S12, S13, S14.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a new operator or contributor, I want one accurate path from installation through routing, recovery and upgrade.

docs/AUTHORITY.md:1-21 defines 0.3 authority while docs/07-evaluation-and-observability.md:3 says Draft-refined/v1 (H). check_generated_docs.py:16-40 checks fixed tool inventory only; validator wiring absent from scoped CI search (M).

## Scope and contract

Use the dossier information architecture with redirects preserving historical links. Generate versions, tools, schemas, CLI/config inventories from runtime. Historical numerical claims carry source/result status; no documentation wording can turn unevaluated gates green.

Owned implementation surfaces (future work, not modified by this packet):

- `README.md`
- `docs/*`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `scripts/check_docs.py`
- `scripts/check_generated_docs.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Write a coherent install → host → import/review → route/explain → backup/restore → upgrade tutorial with offline/privacy behavior.
2. Generate public references and authority page; retain archive links and corrected negative findings.
3. Publish exact supported versions/hosts/filesystems, deprecation window, security reporting, maintainer/reviewer roles and RFC process.
4. Add claim-integrity and documentation drift checks; deliver commands to S13 for serialized CI wiring.

## Acceptance Criteria

### AC-S15-01 (event-driven)
GIVEN generated references and installed runtime
WHEN drift checks run
THEN declared versions/schema/tool inventories SHALL match runtime
VERIFY: extend scripts/check_generated_docs.py with snapshot fixtures

### AC-S15-02 (event-driven)
GIVEN a quantitative README claim without an accepted evidence manifest
WHEN documentation CI runs
THEN the claim SHALL fail validation
VERIFY: extend scripts/check_docs.py with claim-ledger fixture

### AC-S15-03 (event-driven)
GIVEN a fresh supported operator environment
WHEN the complete tutorial is followed
THEN all advertised steps SHALL complete with captured output
VERIFY: scripted tutorial smoke plus independent operator transcript

### AC-S15-04 (event-driven)
GIVEN a deprecated contract or changed support row
WHEN docs validation runs
THEN the published policy SHALL identify replacement and support deadline
VERIFY: new documentation contract fixture for S15

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Keep versioned docs and redirects. Correct unsupported claims immediately without deleting historical contrary evidence.
