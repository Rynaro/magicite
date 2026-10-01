# V1 release decision draft r2: NO-GA

This revision is **not eligible for GA**. It is a draft evidence package, not a
release, tag, publisher authorization, review or maintainer sign-off. Frozen
acceptance and release thresholds are unchanged.

The `c0782fd` package in [`docs/releases/v1/`](../README.md) remains the
immutable historical observation of that source (reviewed at `52f7bba`).
This r2 package re-adjudicates the same 17 gates against clean source
`9bf9ca0`, which merges the adopted trust-hardening plan
(`.spectra/plans/magicite-v1-trust-hardening/`). It does not edit or reinterpret the earlier records.

The [gate table](gate-table.md) covers all 17 mandatory gates. The
[structured manifest](release-manifest.json) is evaluated by the fail-closed validator:

```sh
python scripts/check_release_manifest.py docs/releases/v1/r2/release-manifest.json
```

Exit 1 and `eligible: false` are the expected result. **Review is pending:** every
reviewer and independent-checker field is empty and every review status is
`PENDING`, so the validator also rejects the regenerated witnesses for lacking an
independent named review. That rejection is intended. A review is recorded only by
re-running the [builder](evidence/build_r2_package.py) with `--reviewer`,
`--review-status` and `--reviewed-package`; the maker (`Vivi /s16-readjudication r2 maker`) cannot be the
reviewer. Hand edits are not a review.

## What changed since the c0782fd package

- Source: `c0782fd` → `9bf9ca0` (trust hardening, PR #32). Full
  suite: **1228 passed, 9 skipped**
  (5 local Docker-image checks ('magicite:verify' image not built), 4 unpublished channel reservations (pip-pypi, pipx, uvx, oci-digest)); mypy, ruff, docs and generated-reference checks exit 0
  ([checks](evidence/checks.json), [results](evidence/test-results.json)).
- [criterion-ledger.json](criterion-ledger.json) now has 88 rows: the 76 frozen
  v1 criteria (text unchanged, re-bound to the r2 run) plus the 12 additive
  AC-TH criteria. Frozen rows: 63 PASS, 13 UNEVALUATED,
  0 FAIL. Additive rows: 1 PASS (AC-TH-08), 11
  UNEVALUATED, 0 FAIL. Each non-PASS AC-TH row names its unwitnessed
  VERIFY sub-clause; AC-TH-01, AC-TH-02, AC-TH-03, AC-TH-04, AC-TH-05, AC-TH-06, AC-TH-07, AC-TH-09, AC-TH-10, AC-TH-11, AC-TH-12 are therefore not PASS even though every mapped
  node passed.
- The historical diagnostics no longer run: on r2 they fail closed with
  `CustodianError` before their scenario because writes require enrolled custody
  ([rerun record](evidence/historical-diagnostics-at-r2-source.json)). The
  [custody replay](evidence/reproduce-trust-mirror-loss-custody.py) of admit →
  revoke → delete revoke mirror → re-check, using the simulated fixture custodian,
  reports **NOT_REPRODUCED** (revoke_mirror_deleted: reinstated=false; revoke_projection_row_deleted: reinstated=false; local_authority_tree_deleted: reinstated=false). Revoke decision mirrors
  found to delete: 0 (the r2 revoke wrote no decision mirror file);
  a replayed admit mirror did not restore admission. See its
  [output](evidence/trust-mirror-loss-custody.json) and
  [provenance](evidence/trust-mirror-diagnostic-provenance.json).
- AC-S09-02's process-death subcheck is re-executed by a
  [custody-attached probe](evidence/probe-checkpoint-process-death-custody.py)
  (PASS). The AC-S14-01 hashing-smoke subcheck was not
  re-executed and is marked UNEVALUATED (that row was already UNEVALUATED).
- TRUST and SECURITY move from FAIL to a proposed UNEVALUATED, and GA-ALL from FAIL
  to a proposed UNEVALUATED: no known defect is reproduced at r2, but obligations
  are unrun or unwitnessed.
  PRIVACY moves from PASS to a proposed UNEVALUATED only because the strict v1
  lifecycle-tests mapping names a node absent from the r2 run; the successor node
  is disclosed in the witness for the checker. These are proposals, not findings.

Criterion PASS is the complete stated mechanical obligation only. It does not
substitute for an empirical release gate, and fixture or same-account custody is
never deployment qualification. Unrun external clauses remain UNEVALUATED even
when their supporting tests pass.

Local verification used Python 3.14.6 on macOS-27.0-arm64-arm-64bit-Mach-O in an isolated venv
synced from `uv.lock`. That is not the declared Python 3.11/3.12 release matrix.

## Open notes for the independent checker

- `.spectra/plans/magicite-v1-trust-hardening/README.md:3` still reads "IMPLEMENTATION AND QUALIFICATION PENDING"
  although the implementation is merged at `9bf9ca0`. It is not edited here.
- The PRIVACY data-map witness still binds `docs/releases/v1/privacy-data-map.md`,
  whose last sentence describes the historical live trust-mirror weakness and which
  does not list the trust authority journal or custodian surfaces.
- Whether gates with no remaining known defect but unrun obligations (TRUST,
  SECURITY, GA-ALL) are FAIL or UNEVALUATED; whether to adopt the PRIVACY successor
  node; whether obligation-level PASS candidates deserve fresh witness files.

## Still required

Official SkillRet final split, licensed real 10k corpus, dedicated production E6
measurements, paired retrieval and empirical abstention bounds, independently
authored actual host-task outcomes, real Claude Code transcripts, a release-scoped
threat model, bounded fuzz/adversarial evidence and a named adversarial trust
corpus, separate-UID Linux/macOS custodian deployment qualification, the unwitnessed
AC-TH sub-clauses, full supported recovery/upgrade matrix, published-channel and
fetched supply-chain checks, independent operator tutorial, two independent
compatible RCs, external reproduction or two production pilots, an independent
review of this package, and explicit maintainer sign-off. No participants were
contacted, accounts provisioned or publishing accounts created by this task.

Human decisions remain open for the proposed MCP expansion (the frozen 16-tool
surface remains), optional S10, publishing, worktree deletion, shared-environment
resynchronization, and custodian provisioning. The trust ledger proposal itself was
adopted and implemented through the trust-hardening plan. No release authority is
inferred from code review or green CI.

Rollback of this r2 package removes only evidence files and draft documents; it has
no runtime storage migration. Runtime rollback follows the operator documentation.
