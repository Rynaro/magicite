# Magicite v1 security findings register

**Gate obligations:** SECURITY `critical-resolved`, and the high-finding rule:
"no unresolved critical finding. Every high finding has a named reviewer,
exploitability assessment, mitigation and expiry; exceptions remain visible"
(`.spectra/plans/magicite-v1/release-gates.md:24, 37`; restated in `SECURITY.md:3-5`).
**Scope:** `codex/v1-integration` at `bcdf680`. The r3 evidence package must re-bind
this register to its own source commit. The companion document is the
[threat model](threat-model.md).

> **`critical-resolved` is NOT claimed.** Reviewers:
>
> - **High findings (F-02, F-09, F-10, F-19):** Henrique Aparecido Lavezzo (maintainer).
>   Sign-off is recorded by approving the B3 PR that introduces this register. Until
>   that approval, it is pending. This register does not mark it signed.
> - **All other findings:** PENDING independent security review.
>
> Severities are the author's provisional assessments. SECURITY (including
> `critical-resolved`) stays **UNEVALUATED** until the maintainer's sign-off is
> recorded **and** the r3 evidence package adjudicates the gate. The recorded gate
> status is SECURITY UNEVALUATED (`docs/releases/v1/r2/gate-table.md:22`). No
> third-party security certification is claimed (`release-gates.md:37`).

**Format.** This register is Markdown only. The gate text names no machine-readable
format. If the r3 package needs one for its criterion ledger, derive it from this table.

## Severity scale (provisional)

| Severity | Meaning |
|---|---|
| Critical | An in-scope actor defeats a core guarantee (a revocation, custody authority or confidentiality of custody keys) without any custodian key. |
| High | An in-scope actor bypasses a governance or integrity control with preconditions, or a disclosed residual re-exposes revoked content. |
| Medium | Fail-closed outcome but an untyped or uninformative error; a consistency window; or an audit gap with no shown authority bypass. |
| Low | Availability or diagnosability only. |
| Info | Observation. No product defect. |
| Unrated | Not assessed. Needs a reviewer. |

## Status meanings

- **Fixed:** a code change is merged into `bcdf680`, with in-repo regression evidence.
- **Accepted:** an owner-recorded decision with a disclosed residual.
- **Open:** a known issue with no fix.
- **UNEVALUATED:** no evidence either way.

"Tested in-repo" never means deployment-qualified (threat model §1). `OI-nn` items are recorded
in [r3-open-items.md](r3-open-items.md), each grounded in a merged PR body.

## Register

