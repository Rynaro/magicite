# S16 — Release candidates, external validation and GA decision

## Assignment

Status: ready to assign subject to dependency gates. Priority: P0 V1 release scope.
Build after: S00, S01, S02, S03, S04, S05, S06, S07, S08, S09, S11, S12, S13, S14, S15.
Merge after: S00, S01, S02, S03, S04, S05, S06, S07, S08, S09, S11, S12, S13, S14, S15.
Executor: reasoning-capable implementation agent; follow repository Vivi workflow. This packet itself is specs only.
Work bound: one independently reviewed slice; split into smaller PRs at the listed action boundaries if needed. No elapsed-day deadline; completion is evidence-gated, per user direction.

Read [shared contracts](../contracts.md), [evaluation](../evaluation.md), [release gates](../release-gates.md) and [dispatch plan](../plan.json) before coding. Shared-file ownership in C9 overrides broad owned-path patterns below.

## Story and current evidence

As a maintainer, I want a V1 release decision based on complete evidence and independent use.

The dossier explicitly marks evidence PARTIAL and calls for independent reproduction or two external production pilots. Existing release/CI scaffolding is inspected, not executed in this planning task (research/corrections.md).

## Scope and contract

release-gates.md. Two distinct RC artifacts freeze API/schema with no mandatory calendar wait. Any breaking correction restarts RC compatibility verification. S10 efficacy is optional; its absence must be explicit. This slice does not authorize contacting pilots or publishing releases during spec creation.

Owned implementation surfaces (future work, not modified by this packet):

- `docs/releases/*`
- `docs/evaluation/v1/external/*`
- `.github/BRANCH_PROTECTION.md`

Out of scope: unrelated refactors, changing another slice's contract or thresholds, claiming unrun verification, host execution inside the runtime, publishing a release without release authority.

## Action plan

1. Aggregate immutable evidence for every gate with pass/fail/unevaluated/waived status; no implicit pass.
2. Run two RC rehearsals across supported installation/host/upgrade/restore matrices; compare contract fingerprints.
3. Obtain one independent reproduction OR two independent external production pilots with scoped, consented, redacted evidence.
4. Resolve security findings, support ownership and publisher setup; produce maintainer sign-off and rollback instructions.
5. Publish GA only after release authority approval through the normal repository process, verifying fetched artifacts afterward.

## Acceptance Criteria

### AC-S16-01 (event-driven)
GIVEN a release gate with missing evidence
WHEN the GA validator runs
THEN release eligibility SHALL be false
VERIFY: new release-manifest validation fixture with missing gate

### AC-S16-02 (event-driven)
GIVEN two RC artifacts
WHEN API/schema fingerprints are compared
THEN stable fingerprints SHALL match or require a fresh RC pair
VERIFY: release gate RC-CONTRACT

### AC-S16-03 (event-driven)
GIVEN external validation evidence
WHEN independence review runs
THEN the evidence SHALL satisfy one reproduction or two-pilot path
VERIFY: release gate EXTERNAL with named independent reviewer and immutable report

### AC-S16-04 (event-driven)
GIVEN a complete candidate release manifest
WHEN GA eligibility is evaluated
THEN all nonwaivable release gates SHALL pass
VERIFY: release gate GA-ALL

## Verification and handoff

VERIFY targets above name expected future tests/gates; they are not claims that these tests exist or passed at spec time. Extend the cited existing tests where appropriate. Run focused tests first, then repository lint/type and affected integration suites; preserve current hot-path, writer lease, rebuild and idempotency invariants. Record actual commands, dependency/model pins and outputs. Empirical/release gates require their own artifacts beyond unit tests.

Deliver: implementation branch/base SHA, dependency SHAs, owned-path diff, contract fixtures, criterion-to-test/result mapping, migration/rollback notes, and any unpassed criterion. Do not mark complete merely because tests for the happy path pass. Integration owner applies shared-file patches in dependency order. A contract change requires an explicit spec/criteria amendment before merging dependent work.

## Failure and rollback

Do not tag GA on incomplete evidence. Keep RC labels and publish explicit failed gates; if GA has shipped, use the incident/corrective-release process.
