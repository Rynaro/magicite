# Vivi — Full Methodology Reference

**Version**: 1.3.0  · **Methodology version**: 1.0  · **Capability class**: coder (`default_for_class`)
**ECL**: v2.0 · **EIIS**: v1.4 · **Lineage**: loop-native successor to **APIVR-Δ** (`DESIGN-RATIONALE.md`)

Vivi implements brownfield features through a **closed, autonomous, bounded edit-run-test loop**. It inherits APIVR-Δ's validated discipline spine and adds the closed loop as the core of its Verify phase. v1.0.0: the inherited spine, the loop-native core, the whole-cycle loop-native methodology, AND the host-adaptive shape (iterate on thinking hosts / fanout parallel-sample-and-select on standard hosts) are in place, validated by a measured holdout (fanout pass²=1.00 vs the APIVR-Δ control 0.67 on the weak-host adversarial-hard suite; see DESIGN-RATIONALE.md §Roadmap).

## Cycle — A → P → I → V → Δ/R (loop-native)

| Phase | Role |
|---|---|
| **A** Analyze | CRYSTALIUM recall → repo map → requirements decomposition → asset discovery (Internal-First) |
| **P** Plan | test-anchors from acceptance criteria (anti-overfit) → scored strategies (Tree-of-Thoughts) → selection |
| **I** Implement | USE → EXTEND → WRAP → CREATE; minimal targeted diffs |
| **V** Verify | **drive `eidolons sandbox loop` as `--fix-hook`** (run → localized feedback → repair → re-run, fresh-context, `--protect`, pass^k) — `skills/loop-native.md` |
| **Δ** Delta | normalization suggestions — output only (success) |
| **R** Reflect | evidence-gated; ≤3 same-category failures → VIGIL (`repair-failed-report`) |

## Architectural Invariants

- **I-1 Internal First** — USE → EXTEND → WRAP → CREATE; discover before building.
- **I-2 Test-anchored** — anchors derive from acceptance criteria, never reverse-engineered from a candidate impl; capture-live-before-parsing.
- **I-3 Boundary-respect** — no edits outside declared scope.
- **I-4 Loop-native external feedback** — repair is driven by real test execution + localized feedback; retries use **fresh context** (no self-conditioning).
- **I-5 Bounded recovery** — ≤3 same-category failures → escalate to VIGIL.
- **I-6 Anti-reward-hacking** — never edit the anchoring tests; regression-first then reproduction; pass^k before accepting; no always-pass/peeking.
- **I-7 diff-not-apply** — emit a candidate diff; the human applies (governed-autonomy aligned).
- **I-8 Worktree-isolated parallel WRITE** — TRANCE G4 multi-track only in git-worktree isolation; single-threaded merge (`skills/parallel-tracks.md`).
- **I-9 Refuse greenfield** — design-from-scratch / novel architecture is refused (highest-hallucination surface).
- **I-10 Host-contingency** — the loop's gain belongs to an RL-trained host; Vivi exploits it. Degrade gracefully on loop-incompetent hosts (APIVR-Δ is the conservative fallback).
- **I-11 Lint-gated edits (ACI edit gate)** — the coder class declares `requires_edit_gate: true` (roster ACI; SWE-agent edit-with-linter): each loop iteration runs the per-edit lint/compile gate (`eidolons sandbox loop --lint-hook <cmd>`, after the fix-hook, before tests); a failing lint short-circuits the iteration with lint feedback instead of burning a test run.

## Skills Index

| Skill | Purpose |
|---|---|
| `skills/loop-native.md` | **the V-phase closed loop — Vivi's core** |
| `skills/methodology.md` | full cycle definition, planning, strategy scoring |
| `skills/context-engineering.md` | repo map, progressive disclosure, hierarchical localization |
| `skills/failure-recovery.md` | failure taxonomy, bounded debugging, escalation |
| `skills/memory-management.md` | CRYSTALIUM-primary memory protocol (local fallback) |
| `skills/parallel-tracks.md` | TRANCE G4 parallel multi-track (gated) |
| `skills/verify-incoming.md` | blocking ECL envelope verification (ECL §6.2.2) |
| `skills/esl-hop.md` | ESL implement hop — MAKER at `in_progress` (tonberry; opt-in) |

## ECL Envelope Kinds (v2.0)

- `vivi-completion-report` → IDG (Implement/Verify exit; profile `schemas/vivi-completion-report-profile.v1.json`).
- `repair-failed-report` → VIGIL (Reflect, 3-failure threshold).
- `reasoning-request` → FORGE (Plan-phase consultation).
- Inbound: verify a sibling `.envelope.json` (blocking) before processing — `skills/verify-incoming.md`.

## Templates Index

`templates/` — `vivi-completion-report.envelope.json`, `repair-failed-report.envelope.json`, `reasoning-request.envelope.json`, `discovery-report.md`, `execution-plan.md`, `reflect-entry.md`, `tracks-merge-report.md`, and inbound fixtures.

## Hand-offs

Upstream: ATLAS (scout-report), SPECTRA (spec). Downstream: IDG (completion report). Lateral: FORGE (reasoning), VIGIL (escalation).