| ID | Title | Severity | Attacker capability / exploitability | Component | Status | Fix | Regression evidence | Reviewer | Expiry |
|---|---|---|---|---|---|---|---|---|---|
| F-01 | Revocation bypass: deleting the revoke decision mirror re-admits a revoked engram (original v1 S16 FAIL) | Critical | `same-uid-local-writer`. Reproduced at `c0782fd`: `admitted_after_revoke_mirror_deleted: true` (`docs/releases/v1/evidence/trust-mirror-loss.json`; SECURITY FAIL, `docs/releases/v1/gate-table.md:22`) | Trust history (`core/trust.py`, `core/trust_journal.py`) | Fixed. `NOT_REPRODUCED` at `9bf9ca0` under fixture custody (`docs/releases/v1/r2/evidence/trust-mirror-loss-custody.json`) | PR #32 (merge `9bf9ca0`, custodian-held journal) | TAC-035 to TAC-040; `docs/releases/v1/r2/evidence/reproduce-trust-mirror-loss-custody.py` | PENDING independent security review | n/a |
| F-02 | Governance-evidence fail-open: a truncated or unreadable approval mirror reopened fresh-install policy routing with no governance fence | High | **Exploitability:** requires `same-uid-local-writer` write access to `approvals/` while `state.json` is missing. The attacker truncates a `policy_activate`/`policy_rollback` mirror, which reopened `config_fresh_install` routing with no governance fence (commit `11a693e`; PR #45). No custodian key is needed | `core/policy_store.py:131-180`, `core/router.py:1279-1290` | Fixed. **Mitigation:** unreadable, truncated, non-object or invalid-UTF-8 mirrors raise `InvalidInputError`, and route maps that to the fail-closed `policy_store_corrupt` outcome | `11a693e` (PR #45) | `tests/unit/core/test_policy_governance_evidence.py::test_truncated_json_mirror_fails_closed_with_typed_error`, `::test_unreadable_mirror_fails_closed_with_typed_error`; `tests/unit/core/test_router_policy.py::test_malformed_mirror_without_store_is_policy_store_corrupt` | Henrique Aparecido Lavezzo (maintainer). Sign-off is recorded by approving the B3 PR that introduces this register; until that approval, the sign-off is **pending** | No exception outstanding (fixed). Re-verify at the r3 rebind |
| F-03 | Non-object or invalid-UTF-8 approval mirrors raised raw `AttributeError`/`UnicodeDecodeError`; memo pruning could raise during a concurrent insert | Medium | `same-uid-local-writer` | `core/policy_store.py` | Fixed | `1a249ac` (PR #45) | `test_policy_governance_evidence.py::test_non_object_mirror_fails_closed_with_typed_error`, `::test_invalid_utf8_mirror_fails_closed_with_typed_error`, `::test_prune_survives_cache_mutation_during_scan` | PENDING independent security review | n/a |
| F-04 | Custodian payload validation raised an untyped `AttributeError` for non-object policy roots (found by fuzzing) | Medium | `wire-relay` or a malformed record reaching `_validate_payload` | `core/trust_custodian.py:388-402` | Fixed (`CustodianError`) | `76401bb` (PR #47) | `tests/unit/core/test_fuzz_regressions.py::test_policy_non_dict_or_malformed_root_raises_custodian_error`; TAC-107; fuzz `custodian._validate_payload:policy_snapshot` | PENDING independent security review | n/a |
| F-05 | Bundle extraction leaked untyped zip errors with echoed parser text and left staging directories after a failed `verify_bundle` (found by fuzzing) | Medium | `malicious-bundle-author` supplies a corrupt archive | `core/bundles.py:233-260, 437-441` | Fixed (fixed `InvalidInputError` text; staging and outer temporary directory removed) | `76401bb`, `a9c5564` (PR #47) | `test_fuzz_regressions.py::test_corrupt_archive_raises_invalid_input_and_leaves_no_staging`, `::test_manifest_json_error_does_not_leak_staging`; TAC-083; fuzz `bundles.verify_bundle` | PENDING independent security review | n/a |
| F-06 | Genesis init interrupted between journal and head left reads closed until an explicit reconcile | Low | `crash-and-withhold` (fail-closed; availability only) | `core/trust_journal.py:374-400` | Fixed (resumes only over a byte-identical genesis journal) | `363c5c6` (PR #44) | `tests/unit/core/test_th_retry_matrix.py::test_genesis_init_resumes_after_crash_between_journal_and_head`, `::test_genesis_init_refuses_nonidentical_partial_state` | PENDING independent security review | n/a |
| F-07 | Double trust snapshot: `trust_view_for` read two snapshots with no lease, and `record_pending_intake` bound policy outside the append lease | Medium | `concurrent-stale-writer` or a concurrent writer moves the head between reads | `core/registry.py:1948-1975`, `core/trust.py:577-600` | Fixed (one validated snapshot per operation). Enumerator gaps remain (OI-18, PR #46) | `e038efd` (PR #46) | `tests/unit/core/test_th_single_snapshot_sites.py` (AST enumeration and drift tests) | PENDING independent security review | n/a |
| F-08 | `review_approve` idempotent replay had a fixed 32×10 ms retry budget and failed under contention | Low | Contention only (availability) | `core/registry.py:1803-1874` | Fixed (10 s deadline with jittered backoff) | `5fed9b4` (PR #36) | `tests/unit/core/test_review_approve_busy_wait.py` | PENDING independent security review | n/a |
| F-09 | Journal append window: an external same-UID write between the identity check and the post-write `fstat` was absorbed into the cached verified identity | High | **Exploitability:** requires a `same-uid-local-writer` racing an in-process append, inside the window between the pre-write identity check and the post-write `fstat`. The external write was absorbed into the cached verified identity for the process lifetime (commit `9b79f0f`) | `core/trust_journal.py:241-268` | Fixed. **Mitigation:** the post-write file must be the same inode with exactly the expected size, and the cache entry is dropped before any local write. **Disclosed residual (untested in-repo):** a same-size in-place overwrite inside the window is absorbed into the cached identity and is caught only when the cache drops and a cold re-verify fails closed (`core/trust_journal.py:259-262`) | `9b79f0f` (append-window fix, PR #32, before r2). PR #49 (`7d4058b`) only corrected the residual comment and added the cache-drop and flock-release tests | `tests/unit/core/test_trust_journal.py::test_concurrent_local_write_during_append_window_closes_and_drops_cache` (size-changing write; cache-drop assertion added in PR #49). The same-size in-window residual has no witness: TAC-053 splices outside the window | Henrique Aparecido Lavezzo (maintainer). Sign-off is recorded by approving the B3 PR that introduces this register; until that approval, the sign-off is **pending** | Residual: At the v1 GA decision or 2027-01-31, whichever comes first. Must be re-reviewed before any GA sign-off |
| F-10 | TH-A01 residual: a pre-hardening (`22ae4e0` or earlier) binary on hand-rolled-back bytes routes and discloses a subject revoked after migration | High | **Exploitability:** requires the operator or a `same-uid-local-writer` to hand-copy pre-migration bytes over the registry **and** run a pre-hardening (`22ae4e0` or earlier) binary. The supported runtime on the same bytes still enforces the revocation (TH-A01 evidence c2; PR #35) | Downgrade / old-binary path | Accepted residual (owner decision, `.spectra/plans/magicite-v1-trust-hardening/amendments/TH-A01-ac-th-10-supported-downgrade-scope.md:3, 7, 18`). **Mitigation:** operator procedure. Never run pre-hardening binaries on a hardened registry; restore only via `magicite migration restore` or the supported backup restore (`docs/operations.md:486-492`) | Scope amendment `a239e4a` (PR #35); no code fix possible | `tests/integration/test_th10_old_reader_downgrade.py` (supported half); TAC-042, TAC-061 to TAC-064 | Henrique Aparecido Lavezzo (maintainer). Sign-off is recorded by approving the B3 PR that introduces this register; until that approval, the sign-off is **pending** | At the v1 GA decision or 2027-01-31, whichever comes first. Must be re-reviewed before any GA sign-off |
| F-11 | `review_approve`: a `BusyError` raised inside the critical section after the decision commit is retried, and the `event_id` replay returns the committed decision early, so `apply_local_admission` and the approval audit are skipped | Medium | Contention after commit (made more reachable by F-08's 10 s wait). No authority bypass shown: the admission decision is committed; the local admission flag and audit row are missing | `core/registry.py:1828-1832, 1853-1874` | **Open** (OI-11; reviewer note in PR #36) | None | None | PENDING independent security review | Not applicable (open, not accepted) |
| F-12 | Doctor has no custody probe. Restricted state, sensitive paths and stale or corrupt local state are not diagnosed by custody status (AC-TH-11 partial) | Low | Diagnosability only. Doctor is zero-write (`test_th_custody_zero_write_canary.py::test_doctor_and_custody_status_write_nothing_under_custody_states`) | `obs/doctor.py` | **Open** (OI-09, OI-14; PR #40) | None | Partial: `tests/unit/obs/test_th_custody_zero_write_canary.py` | PENDING independent security review | Not applicable |
| F-13 | Separate-UID custodian deployment (Linux/macOS) not qualified; Darwin `acl_get_fd_np` and Linux `listxattr` error branches untested | Unrated | `foreign-uid-peer`, `misconfigured-deployment` | `core/trust_custodian_transport.py:36-111` | UNEVALUATED (OI-06, PR #38) | n/a | Fixture only: TAC-029, TAC-030, TAC-139 to TAC-143; `tests/unit/core/test_th_peer_platform_fixtures.py` | PENDING independent security review | n/a |
| F-14 | A lost enroll reply makes the operator see "already enrolled" although enrollment succeeded | Low | `crash-and-withhold` (UX; fails closed) | `core/custody_admin.py:68` (`enroll`) | **Open** (OI-17, PR #44) | None | `test_th_retry_matrix.py::test_genesis_lost_enroll_reply_exact_retry_has_one_effect` (one-effect property) | PENDING independent security review | n/a |
| F-15 | Custody wire JSON accepts duplicate keys (last wins) | Info | `wire-relay`. Not exploitable as an authority path per `tests/fixtures/adversarial/trust/manifest.json:71-75` | `core/trust_custodian_transport.py` (`receive_frame`/`receive_message`) | Accepted observation (no defect recorded) | n/a | Bundle manifests do reject duplicates: TAC-102 | PENDING independent security review | n/a |
| F-16 | **Scribe observation:** `magicite fetch-model` has no in-repo digest or revision pin for the downloaded model | Unrated | Network or upstream attacker at acquisition time only | `src/magicite/__main__.py:186-195`, `embeddings/fastembed_provider.py` | UNEVALUATED (scribe observation; needs a reviewer) | n/a | None | PENDING independent security review | n/a |
| F-17 | **Scribe observation:** Vulnerability scanner evidence is not in any release package. The CI gate uses `ignore-unfixed: true` (`.github/workflows/ci.yml:251-258`, `ignore-unfixed` at `:258`); the full-severity SARIF is uploaded (`ci.yml:236-249`). `SECURITY.md:5` says "scanner failures block integration", but with `ignore-unfixed` an unfixed HIGH/CRITICAL does not block | Unrated | n/a (evidence handling; `release-gates.md:37`: scanner settings must not hide unfixed findings in the evidence report) | CI and release workflow | UNEVALUATED | n/a | None in `docs/releases/v1/**` | PENDING independent security review | n/a |
| F-18 | **Scribe observation:** no test witnesses path-escape refusal for the MCP `export` tool's `out_dir`. `register` is tested; `export` reaches the same `_resolve_scan_root` (`core/registry.py:305-313, 1074-1075`) untested | Low | Compromised MCP host passes an escaping `out_dir`. The control exists in code, so exploitability is not shown | `mcp/bind_registry.py:107`, `core/registry.py:1074-1075` | **Open** (test gap) | None | None (`tests/unit/core/test_registry_core.py::test_register_rejects_path_outside_project` covers `register` only) | PENDING independent security review | Not applicable |
| F-19 | **Scribe observation:** autonomous mode (`MAGICITE_AUTONOMOUS`, `config.py:440`; default `False`, `config.py:287`) lets an MCP host self-approve and execute R3 lifecycle mutations (`mcp/bind_lifecycle.py:146-152`) | High | **Exploitability:** requires an operator to opt in to autonomous mode **and** a compromised or prompt-injected MCP host. The host can then bypass the approval gate for sharpen, promote and archive. Revival never auto-executes (`tests/unit/mcp/test_bind_lifecycle.py::test_promote_revival_never_auto_executes_even_under_autonomous_mode`). Trust, policy and backup operations stay CLI-only (`docs/operations.md:470-473`) | `config.py`, `mcp/bind_lifecycle.py` | Accepted residual (owner decision 2026-10-02). **Mitigation:** opt-in via `MAGICITE_AUTONOMOUS`, off by default, and assumes a trusted MCP host. Revisit at GA | n/a | `test_bind_lifecycle.py::test_sharpen_autonomous_mode_applies_the_patch_and_bumps_version` (behaviour, not a control) | Henrique Aparecido Lavezzo (maintainer). Sign-off is recorded by approving the B3 PR that introduces this register; until that approval, the sign-off is **pending** | At the v1 GA decision or 2027-01-31, whichever comes first. Must be re-reviewed before any GA sign-off |

Component paths are relative to `src/magicite/` unless they start with another root.

## Counts

| Severity | Fixed | Accepted | Open | Needs owner decision | UNEVALUATED | Total |
|---|---|---|---|---|---|---|
| Critical | 1 (F-01) | 0 | 0 | 0 | 0 | 1 |
| High | 2 (F-02, F-09) | 2 (F-10, F-19) | 0 | 0 | 0 | 4 |
| Medium | 4 (F-03, F-04, F-05, F-07) | 0 | 1 (F-11) | 0 | 0 | 5 |
| Low | 2 (F-06, F-08) | 0 | 3 (F-12, F-14, F-18) | 0 | 0 | 5 |
| Info | 0 | 1 (F-15) | 0 | 0 | 0 | 1 |
| Unrated | 0 | 0 | 0 | 0 | 3 (F-13, F-16, F-17) | 3 |
| **Total** | **9** | **3** | **4** | **0** | **3** | **19** |

Scribe observations (F-16 to F-19) were raised while writing this register. They are
not plan or review findings and need a reviewer's assessment.

No critical finding is known to be open at `bcdf680`. That is **not** the same as
`critical-resolved`. SECURITY stays UNEVALUATED until the maintainer's sign-off is
recorded and the r3 evidence package adjudicates the gate. The unrated items could
also change the counts.

## What would change the status

1. The maintainer approves the B3 PR, which records the sign-off for F-02, F-09, F-10 and F-19.
2. The r3 evidence package adjudicates SECURITY against its bound source.
3. Before any GA sign-off, F-10, F-19 and the F-09 residual are re-reviewed (expiry: at the v1 GA
   decision or 2027-01-31, whichever comes first).
4. F-11 is resolved or explicitly accepted. F-13, F-16 and F-17 are rated. The F-18
   test is added.
5. Scanner output, with unfixed findings visible, is attached to the r3 evidence.

<!-- provenance: author=IDG scribe (r3 slice B3); sources=git log 9bf9ca0..bcdf680 and commit messages, release-gates.md:24/37, SECURITY.md, docs/releases/v1 and r2 evidence, TH-A01, adversarial manifest, r3-open-items.md (PR bodies #34-#50); date=2026-10-02; reviewer=PENDING -->
