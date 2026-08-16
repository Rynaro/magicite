---
spec: engram/0.2
name: magicite-decay-lazy-read
id: egr_5b5516cd
version: 1
provenance: authored
intent:
  does: "Reason about Magicite's exponential decay, which is evaluated lazily at every read rather than only when a Dream pass materialises it"
  use_when: "a strength value drops with elapsed time and no new evidence, or you are changing a decay rate or an archival floor"
  not_when: "the value changed in response to a signal or a consolidation pass — that is potentiation, a different mechanism"
triggers:
  positive:
  - "a magicite value decreased on its own over time"
  - "magicite retrieval strength decays at read time"
  - "magicite lambda_r and lambda_s decay rates"
  - "why is magicite decay_math split from decay"
  negative:
  - "how does magicite compute delta w for storage strength"
context_affinity: [magicite, decay, plasticity, numerics]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [decay-adjusted-value]
composes: []
inhibits: [magicite-plasticity-dw-formula]
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
1. Read the decay function as plain exponential decay: a value times the exponential of minus its rate times elapsed days. There is nothing cleverer happening, and the units are days.
2. Note the null-anchor convention. A row that has never been decayed carries no anchor timestamp, and that case is treated as "no time has passed" rather than as an error or as an epoch — the honest value for a row with no decay history.
3. Understand that decay is evaluated on *every* read, not only when a consolidation pass materialises it. The router decay-adjusts retrieval strength at query time, so two identical queries seconds apart can legitimately return slightly different scores.
4. Know why the maths lives in its own module. `core/decay_math.py` is deliberately split from `core/decay.py` so the hot-path router can decay-adjust at read time without transitively importing the durable-write path — `core/decay.py` itself imports both forbidden modules for its archival file move.
5. Keep that module dependency-free when changing it. Its only dependencies are the standard library's math and datetime, and it performs no input or output; adding an import there can break the static P0 check in a module that merely imports it.
6. Distinguish the two rates. Retrieval strength and storage strength decay at different configured rates, with retrieval far faster — that difference is the whole point of having two values rather than one.
7. Treat the archival floor as a decay consequence rather than a lifecycle decision. An engram whose strength falls below the floor is relocated by the decay pass, which is why the checkpoint loop skips rows it has already moved.
8. Re-anchor deliberately. Because elapsed time is measured from the stored anchor, a change that rewrites anchors resets everyone's decay clock, which is a data migration and not a tuning change.

## Pitfalls
- (x1) Reading a score that changes between two identical queries as nondeterminism. Read-time decay makes elapsed wall-clock a real input.
- (x1) Adding an import to the pure-maths module and breaking the hot-path import check somewhere else entirely.
- (x1) Treating a missing anchor as epoch-zero, which would decay a brand-new row to nothing instantly.
- (x1) Attributing a decayed value to a failed signal. Nothing needs to have gone wrong for a value to fall; that is what decay is.

## Examples
+ "retrieval strength fell over a weekend with no usage" -> expected, steps 1 and 6
+ "why can't decay live next to the archival code" -> the import boundary, step 4
- "S did not move after we sent outcomes" -> NOT this engram (potentiation and the tier gate)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
