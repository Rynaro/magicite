---
spec: engram/0.2
name: magicite-p0-hot-path-boundary
id: egr_2aa44467
version: 1
provenance: authored
intent:
  does: "Respect the P0 enforcement boundary when editing Magicite's hot-path modules, so a change does not break a statically-enforced invariant"
  use_when: "editing core/router.py, core/signals.py, mcp/bind_retrieval.py, mcp/bind_signals.py, or anything they import"
  not_when: "the failure is a runtime write denial you have already reproduced — that is the authorizer or the lease talking, not the static import check"
triggers:
  positive:
    - "why does magicite forbid importing storage.durable from router"
    - "test_p0_enforcement failed after my magicite edit"
    - "can core/router.py import the writer module"
    - "magicite AC-024 forbidden import list"
  negative:
    - "my magicite write raised a database error at runtime"
context_affinity: [magicite, architecture, invariants, hot-path]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [p0-safe-edit]
composes: []
inhibits: []
provenance_journal:
  - version: 1
    timestamp: "2026-08-15T00:00:00Z"
    author: "claude-orchestrator"
    event: authored
    note: "Codebase tranche (change magicite-codebase-skill-tranche, AC-T1)"
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Know which four modules are governed before editing any of them. `tests/unit/test_p0_enforcement.py` names them explicitly in `HOT_PATH_MODULES`: `mcp/bind_retrieval.py`, `mcp/bind_signals.py`, `core/router.py`, and `core/signals.py`.
2. Know the two modules they may never import, named in `FORBIDDEN_MODULES`: `magicite.storage.durable` and `magicite.engram.writer`. The check is an AST walk over `Import` and `ImportFrom` nodes, so it catches a `from ... import ...` of a symbol as well as a plain module import.
3. Understand that this is a *static* check and therefore transitive through your own edits: importing an innocuous-looking helper that itself imports the writer moves the violation into your module. This is exactly why `core/decay_math.py` exists as a dependency-free split from `core/decay.py` — the latter imports both forbidden modules for its archival file-move, and pulling it into the router's import graph would defeat the check.
4. Treat AC-040 as the second half of the same boundary: no module outside `core/edge_weight.py` may derive an edge's routing weight from `edge.storage_strength` without going through `effective_strength`. `test_edge_weight_helper_is_the_only_weighting_site` enforces it, and it exists because three separate workarounds had already grown in the tree before the helper did.
5. Reach for the framework-free modules when a hot-path module needs a computation. `core/edge_weight.py`, `core/activation.py`, `core/decay_math.py`, and `core/composition.py` are all deliberately free of DB handles and forbidden imports so hot-path code may import them without breaching anything.
6. Do not silence the test. It is the mechanism that turns a code-review convention into a build failure, and an exemption added to make one change land removes the guarantee for every future change.
7. Remember the runtime layers are separate and additive: the static import check is one guard, the SQLite authorizer is a second, and the writer lease is a third. Passing the static check says nothing about whether your write will be permitted at runtime.

## Pitfalls
- (x1) Adding an import to satisfy a type annotation and tripping the check. Put the annotation behind a string or a type-checking-only guard rather than widening the module's real import graph.
- (x1) Reading a runtime `sqlite3.DatabaseError` on a write as an AC-024 problem. The static check has nothing to say at runtime; a denied write is the authorizer, and a refused write is the lease.
- (x1) Assuming the forbidden list is about layering aesthetics. It is about a hot-path tool being unable to reach durable-write code at all, which is what makes the read-mostly guarantee checkable rather than asserted.
- (x1) Introducing a fourth hot-path module without adding it to `HOT_PATH_MODULES`, which leaves the new module unguarded while looking guarded.

## Examples
+ "I want route() to write an engram row directly" -> it cannot, by construction; steps 1 through 3
+ "where do I put a helper the router needs" -> a framework-free module, step 5
- "sqlite3.DatabaseError on INSERT from a tool handler" -> NOT this engram (that is the authorizer at runtime)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
