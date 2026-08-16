---
spec: engram/0.2
name: magicite-composition-plan-expansion
id: egr_acfbc4a5
version: 1
provenance: authored
intent:
  does: "Work on Magicite's composition-plan expansion: the needs and composes closure, its topological sort, and the cycle guard"
  use_when: "route() returns a multi-step plan that is wrong, incomplete, or ordered oddly, or you are changing plan bounds"
  not_when: "the problem is which single candidate ranked first — planning runs after ranking and cannot reorder the winner"
triggers:
  positive:
  - "magicite composition plan order is wrong"
  - "magicite needs and composes closure topological sort"
  - "magicite plan_max_depth plan_max_size bounds"
  - "magicite cycle detected in a composition plan"
  negative:
  - "which candidate did magicite rank first and why"
context_affinity: [magicite, routing, planning, graph]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-route-pipeline-order]
yields: [ordered-composition-plan]
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
1. Know which edge types participate. Plan expansion closes over `depends_on` and `composes` only — the pair declared in the module's own edge-type constant. A `similar_to` or `co_activation` edge never contributes to a plan no matter how strong it is.
2. Remember `needs:` in the file becomes `depends_on` in the index. The frontmatter vocabulary and the edge-table vocabulary differ, and searching for the wrong one is a common dead end.
3. Read the closure as bounded, not exhaustive. Depth and size caps come from `Config`, so a legitimately deep chain is truncated rather than expanded forever — a short plan may be a bound being hit rather than a missing edge.
4. Expect a topological order from a Kahn sort, with a cycle guard rather than a crash. Declared edges are author-supplied and can contain a cycle, so the algorithm must terminate on input the format permits.
5. Use the recorded edge strengths when debugging order. The plan carries the effective strength of every in-closure edge that gates the ordering, which is the input the cycle-break total order uses to make a deterministic choice.
6. Note that dangling targets are included in the structural input used for plan confidence, even though they are dropped from routing. A declared edge naming an unregistered engram still says something about the author's intent, and confidence accounts for it.
7. Treat the module as framework-free and read-only. It takes a plain connection and holds to the same import discipline as the hot-path modules even though it is not itself on the forbidden-import list.
8. Verify a change against a registry with real declared edges, not the toy fixtures — plan behaviour is only interesting where an authored graph exists.

## Pitfalls
- (x1) Searching the edge table for `needs` and concluding the edge was never written. The stored type is `depends_on`.
- (x1) Reading a truncated plan as a missing edge when a depth or size bound was reached.
- (x1) Assuming declared edges form a DAG because they should. Nothing in the format prevents an author from writing a cycle, which is why the guard exists.
- (x1) Expecting plan expansion to change the top-ranked candidate. It runs last and elaborates the winner; it does not re-rank.

## Examples
+ "my plan stops after five steps" -> a depth bound, step 3
+ "two independent steps come out in an arbitrary order" -> the cycle-break total order, step 5
- "the wrong engram won the query" -> NOT this engram (ranking happens before planning)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
