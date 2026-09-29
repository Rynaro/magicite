# S03 — Migration authority and index-safe storage transitions

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S02.
Merge after: S02.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As an operator, I want previewable upgrades that survive interruption and preserve durable decisions.

SQL scripts/version advance are transactional at storage/db.py:51-84; DB is rebuildable at storage/migrations/001_init.sql:1-6; approvals have external mirrors at core/approvals.py:10-23 (H). Writer is atomic/lease-guarded at engram/writer.py:334-356 (H).

## Scope and contract

C0, C8, C11. Provide domain APIs preview/apply/status/resume/restore; S11 owns CLI registration. Preserve IDs, source bytes, trust/control authority and all evidence. Reserve migration numbers centrally. No destructive downgrade without a matching backup.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/storage/db.py`
- `src/magicite/storage/durable.py`
- `src/magicite/storage/migrations/*`
- `src/magicite/core/migration.py`
- `tests/integration/test_upgrade*.py`
- `tests/integration/test_migration_v1.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Inventory authoritative versus derived paths; define versioned migration/snapshot manifests and operation IDs.
2. Implement preview and explicit apply using shared lease/fencing; journal file and SQL progress so replay is idempotent.
3. Support archived 0.2 artifacts and published 0.2/0.3 data snapshots, with explicit unsupported-version errors.
4. Coordinate provisional migrations from S04/S05/S09/S12, serializing final migration numbers at integration.
5. Produce before/after eligibility report; unknown metadata remains unknown and imported state never promotes trust.

## Acceptance Criteria

### AC-S03-01 (event-driven)
GIVEN a legacy snapshot and dry-run request
WHEN migration preview runs
THEN all input bytes SHALL remain unchanged
VERIFY: new tests/integration/test_migration_v1.py::test_preview_zero_write

### AC-S03-02 (event-driven)
GIVEN fault injection at each file/SQL commit boundary
WHEN migration resumes
THEN the final state SHALL equal one uninterrupted migration
VERIFY: new test_migration_v1.py::test_crash_matrix

### AC-S03-03 (event-driven)
GIVEN a completed migration operation ID
WHEN apply is retried
THEN the operation SHALL produce zero duplicate effects
VERIFY: new test_migration_v1.py::test_idempotent_apply

### AC-S03-04 (event-driven)
GIVEN a new-schema registry and matching old backup
WHEN downgrade restoration runs
THEN the restored state SHALL match the supported backup manifest
VERIFY: extend tests/integration/test_upgrade_v02.py with v1 restore and future-version rejection

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Restore the complete pre-upgrade snapshot under current revocation/deletion policy. Retain failed journals and diagnostics; never delete migration rows to pretend downgrade succeeded.
