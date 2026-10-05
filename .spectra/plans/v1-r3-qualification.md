---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-05
---
# V1 r3 product gaps and candidate qualification

## Scope
Intent: CHANGE. Theme: evidence-backed V1 qualification. Project: r3 milestone.
In: close documented F-11/OI-11 approval replay and F-12/OI-14 doctor custody gaps; prepare fresh source-bound r3 evidence and an independently reviewed release decision.
Out: GA/tag/publication, merging PR #51, new architecture, account setup, contacting external operators, synthetic independent validation, and accepting High residual risk on a maintainer's behalf.
Deferred: separate-UID deployment, dedicated performance/quality evaluation, external reproduction/pilots, full RC and fetched-distribution matrix wherever this environment cannot produce valid evidence. Record exact missing evidence and owner; never convert absence to PASS.
Base: clean integration 9692735, worktree branch codex/v1-r3-qualification. Primary checkout contains user harness changes and must remain untouched.
Sources: ATLAS audit; PR #51 source 68725d4120569b246503d790bfcaaf85b7a11f0f (unmerged); .spectra/plans/magicite-v1/release-gates.md; S12/S16. Historical r2 binds 9bf9ca0. Linux run 37076396315 binds 9692735 (1714 passed, 10 skipped); CI 37076342098 also binds base, not future fixes.
Assumption: “conscious pipeline” means current native Eidolons role chain plus explicit readiness/candidate ledger. Tool discovery/configuration is not live Gauge/Junction acceptance. No memory MCP available to this planner; no memory persistence claimed.
Clarify skipped for product scope: concrete prior milestone and current audit suffice; user clarification about pipeline naming remains with orchestrator.
Mechanical right-size: full, score 5 (12 files, security, high stakes); complexity 8/12 → extended. Full is planning ceremony, not automatic TRANCE.

## Approach
Use bounded repairs with existing lease, trust-decision, doctor/1 and release-package patterns. Hypothesis score 90.5. Reproduce each defect first, implement minimally, run relevant tests, freeze clean candidate C, then generate/rebind evidence against C. Evidence packaging may be a subsequent commit E whose manifest names C. Validate using clean C with an evidence-only package overlay, or clean E whose non-package tracked tree is proven identical to C; pass that composed root to the validator. Generator/source-tree diff verifies C because the validator does not inspect Git HEAD. Do not pretend untested source changes in E were covered by C.
Pattern match: adapt existing approval concurrency and zero-write custody tests and r2 builder/validator; preserve historical packages byte-for-byte. Audit found no need for a new framework.
Single writer order: Vivi product fixes → independent VIGIL review → Vivi evidence generation/package → independent release-claim review → IDG concise documentation. Scout/checker writes are forbidden. Separate-agent code/package review is native process evidence; it is neither authenticated independent external product validation nor live Gauge acceptance. Handoffs name candidate SHA, dirty-state status and evidence paths.

## Stories
### Story 1: Approval failure preserves truthful retry semantics
As an operator, I want an acknowledged review approval to include required local admission and audit effects so that a lease failure cannot look like a completed approval.
Timebox: 1d. Risk: P0. Executor: standard Vivi; goals, named files and tests below.
Files: src/magicite/core/registry.py; tests/unit/core/test_review_approve_busy_wait.py and existing approval concurrency integration test. Also own src/magicite/core/approvals.py: a bounded optional idempotency key on propose may derive stable audit identity from decision identity and recover the same mirror across mirror-written/DB-failed splits. Root authorized this narrow helper amendment after inspection found registry alone cannot prevent duplicate audit effects. Preserve default behavior for other callers. lifecycle.py changes still require a narrowly justified scope amendment.
Reproduce BusyError after durable decision commit and before local admission/audit. Prefer distinguishing lease-acquisition contention from errors inside the critical section. Existing event replay must validate or complete required local effects under valid ownership before successful return. If safe completion cannot be guaranteed, return explicit failure/reconciliation status; never silently acknowledge incomplete effects. Do not mint a second trust decision or duplicate approval audit. Preserve bounded acquisition retry for callers supplying event_id and fail-fast behavior without it. No unowned writes after lease loss.
Contract: regression reproduction, scoped code diff, exact tests and results, any recovery behavior/compatibility impact.

### Story 2: Doctor diagnoses custody without writes or leakage
As an operator, I want a doctor custody check reporting protected-current and local journal health so that I can identify unavailable/restricted/corrupt/stale custody safely.
Timebox: 1d. Risk: P0. Executor: standard Vivi.
Files: src/magicite/obs/doctor.py; tests/unit/obs/test_doctor.py; tests/unit/obs/test_th_custody_zero_write_canary.py; related read-only custody/journal helper only if required and declared before edit.
Reuse doctor/1 stable check shape/statuses and read-only inspection patterns. custody_admin.status reads protected current only; do not equate it to local journal validation. Distinguish unconfigured/missing, restricted/unavailable, invalid/corrupt local journal, stale local head, and healthy evidence. Add actionable sanitized remediation. Do not create directories, DB/WAL/shm, keys, enrollments, events, heads, projections or perform reconciliation. Sensitive configured socket/custody paths and key material must not leak through the new check or exception strings.
Contract: stable check ID, documented status semantics, zero-write snapshot tests including planted secrets, no unsupported separate-user deployment claims.

