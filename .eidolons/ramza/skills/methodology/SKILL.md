---
name: ramza-methodology
description: "Decision-ready, tamper-evident specifications via the RAMZA cycle. RS right-sizing runs first — every downstream gate (score, lint, freeze, drift, emit) is a bin/ramza-* tool call, never self-policed arithmetic. Use for any task needing a spec before implementation; ceremony scales with the RS tier (trivial/lite/full), never a fixed complexity threshold. Produces a plan .md + plan-state.json audit trail (+ YAML/JSON block, + ECL envelope when ECL_VERSION is present), never code."
metadata:
  methodology: RAMZA
---

# RAMZA — Methodology Skill

Use this skill when the user needs a specification before implementation. This is
the routing card — see `SPEC.md` for the full architecture,
`docs/methodology/tiers.md` for the tier table, `docs/methodology/scoring.md` for
rubric definitions.

## Memory pre-flight (cross-reference)

On activation, recall fires in `PERSONA.md` before RS — prior specs, decisions, and
patterns are folded into context before this skill runs. See `PERSONA.md`
"Memory & persistence" for the CRYSTALIUM recall call signature.

If `mcp__crystalium__*` tools are unavailable, skip silently — RAMZA is
EIIS-standalone-conformant and works without CRYSTALIUM.

---

## Activation signals

- Any request needing a plan before code — RS decides how much ceremony, not the caller
- Complex features, multi-component/service changes, ambiguous requirements needing structured decomposition
- Any task where "just start coding" would likely require significant rework

Do not gate activation on a guessed complexity score — that arithmetic belongs to
`ramza-rightsize`/`ramza-score`, run below, never estimated in prose.

## RS first — right-size before anything else

Every plan starts here, no exceptions:

```
ramza-rightsize --files-est <N> [--new-dep] [--public-api] [--migration] [--security] [--novel] \
                --stakes low|med|high --plan <slug> --state .spectra/plans/<slug>.state.json
```

Prints `trivial | lite | full` and initialises the state file (`schema:
ramza/plan-state.v1`). The tier is the plan's budget, not a suggestion:

| | trivial | lite | full |
|---|---|---|---|
| Phases (gate-enforced) | RS S C A | RS S P E C T A | RS S P E C T A |
| Hypotheses (E) | — | 3 | 3–5 |
| Verification layers (T) | structural + criteria lint | + dependency, constraint | + self-consistency, adversarial |
| Independent critic (maker≠checker) | — | recommended | **required before A** |
| Plan budget (`ramza-lint`) | ≤120 lines | — | — |
| Required sections | Scope, Approach, Acceptance Criteria | + Stories, Confidence | + Rejected Alternatives, Risks |
| Refine cap | 3 | 3 | 3 |

Full table + executor-scaffold doctrine: `docs/methodology/tiers.md`. Overriding
the computed tier is legitimate — `--override <tier> --reason "…"` — but never
silent; it lands in `rightsize.override` in the state file.

Advance every phase through `ramza-gate advance --state <state> --to <PHASE>` (add
`--reason` when the target skips a tier-mandatory phase — the skip is recorded in
`skips[]`, never silent). `ramza-gate status --state <state>` reports tier, current
phase, and next mandatory phase at any point; `ramza-gate next --state <state>`
prints just the next phase.

## The cycle (phase semantics inherited from SPECTRA; gates now mechanized)

DISCOVER/CLARIFY (as needed) → **RS** → **S**cope → **P**attern → **E**xplore →
**C**onstruct → **T**est → (**R**efine ×≤3) → **A**ssemble

READ-ONLY during all phases — produce specifications, never code.

### S — Scope

Intent class (`IDEA|REQUEST|CHANGE|BUG_SPEC|STRATEGIC`), In/Out/Deferred
boundaries, assumptions with risk-if-wrong. Score complexity through the tool,
never in prose:

```
echo '{"scope":2,"ambiguity":2,"dependencies":2,"risk":2}' \
  | ramza-score --rubric complexity --state <state>
```
→ 4–6 standard · 7–9 extended reasoning · 10–12 human_loop routing.

### P — Pattern *(lite/full)*

