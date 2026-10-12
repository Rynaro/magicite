# S04 — Bundle verification and digest-bound local trust

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S02, S03.
Merge after: S02, S03.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As an operator, I want to review exactly the imported bytes and revoke their routing authority independently of signatures.

Native intake passes supplied provenance through registry.py:457-469 and :296-309 to authored auto-verification at lifecycle.py:136-142 (H for flow, exploitability not reproduced). Missing review path is documented at lifecycle.py:124-127 (H).

## Scope and contract

C1, C2, C10. All external channels set server-owned origin. Integrity/authenticity/provenance/review/compatibility/efficacy/local admission are independent. Trust decisions live outside disposable indexes and bind content/resource digest plus policy revision.

Owned implementation surfaces (future work, not modified by this packet):

- `src/magicite/core/trust.py`
- `src/magicite/core/bundles.py`
- `src/magicite/core/registry.py`
- `src/magicite/core/lifecycle.py`
- `src/magicite/core/approvals.py`
- `tests/unit/core/test_trust*.py`
- `tests/integration/test_register_import.py`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Add bounded staging, canonical manifest validation and offline Ed25519 verification against local trust roots.
2. Add origin classification independent of imported declarations and mandatory pending/quarantine staging.
3. Extend durable approvals/audit for list/review/approve/reject/revoke domain APIs, with expected-digest preconditions.
4. Enforce key revocation and policy changes on cached admissions; deliver threat-model cases and CLI API contracts to S11.

## Acceptance Criteria

### AC-S04-01 (event-driven)
GIVEN an external file claiming authored and verified
WHEN intake runs
THEN the artifact SHALL remain pending or quarantined until local admission
VERIFY: new tests/unit/core/test_trust_intake.py::test_forged_origin

### AC-S04-02 (event-driven)
GIVEN a signed bundle with altered resource or unsafe archive member
WHEN verification runs
THEN the bundle SHALL be rejected before admission
VERIFY: new test_trust_bundle.py::test_tamper_and_escape

### AC-S04-03 (event-driven)
GIVEN an approved digest
WHEN content or local policy changes
THEN the prior approval SHALL cease to authorize routing
VERIFY: new test_trust_intake.py::test_digest_and_policy_binding

### AC-S04-04 (event-driven)
GIVEN concurrent repeated approval and a later DB rebuild
WHEN review execution completes
THEN the restored trust ledger SHALL contain exactly one applied decision
VERIFY: new tests/integration/test_trust_recovery.py::test_review_replay_rebuild

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Revoke local admission and quarantine affected bytes; keep signatures and audit history. Rollback cannot reactivate a revoked key or old vulnerable content without fresh current-policy review.
