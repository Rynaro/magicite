# Magicite v1 r3 open items

This is the checked-in record of the remaining items from the r3 slices. The evidence comes from merged PRs #34 to
#50. PRs #48, #49 and #50 recorded no remaining items. It is scoped to `codex/v1-integration` at `bcdf680`.

Each item restates a "remaining", "not witnessed", "not covered", "follow-up" or
reviewer note from the cited merged PR body (`gh pr view <n> --json body`). The
independent reviews recorded there are by ATLAS, not the maker. Where a code location
is given, it was checked at `bcdf680`. Items that could not be grounded in a PR body
or the code were dropped (see the end of this document).

None of these items is a PASS claim. This document is cited by the
[threat model](threat-model.md) and the [findings register](findings-register.md).

## 1. Obligations for the r3 evidence package

| ID | Item | Source |
|---|---|---|
| OI-01 | Rebind every witness against the r3 clean source. The r2 package is a historical observation of `9bf9ca0` and already drifts at integration: 8 bound paths were changed by PRs #34 to #36, PRIVACY reports a digest mismatch, and `docs/AUTHORITY.md` (digest-bound by the r2 LEARNING witness) was changed by D4. | PR #42 ("Note on evidence bindings"; independent review) |
| OI-02 | State that each package validates only against its own source-commit checkout. r2 still validates against a `9bf9ca0` checkout, and r3 rebinds against its own clean source. | PR #42 |
| OI-03 | Ship an r3-scoped privacy data-map successor. `docs/releases/v1/privacy-data-map.md` still mentions the old mirror weakness and is hash-bound by the v1 and r2 PRIVACY witnesses, so it was deliberately not changed. | PR #42 ("Deliberately not changed") |
| OI-04 | AC-TH-10: map to `tests/integration/test_th10_old_reader_downgrade.py` and cite TH-A01 and its residual. "Restricted recovery" coverage now lives in case (c1) and AC-TH-09. | PR #35 ("Follow-ups for the r3 evidence package") |
| OI-05 | AC-TH-01: no `src/` code reads the `policy.json`/`roots.json` projections (only `trust_legacy.py:110`, during migration). The projection cases are a regression guard showing the projections are inert, not proof of an active defence. | PR #37 ("Recorded limitation"; independent review) |
| OI-06 | AC-TH-04 stays UNEVALUATED. Not witnessed: a real separate-UID Linux/macOS deployment; the Darwin `acl_get_fd_np`/`acl_to_text` ACL branch; Linux `listxattr` OSError/ENOTSUP handling; and a real-kernel `SO_PEERCRED` run (planned for the r3 Linux container run). | PR #38 ("Still not witnessed") |
| OI-07 | AC-TH-06 not witnessed: the retry-path `assert_owned` (`trust_journal.py` ≈:508/:510), `reconcile`'s own assertions, the checks inside `_append_file`/`_replace_file` in isolation, and the initial check at ≈:494. | PR #39 ("Not witnessed here") |
| OI-08 | AC-TH-09: the static scan does not cover paths assembled dynamically from other constants. Generic-copy sites are covered only by the planted-mirror test. | PR #41 ("Not claimed") |
| OI-09 | AC-TH-11 remains PARTIAL. Not witnessed: restricted custody state; sensitive-path non-disclosure; `doctor` has no custody probe (product gap); corrupt local journal and stale head are not *diagnosed* by `custody status`, which reads only custodian state; doctor JSON is not secret-planted. | PR #40 ("AC-TH-11 remains PARTIAL") |
| OI-10 | AC-TH-05 not witnessed: a literal fsync(2) syscall trace (deferred to the Linux container run with `strace`); real process-kill crashes; and a wire-level lost reply, since the faults are in-process. | PR #43 ("Not witnessed (recorded for r3)") |

## 2. Product and test follow-ups

| ID | Item | Source |
|---|---|---|
| OI-11 | `review_approve`: a `BusyError` raised *inside* the critical section after the decision commit (lease ownership or heartbeat loss) is also retried. The retry then returns the existing admit and skips `apply_local_admission` and the approval audit (`src/magicite/core/registry.py:1828-1832, 1853-1874`). This is pre-existing and made more reachable by the 10 s wait. Proposed remedies: restrict retries to acquisition-time contention, or make the post-decision steps idempotent on replay. → F-11 | PR #36 ("Follow-up"; reviewer note) |
| OI-12 | Test isolation: a `tests/unit`-only run fails `test_docs_v1.py::test_current_generated_snapshot_matches` ("runtime reference drift: mcp_tools"). It also fails at base `4ed8b11` and passes in full-suite order. | PR #34 ("Verification") |
| OI-13 | Dead-code candidates: `trust_policy_path`/`trust_roots_path` (no callers), and `_decision_mirror_path`/`_load_decision_file` (pinned as uncalled). | PR #37; PR #41 |
| OI-14 | `doctor` emits no custody signal (product gap). It is zero-write. → F-12 | PR #40 |
| OI-15 | AC-TH-05 remaining: `_replace_file`'s internal fsyncs and `os.replace` are not separately faulted; "projection finalization" is not a separate boundary. Reviewer nit: `test_ack_trace_detects_head_written_before_commit` is a coarse call-order check, not a negative control. | PR #43 |
| OI-16 | AC-TH-02 notes for adjudication: genesis enrollment is atomic, with no separate prepare/commit phase. Exact and conflicting enroll retries return the same error, and the operator distinguishes them via `read_current`. The lease check inside the head write itself is not separately witnessed. | PR #44 ("Notes for r3 adjudication") |
| OI-17 | After a lost enroll reply, an exact retry fails closed with "registry already enrolled" although enrollment succeeded (one effect, by design). → F-14 | PR #44 ("Custodian enroll unchanged") |
| OI-18 | AC-TH-07: the enumerator does not track `TrustJournal.reconcile`/`initialize_reviewed_genesis` or direct `TrustJournal(...)` construction. "LEASE" justifications are reviewed claims, not mechanically checked. The race test simulates contention through a seam. `backup.build_recovery_overlay`'s double read is left as-is because the caller holds the lease. → F-07 | PR #46 ("Notes for r3") |
| OI-19 | Bounded fuzz does not cover: real-socket transport; `CustodianService.handle` end to end; the `artifact_transform` payload kind; structured zip member-table mutations; `parse_file` itself. The critical-findings statement and threat-model linkage are delivered as slice B3 (this directory). | PR #47 ("Not covered (recorded for r3)") |

## 3. Dropped (not groundable in a PR body or the code)

- The tracker label "product gap F8" for the doctor custody probe. The gap itself is kept as OI-14.
- "Owner decided test-only for now; revisit after E3" for the doctor probe. No PR body records it.
- The UX proposal to add a read-only "enrollment matches reviewed inputs" check in `custody_admin`. PR #44 records only the behaviour (OI-17).

<!-- provenance: author=IDG scribe (r3 slice B3 follow-up); sources=gh pr view 34..50 --json body, code at bcdf680; date=2026-10-02; reviewer=PENDING -->
