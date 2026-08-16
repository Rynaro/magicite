---
spec: engram/0.2
name: magicite-plasticity-dw-formula
id: egr_293ed9a7
version: 1
provenance: authored
intent:
  does: "Work on Magicite's weight-change formula: metaplastic saturation, the spacing effect, and the tier gate that refuses Tier-0 evidence"
  use_when: "changing how evidence becomes storage strength, or explaining why a particular signal moved S by the amount it did"
  not_when: "the value moved downward with elapsed time and no new evidence — that is decay, which is a separate mechanism"
triggers:
  positive:
  - "how does magicite compute delta w for storage strength"
  - "magicite TIER_WEIGHT tier 0 never reaches S"
  - "magicite metaplastic saturation and spacing effect"
  - "magicite P0Violation raised applying plasticity"
  negative:
  - "a magicite value decreased on its own over time"
context_affinity: [magicite, plasticity, learning, invariants]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [correct-weight-delta]
composes: []
inhibits: [magicite-decay-lazy-read]
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: Codebase tranche (change magicite-codebase-skill-tranche, AC-T1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Read the formula as a product of four independent factors: a base learning rate, a metaplastic saturation term that shrinks as the weight approaches its ceiling, the tier weight, and a spacing term that shrinks when updates arrive too close together. The delta is that effective rate times the mean outcome times a capture weight carrying recency and salience.
2. Internalise the tier table, because it is the single most consequential constant here. Tier 0 is zero, Tier 1 is six tenths, Tier 2 is one. A Tier-0 signal therefore contributes exactly nothing to storage strength, by construction rather than by convention.
3. Understand the split between the two functions, because it exists to prevent a specific bug. The untiered helper computes saturation and spacing only; the applying function multiplies by the tier weight. This guarantees the gate is exercised on every real application and that the tier weight can never be applied twice.
4. Expect a refusal, not a silent zero, when Tier-0 evidence is aimed at storage strength: the applying function raises rather than quietly multiplying by zero, so the violation is visible.
5. Keep the Tier-1 cap in mind when reasoning about self-reported signals. Six tenths is an anti-poisoning measure, not a tuning parameter — a host that cannot externally verify its own signals should not be able to drive learning at full weight.
6. Remember saturation makes learning self-limiting near the ceiling. A weight close to its maximum barely moves regardless of how much evidence arrives, so "the signal did nothing" can be correct behaviour for an already-strong edge.
7. Remember spacing suppresses bursts. Many updates in quick succession contribute far less than the same updates spread out, so a tight loop of signals is not equivalent to sustained use.
8. Re-read the falsification record before concluding a change here helps. The learning layer is recorded as falsified as implemented on the measured workload, so a formula change needs measurement rather than plausibility.

## Pitfalls
- (x1) Attributing a flat storage strength to a bug when every incoming signal is Tier 0. Zero is the designed weight, and no amount of Tier-0 volume changes it.
- (x1) Applying the tier weight in the untiered helper as well as the applying function, double-counting it — the split exists precisely to make that impossible.
- (x1) Treating the Tier-1 cap as a knob to raise for faster learning, which removes the anti-poisoning property that justified accepting self-reported signals at all.
- (x1) Confusing saturation with decay. Saturation limits growth toward a ceiling; decay reduces a value with elapsed time, in a different module.

## Examples
+ "we sent a hundred signals and S barely moved" -> check tier, saturation, and spacing, steps 2, 6, and 7
+ "why does the code raise instead of returning zero" -> visibility, step 4
- "S fell while nobody used the engram" -> NOT this engram (decay, not potentiation)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
