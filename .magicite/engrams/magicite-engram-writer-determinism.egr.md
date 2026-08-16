---
spec: engram/0.2
name: magicite-engram-writer-determinism
id: egr_987ed80b
version: 1
provenance: authored
intent:
  does: "Preserve Magicite's atomic, byte-deterministic .egr.md write contract when changing anything that renders a file back to disk"
  use_when: "editing engram/writer.py, or a checkpoint produced a diff that should have been empty, or a write left a partial file"
  not_when: "the file is being read and parsed rather than written — round-trip loss usually shows up on write, but is diagnosed on both"
triggers:
  positive:
  - "magicite checkpoint produced a spurious diff"
  - "magicite egr file write must be atomic and deterministic"
  - "magicite writer float rounding and synapse sort order"
  - "magicite atomic_write tmp fsync os.replace"
  negative:
  - "magicite strict lint rejected my hand-written engram"
context_affinity: [magicite, engram-format, storage, determinism]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-writer-lease-and-dream-context, magicite-author-engram]
yields: [deterministic-file-write]
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
1. Preserve the write sequence exactly: write to a temporary sibling, fsync the file, atomically replace the target, then fsync the directory. Every step is load-bearing — dropping the directory fsync leaves the rename itself unflushed, and writing in place makes a partial file possible.
2. Preserve the four determinism rules, because they are what make two checkpoints of identical state produce byte-identical files: floats render to four decimals, the synapse list sorts by type then target, line endings are LF, and no trailing whitespace survives.
3. Understand that determinism is a correctness property here, not tidiness. Without it every consolidation pass produces a spurious diff, the registry churns in version control, and no audit of "what did this pass actually change" is possible.
4. Preserve the round-trip carrier. The parser keeps the original document object, and the writer edits that rather than re-serialising from scratch, which is what keeps comments and formatting the author wrote from being destroyed on a checkpoint write.
5. When adding a field that a checkpoint updates, add it to the round-trip branch as well as the model. This is the exact defect the release fix addressed: the provenance journal was computed in memory but never refreshed on the carrier document, so appended entries never reached the file's bytes.
6. Test persistence by re-parsing the file, not by inspecting the in-memory object. The property worth asserting is that a fresh parse of the bytes on disk reads the new state back.
7. Respect the guard split. The generic write primitive asserts only the single-writer lease, while the two functions rendering the plasticity and synapse blocks additionally assert Dream context — because authored content is legitimately written outside a checkpoint.
8. Never hand-edit checkpoint-owned blocks in a registry that is live; the next pass will overwrite or blend the edit.

## Pitfalls
- (x1) Updating the model but not the round-trip branch, so a value changes in memory and silently never reaches disk. This has happened before and had to be fixed with its own acceptance test.
- (x1) Widening float precision "for accuracy", which breaks byte-identical output and produces a diff on every pass.
- (x1) Re-serialising from the model instead of editing the carrier document, destroying author comments and formatting.
- (x1) Verifying a write by checking the object you just mutated rather than by re-reading the file.

## Examples
+ "every consolidation pass dirties all sixteen files" -> a determinism rule was broken, step 2
+ "my new checkpoint field never appears on disk" -> the round-trip branch, step 5
- "the parser rejected my file" -> NOT this engram (that is lint on the authoring side)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
