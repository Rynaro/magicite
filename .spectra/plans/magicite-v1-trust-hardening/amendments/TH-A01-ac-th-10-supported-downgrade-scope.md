# TH-A01 — AC-TH-10 supported downgrade scope

Status: applied (owner-decided). Plan: `magicite-v1-trust-hardening`. Criterion: `AC-TH-10`. Gate: owner-recorded planning review (acceptance.md:3-4). No other criterion changes.

## Ruling

Accepted by the human owner. The frozen THEN applies "downgrade" without scope, which is unattainable against a pre-hardening binary running on arbitrary rolled-back bytes: after migration, a revocation exists only in authenticated custodian/journal custody, which the frozen 22ae4e0 reader never reads; when pre-migration bytes are hand-copied back, that reader re-derives admission from them (`default_local_authorship_admission`). No new-code change can retrofit that old binary.

The pre-existing scope in `implementation-notes.md:11` already states this boundary: "The actual old-reader qualification uses the frozen 22ae4e0 code on migrated bytes, including after derived database deletion. This does not retrofit arbitrary old binaries against a deliberate malicious downgrade under the authorized client account." This amendment aligns the frozen text with it.

Safety intent that remains mandatory:

- Rotation binds old/new keys, epochs and heads through custodian CAS.
- Unsupported readers reject the new authority.
- No supported downgrade path reactivates revoked content or silently resets enrollment.
- The supported runtime keeps the latest authenticated revocation authoritative (or fails closed) on any rolled-back local bytes.

Out of scope (disclosed residual risk): a pre-hardening binary run against bytes the operator hand-rolled back (copying a complete backup over `.magicite/`, copying `trust/sources/<sha>` over an active `.egr.md`, or a VCS checkout).

## Previous freeze text

```
### AC-TH-10 — Rotation and downgrade continuity
GIVEN an initialized authenticated epoch and current revocation state
WHEN keys/epochs rotate, an old reader opens the new authority format, or downgrade is attempted
THEN rotation SHALL bind old/new keys, epochs and heads through custodian CAS; unsupported readers SHALL reject the new authority; downgrade SHALL NOT reactivate revoked content or silently reset enrollment
VERIFY: dual-bound epoch transition, stale/wrong-key rotation, old-format rejection and revocation-preserving downgrade/restricted recovery
```

## Amended freeze text

```
### AC-TH-10 — Rotation and downgrade continuity
GIVEN an initialized authenticated epoch and current revocation state
WHEN keys/epochs rotate, an old reader opens the new authority format, a supported downgrade path runs (documented `migration restore` into inactive staging, complete-backup restore through the supported runtime, or the frozen 22ae4e0 reader on migrated bytes including after derived database deletion), or the supported runtime opens rolled-back local bytes
THEN rotation SHALL bind old/new keys, epochs and heads through custodian CAS; unsupported readers SHALL reject the new authority; supported downgrade paths SHALL NOT reactivate revoked content or silently reset enrollment; the supported runtime SHALL keep the latest authenticated revocation authoritative over any rolled-back local bytes or remain closed
VERIFY: dual-bound epoch transition, stale/wrong-key rotation (tests/unit/core/test_trust_rotation.py, tests/unit/core/test_trust_rotation_transport.py), old-format rejection, and tests/integration/test_th10_old_reader_downgrade.py (new-format revoke vs 22ae4e0 reader; old admit → reviewed migration → revoke; migration restore staging; trust/authority deletion fails closed; hand-rollback residual: supported runtime still enforces revoke)
```

The THEN retains the original semicolon-separated SHALL clauses (no compound ` AND `); hand rollback + pre-hardening binary is a disclosed residual, not a SHALL.

## Evidence

A8 probe (old code 22ae4e0 via `git archive`, separate interpreter; new code 4ed8b11):

- (a) New-format revoke: old reader routes nothing; body `stale_decision`/`not_admitted`; after DB deletion old sync refuses marker-bearing files (NotFoundError).
- (b) Old admit → reviewed migration → new revoke: stale admit mirror remains, but file carries the `magicite.trust_journal` marker with a different digest; old reader does not route/disclose, including after DB deletion.
- (c1) `migration restore` into staging: active bytes unchanged; old reader does not disclose; new code still revoke.
- (c3) Deleting `trust/authority/`: new code fails closed (TrustLedgerCorruptError "trust custody unavailable; reconciliation required"); no reset.
- (c2) Hand-copied pre-migration bytes: old binary routes and discloses the revoked subject, even after deleting all `trust/decisions/*.json`; new code on the same bytes: journal head unchanged, latest decision revoke, subject not routed.
- No variant made the new runtime reset enrollment or reactivate revoked content.

## Packet updates

- `acceptance.md` — AC-TH-10 block replaced verbatim; version line kept at 1 with a note "Amended by amendments/TH-A01".
- `README.md` — add: "Amendments: [TH-A01](amendments/TH-A01-ac-th-10-supported-downgrade-scope.md) scopes AC-TH-10 downgrade to supported paths."
- `spec.md:106-107` — append "Downgrade scope is defined by TH-A01."
- `traceability.md:9` — append "(supported paths, TH-A01)".
- `threat-model.md` — residual paragraph.
- `docs/operations.md:486` — replacement text; `docs/operator-tutorial.md` optional pointer.
- `tests/integration/test_th10_old_reader_downgrade.py` — new witness.
- `docs/releases/` r3 evidence package (later) — cite TH-A01 and the residual.

## Freeze chain

This packet has no RAMZA `state.json` / hash-chained freeze (adopted as plain files in commit 0577912). No `ramza-freeze --amend` record exists or is fabricated. Integrity record is the file digest only:

- Previous `acceptance.md` sha256: `1ee9e8cf3cc964d399610c5cd263b84b9e7153d3e6c30e75e0940fda8dc4b9ea` (at `4ed8b11`)
- Amended `acceptance.md` sha256: `39e7f43f89e502c4d46679d45e6184611155c1a27d21679835aff588441b4ed0`
- Recorded by: human owner decision, drafted by RAMZA, 2026-10-01, this commit.

Original `.spectra/plans/magicite-v1/acceptance.md` (`f10330326a78…a2c`, README.md:18-19) unchanged.
