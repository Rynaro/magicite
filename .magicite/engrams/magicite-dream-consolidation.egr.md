---
spec: engram/0.2
name: magicite-dream-consolidation
id: egr_40c7c6af
version: 1
provenance: authored
intent:
  does: "Run and reason about the Dream consolidation worker, the only component that turns session evidence into durable engram state"
  use_when: "session signals need to become durable storage strength, learned edges, or lifecycle status changes"
  not_when: "the durable index is mid-rebuild or out of step with the .egr.md files — finish the sync first"
triggers:
  positive:
  - "run the magicite dream consolidation worker"
  - "when does magicite write durable storage strength"
  - "magicite checkpoint writes back to the egr file"
  - "what does a magicite consolidation pass actually change"
  negative:
  - "rebuild the magicite skill graph index from engram files"
context_affinity: [magicite, dream, plasticity, consolidation]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-route-and-signal-loop]
yields: [durable-consolidated-state]
composes: []
inhibits: [magicite-rebuild-skill-index]
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
1. Confirm there is evidence to consolidate. A pass over a registry whose sessions never closed has nothing to promote, and running it anyway produces a clean no-op that is easy to misread as a failure.
2. Run it inline with `uv run magicite dream --project-root .`, or let the enqueued path fire from `session_end`. The inline CLI is the debuggable form; the enqueued form is the production path.
3. Understand the write boundary: consolidation is the only writer of durable Tier-A node state and Tier-B edge state. Every other tool either reads or writes ephemeral evidence, which is why an outcome signal does not immediately move storage strength.
4. Expect edge changes to be filtered. A weight delta below the commit-noise floor is discarded rather than accumulated, so a pass can legitimately change nothing while still having run correctly.
5. Expect weak edges to be pruned, not deleted silently — an edge that stays below the prune threshold across consecutive runs is archived out of the live synapse set.
6. Check that checkpoint writes reached the file, not just memory. A checkpoint appends a provenance journal entry and rewrites the `.egr.md`; the invariant worth asserting is that a fresh parse of the file on disk reads the new entry back.
7. Never run consolidation against a half-rebuilt index. It derives durable state from what the index currently contains, so consolidating a partial index bakes the gap into engram files.
8. Re-read `docs/01` before treating a consolidation result as improvement. On the measured workload the learning layer is recorded as falsified as implemented; a pass running correctly is not the same as a pass helping.

## Pitfalls
- (x1) Reading a no-op pass as a broken worker. Below-threshold deltas are dropped by design and a correct pass over thin evidence changes nothing.
- (x1) Hand-editing plasticity blocks in `.egr.md` files. Those fields are checkpoint-owned durable state, and a manual edit is overwritten or, worse, silently blended into the next pass.
- (x1) Running a pass mid-rebuild and then wondering why engrams lost edges.
- (x1) Assuming an appended provenance entry is persisted merely because it exists on the in-memory object; the persistence of that append is a distinct property with its own acceptance test.

## Examples
+ "our signals never become durable" -> steps 1 through 3
+ "dream ran and changed nothing, is it broken" -> step 4, probably not
- "I need to rebuild the index from the engram files" -> NOT this engram (sync first, consolidate after)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
