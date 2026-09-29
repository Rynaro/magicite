# Scoring — Rubrics as Calibratable Instruments

> Honesty clause (D7): the weights and thresholds below are v1 **instruments under
> calibration**, not validated truths. They inherit SPECTRA 4.11's shape; what changed
> is that the arithmetic is now computed by `ramza-score` (never by the model) and
> every scored gate appends to `ramza-calibration.jsonl` so instrument-vs-outcome
> data accrues. Recalibration protocol at the bottom.

## complexity (Scope phase)

`{scope, ambiguity, dependencies, risk}` each 1–3 → total 4–12.
**Routing:** 4–6 standard · 7–9 extended reasoning · 10–12 human-in-the-loop.

## explore (hypothesis rubric)

Dimensions 1–10, weighted: alignment .25 · correctness .20 · maintainability .15 ·
performance .15 · simplicity .10 · risk .10 · innovation .05 → total 0–100.
**Verdict:** ≥85 elite · 70–84 solid · <70 weak (weak ⇒ exit 1: rework or drop).

## refine (critique rubric)

`{clarity, completeness, actionability, efficiency, testability}` each 1–5,
`--cycle N` sets the bar: cycle 1 all ≥3, cycles 2–3 all ≥4. Fail ⇒ exit 1.
Diminishing returns: if mean improvement between cycles < 0.3, stop refining
(compare `total` across the calibration log entries).

## confidence (Assemble gate)

`{pattern_match, requirement_clarity, decomposition_stability, constraint_compliance}`
each 0–100, equal-weighted → %.
**Verdict:** ≥85 AUTO_PROCEED · 70–84 VALIDATE (human reviews) · 50–69 COLLABORATE
(halt, ask) · <50 ESCALATE (exit 1, gap report).

## Calibration protocol

1. Every `ramza-score` call logs `{rubric, dims, total, verdict, at, label}` (JSONL).
2. After execution, record the outcome against the plan (shipped-clean / rework /
   abandoned) — the nexus eval harness or a one-line annotation.
3. Periodically test the instrument: verdicts should discriminate outcomes
   (target: AUC ≥ 0.8, κ ≥ 0.75 vs human judgment — the AdaRubric bar).
   Weights/thresholds that fail get revised **in a versioned bump**, not silently.
4. Cross-model anchor plans (SPECTRA 4.10's calibration protocol) apply unchanged:
   new host model ⇒ re-score the anchors, check agreement before trusting verdicts.
