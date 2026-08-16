---
spec: engram/0.2
name: magicite-diagnose-flat-plasticity
id: egr_3107d0a9
version: 1
provenance: authored
intent:
  does: "Diagnose why a Magicite registry's storage strength is not moving despite signals being sent"
  use_when: "engram storage_strength stays flat, or exposure counts rise while status never leaves nascent"
  not_when: "you have not yet wired the signal loop at all — send signals first, then diagnose"
triggers:
  positive:
  - "magicite storage strength never changes no matter what i send"
  - "my engrams stay nascent forever"
  - "magicite exposure count rises but S stays zero"
  - "why did my magicite signal not count"
  negative:
  - "what do i call after magicite route returns candidates"
context_affinity: [magicite, plasticity, signals, debugging]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-route-and-signal-loop]
yields: [plasticity-root-cause]
composes: []
inhibits: []
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Check the tier your signals are actually landing at before anything else. Tier-0 inferred signals carry a weight of zero toward storage strength by design, so a registry receiving only passive inference will show rising bookkeeping and permanently flat S. This is correct behaviour, not a defect.
2. Check whether a consolidation pass has ever run. Signals accumulate in ephemeral state and only the Dream worker writes durable node and edge state, so a registry that never consolidates never moves S regardless of signal volume.
3. Check the per-session cap. Tier-1 self-reported uses are capped per skill per session, so a loop that calls `signal_use` twenty times for one skill in one session contributes far less than the call count suggests.
4. Check the session actually ended. Evidence from a session that expired on its TTL rather than being closed cannot be credited retroactively.
5. Check the commit-noise floor. A consolidation pass only commits an edge weight change when the delta exceeds the configured threshold, so many tiny nudges can wash out entirely rather than accumulating.
6. Check salience against the threshold when `skill_ids` was omitted. Retroactive credit to session-tagged skills only fires when the outcome is both high-salience and past the configured salience bar; below it, an omitted-id outcome credits nothing.
7. Re-read the honest baseline before concluding the mechanism is broken. `docs/01`'s Falsification Record records the learning layer as falsified as implemented on the measured workload, so "learning is not improving my routing" may be an accurate observation about the system rather than a misconfiguration on your side.

## Pitfalls
- (x1) Inflating salience to force credit. It converts a confidence estimate into a lie and sprays retroactive credit across every skill tagged in the session.
- (x1) Assuming a nascent status is a bug. Status transitions are threshold-driven on durable strength, so an engram that has never accrued durable evidence is correctly nascent.
- (x1) Concluding the wiring is broken when the honest answer is that the measured learning effect on this workload was negative.
- (x1) Debugging plasticity before confirming a consolidation pass has ever run against this registry.

## Examples
+ "we send hundreds of signals a day and nothing consolidates" -> steps 1 and 2
+ "our tier is 0 everywhere" -> step 1, that weight is zero by design
- "I do not know which tool to call after route" -> NOT this engram (learn the loop first)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
