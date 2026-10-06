---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-10-06
---
# Bounded V1 hardening

## Scope
CHANGE on integration c7e0789c07705f48131b0a0e0d0c19f2f68d28fe. Address F-14 enrollment-reply UX, F-18 MCP export containment test gap, OI-12 generated-reference import-order isolation; assess F-16/F-17 without implementing broader model acquisition or scanner policy changes.
Source: ATLAS refreshed live base and PR #51 b08aec4 findings register (still unmerged, human High sign-off pending). No PR #51 merge, human risk acceptance, new public APIs, trust authority changes, enrollment reset/reconciliation, or edits to immutable docs/releases/v1/** evidence. Primary dirty harness remains untouched.
Mechanical right-size: lite score3 (8 files, security, medium stakes); complexity7/12 extended. No full planning ceremony or independent plan-critic requirement. Separate implementation review remains mandatory. 30–75 minutes is a working execution budget, not a completion claim or verification cap.

## Approach
Adapt existing fail-closed custody errors, MCP containment controls and generated-reference checks. Selected hypothesis90: bounded UX/regressions/isolation/assessment. Rejected regression+docs-only78.5 leaves operator ambiguity; generalized recovery/reference framework75 expands authority/API scope unnecessarily.
F14: core/custody_admin.py enrollment checks reviewed bytes then calls CustodianStore.enroll; duplicate enrollment remains rejected. Provide accurate actionable guidance for ambiguous lost replies / already-enrolled retries, using existing read-only custody status/inspection. Never claim original genesis succeeded or matches reviewed inputs merely because current policy matches: authority may have advanced. No secret/path/raw exception leakage and no misleading blanket 'enrollment failed' diagnosis after a committed enrollment. Keep error category and repeat-call failure compatibility.
F18: registry.export uses _resolve_scan_root; no demonstrated bypass. Test through actual MCP export entry for absolute, parent-relative and symlink escapes, asserting typed failure and no writes outside project. ensure_dirs precedes guard, so no global zero-write promise. Keep normal in-project export working.
OI12: ATLAS reproduced fresh-process `from magicite.mcp import bind_inspect; import check_generated_docs as g; g.check()` yielding mcp_tools drift; isolated existing test passes. Decorator arrival order in mcp/registry.py explains ordering-only false drift. Normalize reference comparison order or isolate a fresh reference process; preserve runtime public manifest ordering and tool identities/content/schema checks. Do not solve by test reordering, expected drift suppression, or weakening snapshot validation.
F16: inspect fetch-model CLI/TextEmbedding model acquisition; distinguish model-name selection from immutable revision/digest verification. F17: separate all-severity SARIF visibility from HIGH/CRITICAL enforcement with ignore-unfixed:true. Assess actual evidence and bounded next action; no invented exploit, severity acceptance, SECURITY PASS, or claim reports are already packaged.

## Stories
1. As an operator, I need truthful enrollment retry guidance without changing authoritative enrollment behavior. Timebox1d; riskP1; executor standard Vivi. Own src/magicite/core/custody_admin.py and tests/unit/core/test_custody_admin.py; run existing test_th_retry_matrix.py lost-reply one-effect and duplicate-rejection cases unchanged.
2. As a maintainer, I need MCP export containment witnessed at its exposed boundary. Timebox1d; riskP1; executor standard Vivi. Own tests/unit/mcp/test_export_containment.py (new) or existing tests/unit/mcp/test_registry.py if better fixture fit. Production export changes only if a real defect is reproduced and scope amended.
3. As a maintainer, I need generated docs checks independent of prior imports while still detecting real reference drift. Timebox1d; riskP1; executor standard Vivi. Own scripts/check_generated_docs.py and tests/unit/test_docs_v1.py. Avoid production mcp/registry.py edits because public ordering is not this slice's contract.
4. As a release reviewer, I need source-grounded F16/F17 assessments without implied risk acceptance. Timebox1d; riskP2; executor Vivi findings + separate checker, then IDG prose if needed. Own docs/security/bounded-hardening-assessment.md (new additive document; does not depend on merging PR51).
Timeboxes are methodological maxima; execute as one bounded slice with exclusive writer ownership. Independent checker reviews custody authority, MCP outside-write evidence, meaningful negative controls and assessment claims.

## Acceptance Criteria
Authoritative atomic criteria are v1-bounded-hardening.acceptance.md. Freeze and transport hash supplied at handoff. All start UNEVALUATED until actual tests/review pass.

## Verification
Run F14/F18 focused tests and two fresh-process OI12 permutations, then affected suites, ruff/mypy and generated-reference checks. At least one negative control must demonstrate changed tool metadata/schema still yields drift; preserve exact sixteen unique tools. Run supported Python3.12 full suite once for final candidate and hosted checks for submitted PR. Preserve existing enrollment retry/one-effect tests.
Changes outside the declared paths need a bounded recorded scope amendment, not an inferred user permission flow. Separate checker must confirm original authority/public APIs remain unchanged. Freeze final tested source SHA and attach commands/results to handoff. No revision of r3 evidence to make it appear current for this new candidate; new results are a separate slice record.

## Confidence
Mechanical confidence95.75% → AUTO_PROCEED is recorded in state at Assemble. It concerns bounded execution, not release eligibility. Known exact repro and existing APIs constrain uncertainty.

## Handoff
Receiver Vivi. Worktree /Users/henrique/.codex/worktrees/v1-r3-qualification/magicite, branch codex/v1-bounded-hardening at clean c7e0789. No source edits by RAMZA. Return source SHA, scoped diff, criterion→result mapping, actual commands, independent review findings, and unresolved risks. F16/F17 assessment may legitimately leave observations UNEVALUATED pending evidence; do not treat documentation as resolution.
