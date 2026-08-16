---
spec: engram/0.2
name: magicite-writer-lease-and-dream-context
id: egr_8e528212
version: 1
provenance: authored
intent:
  does: "Hold Magicite's single-writer lease correctly and understand why learned-state writes additionally require Dream context"
  use_when: "a write refuses with a lease or Dream-context error, or you are adding a code path that writes durable or learned state"
  not_when: "the write was denied by the connection's authorizer because it targeted a non-eph_ table from the hot path"
triggers:
  positive:
  - "magicite assert_single_writer raised on my write"
  - "magicite DreamContextError writing plasticity"
  - "how does magicite stop two dream runs at once"
  - "magicite writer_lease flock dream.lock"
  negative:
  - "magicite sqlite3 DatabaseError on insert from a tool handler"
context_affinity: [magicite, storage, concurrency, invariants]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-p0-hot-path-boundary]
yields: [safe-durable-write]
composes: []
inhibits: [magicite-ephemeral-vs-durable-tables]
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
1. Separate the three guards before debugging. G1 is the connection authorizer, G2 is the single-writer lease, and G3 is the Dream-context assertion. They are additive, and each has a distinct error and a distinct fix.
2. For G2, wrap the write in `storage/lease.py`'s `writer_lease()`. Every public function in `storage/durable.py` and `engram/writer.py::atomic_write()` calls `assert_single_writer()`, so a durable write outside the lease raises rather than racing. The in-process layer is a process-wide `threading.Lock` plus a re-entrant `contextvars` depth counter, so nesting is safe.
3. For G3, understand the narrower scope. Only `write_plasticity()` and `write_synapses()` — the two functions that render the `plasticity:` and `synapses:` blocks — call `assert_dream_context()`. The generic `atomic_write()` primitive does not, because `register()` and `sharpen()` legitimately write authored identity, routing, and body content without being inside a checkpoint.
4. Do not set the Dream-context variable yourself. `_DREAM_CONTEXT` is set only by `core/dream.py::checkpoint_phase()`, and `tests/unit/test_g3_enforcement.py` greps the tree to keep it that way. If your code needs to write learned state, the correct answer is that it belongs in the checkpoint phase, not that it needs the flag.
5. For cross-process safety, know that the lease is two mechanisms in sequence: `fcntl.flock` on `<runtime>/dream.lock` with `LOCK_EX | LOCK_NB` first, which is fast and same-host, then an atomic TTL-guarded upsert against the `writer_lease` table, which is what survives filesystems where flock is unreliable.
6. Expect a second concurrent Dream run to return busy rather than to block or to corrupt — that is a property about two operating-system processes, not merely two contexts in one interpreter.
7. Know the current call-site boundary: `core/dream.py::run()` and the standalone checkpoint path are the cross-process lease's only callers. `register()`, `sync()`, and `export()` hold the in-process lease but not the cross-process one, which is a recorded follow-up rather than an accident.

## Pitfalls
- (x1) Adding a `_DREAM_CONTEXT` set outside the checkpoint phase to make a write pass. That converts a semantic guarantee about which code may write learned state into a decoration, and the enforcement test greps for exactly this.
- (x1) Hand-editing `plasticity:` or `synapses:` blocks in an `.egr.md`. Those are checkpoint-owned; the next pass overwrites or blends the edit.
- (x1) Assuming the flock alone is the lease. On a filesystem where flock degrades, the database row is what actually holds the line — which is why `magicite doctor` warns about filesystem class at all.
- (x1) Diagnosing a lease refusal as an authorizer denial. The authorizer only ever speaks on the hot-path connection and only about non-`eph_` tables.

## Examples
+ "two dream runs started and I expected one to wait" -> it returns busy instead, step 6
+ "my new code writes storage_strength and raises" -> it belongs in the checkpoint phase, step 4
- "my tool handler cannot INSERT into engram" -> NOT this engram (authorizer, wrong connection)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
