# A02 — C6 export deletion local-management scope

Status: applied. Plan: `magicite-v1`. Contract: `C6` (owner S09). Criteria: `AC-S09-05`, `AC-S09-06` (added). Gate: `ramza-freeze --amend`.

## Ruling

The amendment is accepted. It does not weaken C6's deletion or privacy guarantees. It names the custody set C6 already implied by "exports still under local management" and makes the operator-facing limitation verifiable.

C6 already requires deletion of ledger, projections, caches, and exports still under local management, and already treats backups as a separate documented domain with expiry and restore-time tombstone replay. It does not require Magicite to discover or erase files the operator copied or moved outside Magicite's custody. Claiming that deletion follows those copies would be a false guarantee (copies can leave the machine). Scanning the operator's filesystem for lookalikes would be both incomplete and privacy-invasive.

V1 safety intent that remains mandatory:

- Privacy deletion purges ledger payloads, projections, caches, and every export artifact Magicite still tracks.
- Backups remain a separately documented domain with expiry and restore-time tombstone replay.
- Default export uses fresh per-export pseudonyms; copies of one export cannot be linked to a later export.
- Operators are told, on the export itself, that deletion cannot follow copies.

Out of deletion scope (not a C6 loss):

- Files the operator copied or moved outside Magicite's tracked locations.
- Caller-chosen export destinations Magicite does not record in a registered export manifest (operator custody from the moment of write).

C6 contract text gains the clarifying sentences below. Two acceptance criteria are added so the managed-set purge and the export notice are independently verifiable. Existing AC-S09-01..04 are unchanged.

## RAMZA decision record

Right-size: lite (score 4: ~8 spec files, `--security`, stakes high). Complexity: 6/12, standard. Selected Explore hypothesis `hyp-B-disambiguate-managed-set` scored 87.5 elite. Rejected: leave C6 unamended (63 weak — leaves S09 unbound); encrypt-then-shred export wrapping keys on deletion (63.5 weak — a new control, not a disambiguation, and it would silently destroy consented export archives/backups). Maker of the interpretation: S09 implementer. Checker: RAMZA (lite critic optional; identities are distinct).

## Previous freeze text

C6 deletion sentence (unchanged, then clarified):

```
Deletion covers ledger, projections, caches, exports still under local management; backups are documented separately with expiry and restore-time tombstone replay.
```

No prior acceptance criterion bound export-deletion custody. AC-S09-03 only requires that managed current evidence stores contain no raw sentinel after upgrade and default checkpoint/export.

## Amended freeze text

C6 clarifying sentences, inserted after the existing deletion sentence:

```
Exports still under local management are artifacts Magicite created and still tracks in its managed export directory (the ledger export tree, conventionally `<data_dir>/evidence/exports/`) plus any path still recorded in a registered export manifest; files the operator copied or moved elsewhere, and caller-chosen destinations Magicite does not record in that manifest, are out of deletion scope. Each export SHALL carry a notice that deletion cannot follow those copies. Fresh per-export pseudonyms already required above make such copies unlinkable to later exports.
```

Added criteria:

```
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
```

Each new ID is one assertion. AC-S09-05 uses the A01 identity-set style so the THEN has no compound ` AND `. AC-S09-06 is a separate trigger (export write, not deletion).

## Evidence

S09 implementation question (independent of this amendment): physical payload purge plus ledger tombstones, projection purge, and wipe of managed export artifacts under the ledger export tree. Caller-supplied `export_dir` writes and operator copies are not tracked unless S09 records them in a registered export manifest.

This amendment does **not** authorize:

- Filesystem-wide search for export lookalikes.
- A claim that deletion erased operator copies.
- Encrypt-then-shred of export contents (that would be a new C6 control and a separate amendment).

S09 must newly implement the export notice (AC-S09-06) and add the managed-dir purge plus operator-copy non-follow test (AC-S09-05). If S09 records registered export-manifest paths, deletion must purge those too. Fresh per-export pseudonyms are already a C6 requirement.

## Packet updates

- `.spectra/plans/magicite-v1/contracts.md` — C6 clarifying sentences.
- `.spectra/plans/magicite-v1/acceptance.md` — AC-S09-05 and AC-S09-06 added.
- `.spectra/plans/magicite-v1/slices/s09-evidence-receipts-durable-ledger-and-privacy.md` — verbatim criteria plus action-plan alignment.
- `.spectra/plans/magicite-v1/plan.json` — `criteria_count` 76; S09 acceptance IDs; `criteria_amendments` entry A02.
- `.spectra/plans/magicite-v1.md` — criteria count and amendment index.
- `.spectra/plans/magicite-v1.state.json` — hash-chained via `ramza-freeze --amend`.
- `.spectra/plans/magicite-v1/verification.md` — amendment recorded with new freeze hash.
- Packet manifest and ECL envelope fingerprints regenerated for changed payloads.

## Freeze chain

- Previous `criteria_sha256`: `20a5c8c9a8e0d938074e1ee893ba758eaf31bc919466be62ad41897e5f3f6673`
- Amended `criteria_sha256`: `f10330326a78d50c9d657866dda8367849f79aca36c2740ac516a4db99011a2c`
- Recorded in `.spectra/plans/magicite-v1.state.json` via `ramza-freeze --amend` at `2026-09-29T23:33:25Z`.
