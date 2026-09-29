# S12 — Read-only diagnosis, backup and crash recovery

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S03, S04, S09.
Merge after: S03, S04, S09.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As an operator, I want diagnosis that cannot alter data and recovery covering every authoritative store.

doctor.py:132-145 calls db.connect; storage/db.py:87-98 defaults migration on, contradicting read-only wording (H). Existing lease asserts ownership at storage/lease.py:150-161 and multiprocess tests exist at tests/integration/test_lease_multiprocess.py:141-226 (H).

## Scope and contract

C0, C6, C8. Doctor uses genuine read-only opens without creating DB/WAL/files. Backup covers artifacts/assets, evidence, trust, approvals, policy pointers, privacy tombstones and config at its explicitly declared recovery-point sequence; current overlays are preserved in-place or separately supplied for clean-machine restore under C8; exclude secrets unless operator separately arranges encrypted custody. CLI wiring belongs to S11.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/obs/doctor.py`
- `src/magicite/core/backup.py`
- `src/magicite/storage/lease.py`
- `tests/unit/obs/test_doctor.py`
- `tests/integration/test_lease_multiprocess.py`
- `tests/integration/test_backup_restore_v1.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Create doctor/1 JSON with stable check IDs, status, evidence, remediation and unknown/not-applicable distinctions.
2. Add consistent snapshot/restore domain APIs under shared lease, integrity verification and recovery journal.
3. Extend process-kill and stale-fence tests to all new authorities and cross-store commit boundaries.
4. Classify supported local filesystems and explicitly reject unsafe writable service configurations; preserve diagnostics for NFS/network shares.

## Acceptance Criteria

### AC-S12-01 (event-driven)
GIVEN missing, old-schema, corrupt or read-only data directories
WHEN doctor runs
THEN filesystem and database bytes SHALL remain unchanged
VERIFY: extend tests/unit/obs/test_doctor.py::test_zero_write_matrix

### AC-S12-02 (event-driven)
GIVEN a verified backup of all authoritative stores
WHEN restore and rebuild run
THEN all acknowledged durable records through the backup recovery point SHALL be recovered
VERIFY: new tests/integration/test_backup_restore_v1.py::test_complete_restore

### AC-S12-03 (event-driven)
GIVEN a revoked artifact or privacy deletion after backup plus a valid current RecoveryOverlay and sequence anchor
WHEN restore runs
THEN current revocation and deletion policy SHALL prevent reactivation
VERIFY: new test_backup_restore_v1.py::test_policy_reapplication

### AC-S12-04 (event-driven)
GIVEN a stale writer or killed lease holder
WHEN another process recovers
THEN the stale process SHALL be unable to commit
VERIFY: extend tests/integration/test_lease_multiprocess.py with evidence/trust/policy writers

### AC-S12-05 (event-driven)
GIVEN a clean-machine backup with absent or stale current overlay/anchor
WHEN restore is attempted
THEN routing and evidence access SHALL remain disabled with reconciliation_required
VERIFY: new tests/integration/test_backup_restore_v1.py::test_missing_stale_overlay_closed

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Restore a checksum-verified snapshot with current revocation/deletion overlays. A failed restore stays offline with actionable status; do not serve a mixed-generation registry.