Query memory (CRYSTALIUM verbs, see below) and the codebase for reusable
patterns; ≥85% match → template, 60–84% → adapt, <60% → generate. Surface prior
failures as anti-patterns. No tool gate here — Pattern is judgment; Scope,
Explore, and Confidence are arithmetic.

### E — Explore *(lite/full)*

3–5 genuinely distinct hypotheses (≥1 conservative, ≥1 pattern-leveraging, ≥1
innovative — no strawmen). Score each through the tool, never eyeballed:

```
echo '{"alignment":9,"correctness":8,"maintainability":7,"performance":7,"simplicity":6,"risk":7,"innovation":5}' \
  | ramza-score --rubric explore --state <state> --label "hyp-A"
```
`elite` ≥85 · `solid` 70–84 · `weak` <70 (exit 1 — rework or drop). All
candidates within 5% of each other ⇒ insufficient differentiation, re-observe.

### C — Construct

Theme→Project→Feature→Story→Task hierarchy. Every story: user story, timebox
(1d…≤8d, never points), EARS acceptance criteria (`templates/acceptance-criteria.md`
— the exact form `ramza-ears-lint` parses), risk tag (P0/P1/P2), and executor
hints (recommended tier + output contract). Scaffold density is inverse to
executor tier (`docs/methodology/tiers.md`).

### T — Test

```
ramza-lint --plan <plan.md> --state <state>       # structural completeness, tier-aware
ramza-ears-lint <criteria.md>                     # EARS grammar + one-assertion-per-criterion
```

Full tier only — independent critique (never self-approved), then:

```
ramza-gate critic --state <state> --author <you> --checker <critic-id>
```

See `skills/critic/SKILL.md` for the full debiasing protocol. `ramza-gate advance
--state <state> --to A` DENIES entry to Assemble at tier=full without a recorded
critic.

### R — Refine

Only via the gate — T→R, back to T:

```
ramza-gate refine --state <state>
echo '{"clarity":4,"completeness":4,"actionability":4,"efficiency":3,"testability":4}' \
  | ramza-score --rubric refine --state <state> --cycle <N>
```
Cycle 1 passes at all dims ≥3; cycles 2–3 need all dims ≥4. Hard cap 3
(`ramza-gate refine` DENYs past it) — escalate with a gap report, never loop
past the cap.

### A — Assemble

```
echo '{"pattern_match":90,"requirement_clarity":85,"decomposition_stability":88,"constraint_compliance":92}' \
  | ramza-score --rubric confidence --state <state>
ramza-drift --state <state> --declare 'src/auth/* db/migrate/*'
ramza-freeze --state <state> --criteria <criteria.md>
ramza-verify-emit --spec <spec.md> [--envelope <envelope.json>]
```
Confidence verdict: ≥85 AUTO_PROCEED · 70–84 VALIDATE · 50–69 COLLABORATE (halt,
ask) · <50 ESCALATE (exit 1, gap report). All four calls above are mandatory
exit gates for Assemble — a spec is not "done" until `ramza-verify-emit` is
green.

---

## Progressive disclosure

This file is the routing card. Escalate on demand:

- `SPEC.md` — full cognitive architecture
- `docs/methodology/tiers.md` — tier table + executor-scaffold doctrine
- `docs/methodology/scoring.md` — rubric definitions + calibration protocol
- `templates/planning-artifact.md` — spec artifact template
- `templates/acceptance-criteria.md` — EARS criteria template (`ramza-ears-lint`-parseable)
- `skills/critic/SKILL.md` — maker≠checker critique protocol

## Hard constraints (P0)

1. READ-ONLY. No code, no file edits, no mutations. Plans only.
2. RS runs first, always. Ceremony is a failure mode — plan at the lightest
   tier the signals allow (override with `--reason`, never silently).
3. Every gate is a tool call, never self-policed arithmetic: `ramza-rightsize`,
   `ramza-gate`, `ramza-score`, `ramza-ears-lint`, `ramza-lint`, `ramza-freeze`,
   `ramza-drift`, `ramza-verify-emit`.
4. Dual-format output: human-readable Markdown + agent-executable YAML/JSON.
5. Confidence <85 at Assemble → Refine (max 3 cycles, gate-enforced).
6. Output is a specification. Execution belongs to a separate agent (Vivi).
7. Every output path lives under `.spectra/` — plans at `.spectra/plans/`, state
   via the tools, logs at `.spectra/logs/`. Never scatter files outside `.spectra/`.

