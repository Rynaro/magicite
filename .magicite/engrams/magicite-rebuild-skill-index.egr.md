---
spec: engram/0.2
name: magicite-rebuild-skill-index
id: egr_574cca6c
version: 1
provenance: authored
intent:
  does: "Rebuild Magicite's durable SQLite skill-graph index from the .egr.md files, which are the only source of truth"
  use_when: "the skill-graph index is missing, stale, corrupt, or out of step with edited engram files on disk"
  not_when: "the engram files themselves are wrong — a rebuild faithfully reproduces bad input, it does not repair it"
triggers:
  positive:
  - "rebuild the magicite skill graph index from engram files"
  - "magicite skill-graph.db is stale or corrupt"
  - "i edited an .egr.md and route still returns the old text"
  - "magicite sync project root rebuild invariant"
  negative:
  - "my .egr.md was rejected by strict lint"
context_affinity: [magicite, registry, sqlite, rebuild]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-offline-embedding-setup]
yields: [rebuilt-skill-graph-index]
composes: []
inhibits: [magicite-dream-consolidation]
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
1. Internalise the invariant before touching anything: the `.egr.md` files are the source of truth and the index is a derived artifact. Deleting `skill-graph.db` is a safe, supported operation — that is why the file is gitignored rather than tracked.
2. Ensure an embedding provider is available first; a rebuild re-embeds every engram body, so a provider that needs a download will stall or fail the whole pass.
3. Delete the index and its write-ahead files together — `skill-graph.db`, `skill-graph.db-wal`, and `skill-graph.db-shm`. Removing only the main file while leaving a WAL behind is how you get a half-restored index.
4. Run `uv run magicite sync --project-root .` and read the reported counts rather than assuming success. A silently smaller count than the number of `.egr.md` files on disk means engrams were rejected, not that the registry shrank.
5. Investigate any rejection as a lint failure on the specific file, since a strict-profile failure aborts before any database write for that engram and leaves it absent from the rebuilt index entirely.
6. Confirm declared edges survived. Edge targets are resolved by engram name at ingest, and a target that is not registered becomes a dangling edge that is dropped from routing rather than reported as an error.
7. Do not run a Dream consolidation pass against a half-rebuilt index; finish the sync first, because consolidation writes durable state derived from what the index currently contains.

## Pitfalls
- (x1) Committing the index. It is deliberately gitignored as a rebuildable artifact; tracking it produces constant binary churn and invites a stale index to be treated as authoritative.
- (x1) Editing an `.egr.md` and expecting `route()` to change without a sync. The durable index, not the file, backs retrieval at query time.
- (x1) Deleting `skill-graph.db` while a server holds it open. Stop the server first, or the WAL and shared-memory files can be recreated underneath you.
- (x1) Reading a shrunken post-rebuild count as normal. The rebuild invariant is that a rebuild reproduces the registry exactly; any shortfall is a rejected engram.

## Examples
+ "I changed an engram's procedure but route still returns the old body" -> steps 3 and 4
+ "is it safe to delete skill-graph.db" -> step 1, yes, it is derived
- "my new engram fails strict lint on not_when" -> NOT this engram (fix the file first, then rebuild)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
