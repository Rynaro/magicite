# S09 — Evidence receipts, durable ledger and privacy

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S00, S02, S03.
Merge after: S00, S02, S03.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As an operator, I want attributable feedback without storing private prompts by default.

Existing signals authenticate hook tier at core/signals.py:44-54, not verifier correctness (H). eph_event is transient at migrations/001_init.sql:165-169; raw route queries at router.py:542-549 bypass generic event digest handling in obs/events.py:42-64 (H).

## Scope and contract

C0, C4, C6. New append authority is separate from derived DB. Explicit checkpoint returns durable acknowledgement; ephemeral receipt enqueue does not. Provide domain APIs and schemas to S11. Delayed outcomes bind original decision, selected digest and policy, never current same-name skill.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/evidence.py`
- `src/magicite/core/signals.py`
- `src/magicite/obs/events.py`
- `tests/unit/core/test_evidence.py`
- `tests/integration/test_evidence_recovery.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Add ephemeral bounded decision receipts and versioned evidence-event validation with source taxonomy.
2. Implement lease-guarded durable checkpoint, event-ID idempotence, torn-write recovery and rebuild projections.
3. Add local retention/deletion/export with pseudonyms and HMAC key lifecycle; migrate historical raw-query rows with explicit sanitization report. Deletion purges the C6 local-management set (managed export directory plus registered export-manifest paths). Every export carries a notice that deletion cannot follow operator copies; unregistered copies stay out of scope.
4. Keep verifier assertions separate from server-authenticated source identity; provide unknown-outcome and unsupported-counterfactual states.

## Acceptance Criteria

### AC-S09-01 (event-driven)
GIVEN a repeated event ID with identical or conflicting payload
WHEN checkpoint retries
THEN the ledger SHALL enforce one immutable payload per event ID
VERIFY: new tests/unit/core/test_evidence.py::test_event_id_idempotence

### AC-S09-02 (event-driven)
GIVEN an acknowledged checkpoint followed by process death and index rebuild
WHEN recovery runs
THEN acknowledged evidence SHALL remain available
VERIFY: new tests/integration/test_evidence_recovery.py::test_ack_survives_rebuild

### AC-S09-03 (event-driven)
GIVEN raw-query history and secret sentinels in inputs
WHEN upgrade and default checkpoint/export run
THEN managed current evidence stores SHALL contain no raw sentinel
VERIFY: new test_evidence_recovery.py::test_privacy_history_and_export

### AC-S09-04 (event-driven)
GIVEN deterministic selection or delayed self-reported feedback
WHEN the evidence is evaluated
THEN unsupported counterfactual efficacy SHALL remain unknown
VERIFY: new tests/unit/core/test_evidence.py::test_provenance_and_support

### AC-S09-05 (event-driven)
GIVEN managed export artifacts under the ledger export directory plus an unregistered operator copy outside that directory
WHEN privacy deletion runs
THEN deletion SHALL cover the C6 local-management set: purge managed export-directory artifacts plus any registered export-manifest paths; unregistered operator copies remain out of scope
VERIFY: new tests/integration/test_evidence_recovery.py::test_deletion_covers_managed_exports_only

### AC-S09-06 (event-driven)
GIVEN a default evidence export that uses fresh per-export pseudonyms
WHEN the export artifact is written
THEN the export SHALL include a notice that privacy deletion cannot follow operator copies
VERIFY: new tests/integration/test_evidence_recovery.py::test_export_carries_copy_deletion_notice

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Disable capture without disabling retrieval; preserve acknowledged segments. Rollback readers reject unsupported ledger versions; do not restore deleted personal data from backups without replaying tombstones.