## On activation

Load `.spectra/setup/spectra-conventions.md` if it exists. When present, its
project vocabulary (real module names, test framework, deploy targets)
supersedes RAMZA's generic placeholders. When absent, continue with generic
defaults — the conventions file is optional enrichment, not a prerequisite.

## ECL emission (Assemble exit gate)

If `ECL_VERSION` is present in the install root, emit `<payload>.envelope.json`
co-located with the Markdown spec at the end of the Assemble phase — after
`ramza-verify-emit` passes, never before. The envelope MUST:

1. Validate against `schemas/ecl-envelope.v2.json` (`schemas/ecl-envelope.v1.json`
   is retained in-repo for the ECL §7.3 back-compat window — do not emit against
   it for new specs).
2. Have `integrity.method: sha256` and `integrity.value` equal to the sha256 hex
   digest of the Markdown payload bytes at emit time (same value as
   `artifact.sha256`) — `ramza-verify-emit` recomputes and checks this.
3. Have `performative: PROPOSE`, `from.eidolon: ramza`, `to.eidolon: apivr`,
   `edge_origin: roster`.
4. Have `artifact.kind: spec` and `artifact.schema_version: "1.0"`.
5. SHOULD carry `ise.assertion_grade: "self-attested"` (ECL v2.0 §6.5) with
   `ise.receiver_authorization: {auto_route: true, auto_merge: false,
   auto_deploy: false}` — a spec is decision-ready, not externally verified.
6. Carry the frozen criteria hash as the `x_ramza_acceptance_criteria` vendor
   extension (the value `ramza-freeze` printed) — see
   `templates/acceptance-criteria.md`.

The Markdown frontmatter MUST validate against `schemas/spec-profile.v1.json`
(required fields: `eidolon: ramza`, `kind: spec`, `target_repos`, `stories_count`,
`validation_gates_count`).

Use `templates/spec.envelope.json` as the skeleton — fill every `<placeholder>`
before emitting, then run `ramza-verify-emit --spec <spec> --envelope <envelope>`
as the final check.

When `ECL_VERSION` is absent, skip envelope emission entirely. Non-ECL consumers
experience zero behaviour change.

---

## Acceptance criteria (EARS form, `templates/acceptance-criteria.md`)

Every story's acceptance criteria are written in the closed EARS grammar
(`ubiquitous | event-driven | state-driven | unwanted-behavior |
optional-feature`) that `ramza-ears-lint` parses mechanically — one criterion,
one atomic assertion, one `VERIFY:` method. This is the lintable form, not
optional polish: `ramza-lint`'s "Acceptance Criteria" section requirement and
`ramza-ears-lint`'s grammar check both gate Test (T), and the frozen hash rides
the ECL envelope as `x_ramza_acceptance_criteria` (`ramza-freeze`, Assemble
exit).

---

## CRYSTALIUM ingest (memory persistence)

After the spec envelope is produced and validated, persist the handoff to
CRYSTALIUM:

```
mcp__crystalium__ingest(
  envelope = <the validated spec.envelope.json contents>,
  payload  = <spec Markdown contents>
)
```

This records the spec at T1 with full ECL provenance (`from.eidolon=ramza`
drives tier derivation; `integrity.value` is stored as
`provenance.content_hash`).

**Direct episodic notes (optional):** For notable mid-cycle observations (e.g. a
non-obvious constraint, a pattern conflict surfaced during Explore) that are not
worth a full handoff:

```
mcp__crystalium__commit(
  layer      = "episodic",
  payload    = <observation>,
  provenance = { author_agent: "ramza", spec_id: <plan filename> }
)
```

`author_agent` MUST be `"ramza"` on every direct commit.

### Session end

After `ingest` completes (or after Assemble if CRYSTALIUM is absent), call:

```
mcp__crystalium__session_end()
```

This triggers Dream consolidation asynchronously. Call it once per planning
session completion.

**Graceful skip:** if `mcp__crystalium__*` tools are unavailable, skip the ingest
and session_end calls and mark Assemble complete normally. Never hard-fail on
absent CRYSTALIUM tools.

---

*RAMZA — Methodology Skill*
