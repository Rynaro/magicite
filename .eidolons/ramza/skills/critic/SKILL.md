---
name: ramza-critic
description: "The maker≠checker critique protocol for a plan at Test (T). Mandatory before Assemble at tier=full (self-approval is mechanically DENIED by ramza-gate), recommended at tier=lite. Mechanical debiasing: strip author identity, anchor findings on ramza-lint/ramza-ears-lint output plus the refine rubric, record identities via ramza-gate critic. Read-only; critiques, never edits."
metadata:
  methodology: RAMZA
---

# RAMZA — Critic Skill (maker≠checker)

Use this skill whenever a plan needs independent critique — mandatory at
tier=full before Assemble (`ramza-gate advance --to A` DENIES entry without a
recorded critic), recommended at tier=lite. The critic is never the plan's
author: `ramza-gate critic` mechanically enforces `--author != --checker` and
exits 1 on a self-approval attempt.

## Inputs

The critique session works from exactly three artifacts — nothing else
self-reported by the author:

1. **The plan** (`.spectra/plans/<slug>.md`) — the artifact under review.
2. **The acceptance criteria** (the plan's `## Acceptance Criteria` section, or
   a sibling `.spectra/plans/<slug>.acceptance.md`) — the EARS-form criteria
   block.
3. **The state file** (`.spectra/plans/<slug>.state.json`) — tier, phase, the
   prior gate history (`gates[]`), and any recorded skips.

The critic does not receive the author's private chat reasoning or a prose
"why I chose X" narrative that isn't already in the plan body — only the
artifact, the criteria, and the mechanical history in state. That's what
"strip author identity" buys: a critique anchored on what the plan actually
says, not on the author's framing of it.

## The debias procedure (mechanical, in order)

### 1. Strip author identity

Before evaluating content, remove/ignore anything that identifies or argues
for the author's authorship rather than describing the plan itself:

- Do not read `critic.author`/`critic.checker` in the state file before
  forming an independent judgment — read it only afterward, to confirm the
  `--checker` value being recorded is really you.
- Ignore any frontmatter `author:`/`by:` field, git-blame authorship, or
  inline "I chose X because…" framing that argues for the plan rather than
  stating what it does. Critique the artifact's claims, not the author's
  narration of their own reasoning.
- If the plan's author and the critic are the same identity, stop here —
  `ramza-gate critic` will reject the recording in step 3; do not proceed to a
  self-critique in its place.

### 2. Evaluate criteria-anchored, not vibes-anchored

Run the mechanical checks FIRST, and anchor every subsequent finding on their
actual output — never on a re-derived impression of "does this look
complete":

```
ramza-lint --plan <plan.md> --state <state>
ramza-ears-lint <criteria.md>
```

Every missing section or EARS violation these print is a **finding**,
verbatim — not a starting point for independently re-litigating structure.
Structure is mechanical (that's the whole point of `ramza-lint` /
`ramza-ears-lint` being grep-level tools); the critic's judgment is reserved
for what the tools cannot see — does the approach actually address the risk,
is a hypothesis's rejection rationale sound, is a timebox realistic, does a
story's executor hint match its actual ambiguity.

Score that content-quality judgment through the refine rubric — never
estimated in prose:

```
echo '{"clarity":<1-5>,"completeness":<1-5>,"actionability":<1-5>,"efficiency":<1-5>,"testability":<1-5>}' \
  | ramza-score --rubric refine --state <state> --cycle <current-refine-cycle>
```

`ramza-score` computes the verdict (cycle 1: pass requires every dimension
≥3; cycle ≥2: every dimension ≥4) and appends the result to `state.gates[]`
and the calibration log (`ramza-calibration.jsonl`) — the critic reports the
tool's verdict, never a hand-computed one.

### 3. Record identities

Once the critique is complete, record it — this is what makes the plan
eligible to advance to Assemble at tier=full:

```
ramza-gate critic --state <state> --author <author-id> --checker <checker-id>
```

`--author` and `--checker` MUST be distinct identities — `ramza-gate` DENYs
`author == checker` with exit 1. This is the mechanical enforcement of
maker≠checker, not a convention to remember. Use stable identifiers (e.g. a
model class + session tag, or a human reviewer's name); whatever value goes
here is the audit trail's permanent record of who checked whom.

## Output shape

The critique report has exactly three parts, in this order:

1. **Verdict** — the `ramza-score --rubric refine` result verbatim (`total`,
   per-dimension `dims`, and `pass`/`fail`), plus the `ramza-lint` and
   `ramza-ears-lint` exit status (clean, or the violation count each printed).
2. **Per-dimension findings** — one line per rubric dimension (clarity,
   completeness, actionability, efficiency, testability), each citing the
   specific plan section or acceptance-criteria ID it concerns. A dimension
   scored below the cycle's bar MUST have a finding; a passing dimension MAY
   have one too (e.g. a near-miss worth flagging before the next cycle).
3. **Prescription list** — an ordered, actionable list the author applies in
   Refine (R). Each item names the section/ID to change and the specific fix
   — never "improve clarity," always "AC-004's THEN is compound; split into
   AC-004a/AC-004b." This list is what Refine consumes; a critique without a
   prescription list cannot be acted on.

```
## Critique — <plan slug>

**Verdict:** ramza-lint <clean | N violations> · ramza-ears-lint <clean | N violations> ·
refine rubric: <pass | fail> (total <X>, cycle <N>)

**Findings**
- clarity (<score>/5): <finding, cites section/ID>
- completeness (<score>/5): <finding>
- actionability (<score>/5): <finding>
- efficiency (<score>/5): <finding>
- testability (<score>/5): <finding>

**Prescriptions**
1. <section/ID> — <specific fix>
2. …
```

## Hard constraints (P0)

1. Maker≠checker is mechanical, not a convention — `ramza-gate critic` DENYs
   `--author == --checker`. Never role-play both sides in one session.
2. Structural findings come from `ramza-lint` / `ramza-ears-lint` output,
   never re-derived by re-reading the plan for "missing sections."
3. Content-quality findings are scored via `ramza-score --rubric refine`,
   never estimated in prose.
4. Every finding below the cycle's bar carries a prescription; a verdict with
   no prescription list is not a usable critique.
5. Full tier: no path to Assemble without a recorded critic (`ramza-gate
   advance --to A` enforces this directly, see `docs/methodology/tiers.md`).
6. READ-ONLY — the critic reviews and records; it never edits the plan itself.
   Fixes land only through the author's next Refine pass.

See `SPEC.md` "## T — Test" for how the critic gate fits the
cycle, `docs/methodology/tiers.md` for how it fits the tier table, and
`docs/methodology/scoring.md` "refine (critique rubric)" for the rubric's
weights and thresholds.

---

*RAMZA — Critic Skill (maker≠checker)*