### Story 3: R3 candidate evidence and honest decision
As a maintainer, I want a reproducible r3 package tied to its exact candidate so that current observations cannot be mistaken for r2 or universal release readiness.
Timebox: 2d. Risk: P0. Executor: standard Vivi for generator/evidence, separate checker for adjudication, IDG for final prose.
Files: docs/releases/v1/r3/**; optional .github/workflows/r3-evidence.yml only if required for candidate evidence; targeted release-package validator tests if generator behavior changes. Existing r2 builder may be read and adapted, never rewritten.
Produce r3 README, privacy-data-map successor, criterion ledger, release manifest, gate table, raw check metadata/log hashes and generator/reproduction instructions. Obtain PR #51 security records by immutable source reference; clearly label unmerged provenance and pending reviewer decisions. Do not represent inherited documents as new sign-off.
Each witness binds candidate C, source hashes, environment, command, result, log/artifact digests and real verifier identity. Re-run witnesses invalidated by either fix or changed documentation. Prior Linux output is inherited base evidence only unless rerun on C; same-account fixture is not separate-UID custody evidence. Do not rehash stale evidence into a new PASS. Whole-gate PASS needs every required criterion, not a subset.
Preserve AC-TH-10 → test_th10_old_reader_downgrade.py, TH-A01/residual; distinguish inert projection regression guards from active defenses; retain unobserved retry/reconcile fences, real crash/lost-reply/OS branches and fuzz limitations where still absent.
Independent checker evaluates all 17 release gates. SECURITY requires named human assessment/mitigation/expiry for Highs; EXTERNAL needs genuinely independent product evidence; RC-CONTRACT needs two compatible independently built RCs; GA-ALL requires all gates and maintainer approval. Expected decision remains NO-GA with exact unmet gates. Never import r2's 4 PASS/13 UNEVALUATED tally as the r3 result.
Contract: candidate-bound reproducible package with full validator diagnostics and expected NO-GA exit semantics; per-gate evidence and reviewer; explicit unsatisfied dependencies. Local verification limitations are recorded with cause, not hidden by CI history.

## Acceptance Criteria
The authoritative atomic criteria are v1-r3-qualification.acceptance.md, frozen at Assemble. Executor maps every criterion to actual result/evidence. Status starts UNEVALUATED until checked.

## Verification and constraints
Run defect regressions first, then affected unit/integration suites, ruff, mypy, docs/generated contract checks, and full supported-runtime suite when available. Use repository commands/environment. Supported Python 3.12.14 is /Users/henrique/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3; create an isolated /private/tmp environment from the repository lock as needed. Existing Python 3.14 environments do not qualify the declared supported matrix. Full-suite-only test isolation defect is an existing OI-12 limitation, not permission to omit the new focused regressions. New regressions must work individually.
Validate the r3 package with scripts/check_release_manifest.py using its documented invocation and --root pointing to the composed C-plus-evidence workspace defined above. Ensure a mismatched source or modified bound witness cannot validate; use existing meaningful negative tests before adding duplicates. New product files after C require C2 and regenerated affected evidence.
Independent verification covers code-path/lease boundaries, doctor mutation and disclosure boundaries, and every PASS's evidence binding. Source work is complete only when regression criteria pass; package work is complete only when validation has zero malformed-input, digest, source-binding, witness, or unsupported-PASS errors and the independent reviewer agrees with statuses. For expected NO-GA, check_release_manifest.py exit 1 / eligible:false is the correct outcome: its errors must be limited to enumerated unmet-gate, RC, external and sign-off requirements. Do not weaken the validator or demand GA exit 0. Release stays blocked on external criteria.
Self-consistency decomposition: (A) product→tests→candidate→package→review; (B) approval contract→doctor contract→release contract; (C) reproduce→repair→prove→bind→adjudicate. All preserve two product fixes, exact candidate evidence and independent NO-GA decision; no differing scope.

## Confidence
Mechanical ramza-score confidence: 94% → AUTO_PROCEED (pattern 92, clarity 96, decomposition 92, constraints 96). Confidence applies to bounded milestone execution, not V1 release eligibility. Independent ATLAS critique corrected two release-validation contracts through refine cycle 1; see critique sidecar.

## Rejected Alternatives
- Evidence-only with deferred fixes: 78; conservative and fast but leaves known product gaps despite user's next-milestone request.
- Generalized crash-recovery/qualification framework: 75.5; potentially reusable but expands architecture and migration risk beyond these two defects.

## Risks
- P0: treating committed decision as fully applied; assert local effects and audit, with exact replay/fault tests.
- P0: doctor read path mutates custody or leaks secrets; zero-write/secret-planted matrix and sanitized failures.
- P0: stale evidence laundered into r3 PASS; clean candidate, per-witness provenance and independent status review.
- P1: CI/external resources unavailable; complete local work, label exact unrun evidence UNEVALUATED.
- P1: extra implementation helper needed; amend declared scope before edit without inventing requirements.
- P1: pending PR #51 contains human decisions; preserve provenance, do not merge or invent approval.

## Handoff
Receiver: Vivi. Base: 9692735. Worktree: /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite.
Write owner is exclusive per chain step; do not revert other agents' work. Report exact candidate and modified files, criterion result mapping, commands, logs, unresolved criteria and next role.
