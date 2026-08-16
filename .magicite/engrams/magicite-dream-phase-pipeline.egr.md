---
spec: engram/0.2
name: magicite-dream-phase-pipeline
id: egr_1bd3fd0a
version: 1
provenance: authored
intent:
  does: "Navigate Magicite's seven Dream phases and find the one that owns a given consolidation behaviour, including the two that live outside dream.py"
  use_when: "changing consolidation behaviour, or a consolidation pass produced an effect you need to attribute to a phase"
  not_when: "you only need to run a pass and read its result rather than modify what a phase does"
triggers:
  positive:
  - "which magicite dream phase writes this state"
  - "magicite dream phase 3 and phase 6 are not in dream.py"
  - "magicite consolidation phase pipeline replay potentiate renormalise"
  - "magicite checkpoint phase dirty set"
  negative:
  - "just run the magicite dream worker and show me the result"
context_affinity: [magicite, dream, consolidation, architecture]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-plasticity-dw-formula, magicite-writer-lease-and-dream-context]
yields: [located-dream-phase]
composes: []
inhibits: []
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
1. Do not assume every phase lives in `core/dream.py`. Phases 1, 2, 4, 5, and 7 are defined there; phase 3 lives in `core/decay.py` and phase 6 in `core/audit.py`. Grepping only that one file is the most common way to conclude a phase does not exist.
2. Match the phase to the behaviour before editing. Phase 1 replays ephemeral events into an in-memory intermediate representation and writes nothing. Phase 2 potentiates, folding captured tags into real weight changes. Phase 3 decays and archives below the floor. Phase 4 renormalises for homeostasis. Phase 5 distils frequent paths into proposals. Phase 6 audits the whole graph. Phase 7 checkpoints to disk.
3. Respect the write boundary. Only the checkpoint phase sets the Dream-context marker, and only the two functions that render the plasticity and synapse blocks assert it, so learned state can only be written from inside that phase.
4. Expect the prune rule to be hysteretic rather than instantaneous: an edge is archived out of the live set after staying below the prune threshold across consecutive runs, not the first time it dips.
5. Expect the commit-noise floor to discard tiny changes. A pass that legitimately changes nothing is a correct pass over thin evidence, not a broken worker.
6. Understand the determinism property and do not break it. Checkpoint candidates are handled so that two runs over identical state produce byte-identical files, which is what makes the whole pipeline auditable.
7. Note that the checkpoint loop skips rows the decay phase already relocated. Phase ordering matters, and a phase that assumes it sees every row will misbehave on archived ones.
8. Run the worker inline when debugging rather than through the enqueued path, so the phases execute in one process you can observe.

## Pitfalls
- (x1) Concluding phases 3 and 6 are unimplemented because they are absent from `core/dream.py`. They live in the decay and audit modules respectively.
- (x1) Writing learned state from a phase other than checkpoint, which the Dream-context assertion refuses.
- (x1) Reading a no-op pass as a failure when the commit floor or thin evidence explains it.
- (x1) Introducing nondeterminism into the checkpoint ordering, which breaks the byte-identical guarantee and every audit that relies on it.

## Examples
+ "where does archival actually happen" -> phase 3, in the decay module, step 1
+ "my new phase wants to write storage_strength" -> only checkpoint may, step 3
- "I just want to trigger consolidation" -> NOT this engram (that is the operational runbook)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
