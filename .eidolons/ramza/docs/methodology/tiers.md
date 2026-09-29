# Right-Sizing Tiers

> The RS gate exists because forced ceremony is a measured failure mode (see
> DESIGN-RATIONALE D3). `ramza-rightsize` computes the tier deterministically;
> this table is what the tier *means*. Overrides are legitimate — and recorded.

## Signal scoring (ramza-rightsize)

| Signal | Values | Points |
|---|---|---|
| `--files-est` | ≤2 / 3–9 / ≥10 | 0 / 1 / 2 |
| `--new-dep` `--public-api` `--migration` `--security` `--novel` | flag present | +1 each |
| `--stakes` | low / med / high | 0 / 1 / 2 |

**Tier:** score ≤1 → `trivial` · 2–4 → `lite` · ≥5 → `full`.

## What each tier mandates

| | trivial | lite | full |
|---|---|---|---|
| Phases (gate-enforced) | RS S C A | RS S P E C T A | RS S P E C T A |
| Hypotheses (E) | — | 3 | 3–5 |
| Verification layers (T) | structural + criteria lint | + dependency, constraint | + self-consistency, adversarial |
| Independent critic (maker≠checker) | — | recommended | **required before A** |
| Plan budget (`ramza-lint`) | ≤120 lines | — | — |
| Required sections | Scope, Approach, Acceptance Criteria | + Stories, Confidence | + Rejected Alternatives, Risks |
| Refine cap | 3 (all tiers — `ramza-gate` enforced) | 3 | 3 |

Skipping a tier-mandatory phase is possible only with `--reason` and lands in the
state file's `skips[]` — visible in every audit.

## Executor-tier scaffold density (Construct)

The tier of the *executing* model shapes the spec, inversely:

| Executor tier | Scaffold |
|---|---|
| frontier (Fable/Opus-class) | goals, constraints, acceptance criteria — no step-scripting |
| mid (Sonnet-class) | + file-level action plan, named patterns |
| economy (Haiku-class) | + explicit steps, expected outputs, schema-validated contracts |

Rationale: scaffold value scales inversely with model strength; imposed micro-plans
measurably hurt strong models (research dossier T1). Frontier attention is reserved
for boundaries: plan authoring, judging, amendment (D6).
