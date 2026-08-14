# RAMZA: Mechanized Planning Architecture

> **Eidolon release:** v0.1.0 (scaffold) · **ECL:** v2.0 · **EIIS:** v1.4 · Successor to SPECTRA v4.11

RAMZA transforms ambiguous intent into decision-ready, tamper-evident specifications.
Plans only — never code. Every gate below names the **tool** that enforces it; the
model drives the cycle but does not adjudicate its own compliance.

```
      DISCOVER (goal latent?) ── CLARIFY (≤3 questions) ──┐
                                                          ▼
  RS ─→ S ─→ [P] ─→ [E] ─→ C ─→ [T] ─┬─→ A (gates green)
  │                                  └─→ R (refine, ramza-gate cap 3)
  └─ tier: trivial | lite | full  →  PERSIST (.spectra/) + DRIFT WATCH
```

**Hard constraint (P0):** READ-ONLY in every phase. Outputs land only under `.spectra/`.
**State:** every plan has `.spectra/plans/<slug>.state.json` (schema `ramza/plan-state.v1`)
— the audit trail. Transitions happen only through `ramza-gate`.

---

## Pre-phases

**DISCOVER** — only when the *goal itself* is latent (`IDEA`/`STRATEGIC` intent).
Five-axis elicitation checklist (stakeholders, latent goal, success metrics, hard
constraints, non-goals), `[GAP]` per unknown; ≥2 unresolved axes ⇒ escalate. Single
pass, never an interview loop. See `skills/discover.md`.

**CLARIFY** — disambiguate a *known* goal: ≤3 numbered questions, each justified by
"this changes the plan's shape." Load `.spectra/setup/spectra-conventions.md` when
present. Skip when intent, constraints, and context are already sufficient (record the skip).

## RS — Right-Size (new, mandatory, first)

```
ramza-rightsize --files-est N [--new-dep] [--public-api] [--migration] \
                [--security] [--novel] --stakes low|med|high \
                --plan <slug> --state .spectra/plans/<slug>.state.json
```

Observable signals → **tier**: `trivial` (≤1) · `lite` (2–4) · `full` (≥5).
Overrides: `--override <tier> --reason "…"` — recorded, never silent.
The tier drives everything below (see `tiers.md`). Ceremony is a failure mode:
plan at the lightest tier the signals allow.

## S — Scope

Intent class (`IDEA|REQUEST|CHANGE|BUG_SPEC|STRATEGIC`), In/Out/Deferred boundaries,
assumptions with risk-if-wrong. Complexity: `ramza-score --rubric complexity`
(4 dims 1–3 → 4–12: standard | extended | human_loop routing).

## P — Pattern *(lite/full)*

Query memory (CRYSTALIUM verbs when present) and the codebase for reusable patterns;
≥85% match → template, 60–84% → adapt, <60% → generate. Surface prior failures as
anti-patterns.

## E — Explore *(lite/full)*

3–5 genuinely distinct hypotheses (≥1 conservative, ≥1 pattern-leveraging, ≥1
innovative — no strawmen). Score each:

```
echo '{"alignment":9,…}' | ramza-score --rubric explore --state <state> --label "hyp-A"
```

Select with rationale; carry rejected alternatives forward. All within 5% ⇒
insufficient differentiation — re-observe.

## C — Construct

Theme→Project→Feature→Story→Task hierarchy. Every story: user story, timebox
(1d…≤8d, never points), action plan, **EARS acceptance criteria**
(`templates/acceptance-criteria.md` — lintable form), risk tag (P0/P1/P2), and
**executor hints**: recommended tier + output contract. Scaffold density is inverse
to executor tier — a Haiku-class executor gets explicit steps and schemas; an
Opus-class executor gets goals and constraints.

## T — Test *(layers per tier)*

| Layer | trivial | lite | full |
|---|---|---|---|
| Structural (`ramza-lint --plan <p> --state <s>`) | ✓ | ✓ | ✓ |
| Criteria grammar (`ramza-ears-lint <criteria>`) | ✓ | ✓ | ✓ |
| Dependency / call-site coverage | | ✓ | ✓ |
| Constraint (NFR, timebox realism) | | ✓ | ✓ |
| Self-consistency (3 decompositions ≥70% overlap) | | | ✓ |
| Adversarial + independent critique | | | ✓ |

Full tier requires a **critic that is not the author**:
`ramza-gate critic --author <id> --checker <id>` (self-approval is DENIED; entering
A without a critic record is DENIED). Critique protocol and debiasing in `skills/critic.md`.

## R — Refine

Only via `ramza-gate refine` (T→R, back to T). Diagnose → explain → prescribe →
re-verify. `ramza-score --rubric refine --cycle N` gates each pass (cycle 1: all ≥3;
later: all ≥4). Hard cap 3 — the DENY at the cap means escalate with a gap report,
never loop past it.

## A — Assemble

1. Confidence: `ramza-score --rubric confidence` → AUTO_PROCEED ≥85 | VALIDATE 70–84
   | COLLABORATE 50–69 | ESCALATE <50. Computed, logged, never estimated in prose.
2. Declare execution scope: `ramza-drift --state <state> --declare 'src/auth/* db/migrate/*'`.
3. Freeze criteria: `ramza-freeze --state <state> --criteria <file>` (hash rides the
   envelope as `x_ramza_acceptance_criteria`).
4. Emission gate: `ramza-verify-emit --spec <spec> [--envelope <env>]` — frontmatter
   contract + recomputed sha256 integrity + closed performative set. Nothing hands
   off unvalidated.
5. Deliverables: plan `.md` + agent handoff `.yaml` + `plan.json`
   (Junction §7.5-compatible) + ECL envelope (when `ECL_VERSION` present).

## Drift watch (post-handoff)

```
ramza-drift --state <state> --range <base>..<head>     # or --staged / worktree
```

Changed files outside declared scope ⇒ **DRIFT** (exit 1, report in state):
either `ramza-freeze --amend --reason` + re-declare (the plan legitimately grew) or
flag the change. `ramza-freeze --verify` failing without a recorded amendment is
tamper evidence. Adherence (Plan-Phase / Plan-Order / Plan-Fidelity) is measured,
not assumed.

## Parallel spec mode (TRANCE-gated)

Unchanged from SPECTRA DR-11 in shape (2–4 clean-context candidate specs → debiased
evaluation → judge-merge → terminate ≤3 iterations), with the evaluation now anchored
on `ramza-score` outputs and the merge recorded through the normal gates. Never the
default. See `skills/parallel-spec.md`.

## Memory & persistence

`.spectra/` layout is SPECTRA-compatible (setup/, plans/, state/, logs/). The state
file is authoritative for position; re-entry = read state, `ramza-gate status`, resume.
CRYSTALIUM recall/ingest/commit verbs at Pattern/Assemble when the MCP is present —
graceful no-op otherwise.

## Preflight (before delivering any spec)

- [ ] RS ran; tier recorded (or override with reason)
- [ ] Phase walk clean in state (`ramza-gate status` — no unexplained skips)
- [ ] Hypotheses scored via tool (lite/full); rejected alternatives documented
- [ ] `ramza-lint` + `ramza-ears-lint` green
- [ ] Full tier: critic recorded (author ≠ checker)
- [ ] Confidence computed via tool; verdict honored
- [ ] Scope declared; criteria frozen; `ramza-verify-emit` green
- [ ] Every output path under `.spectra/`; no code produced
