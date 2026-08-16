---
spec: engram/0.2
name: magicite-activation-ppr
id: egr_863244b8
version: 1
provenance: authored
intent:
  does: "Work on Magicite's sparse personalized-PageRank activation: the graph build, the softmax personalization vector, and the power iteration"
  use_when: "changing how activation spreads, debugging an activation vector, or adding an edge type to the propagation graph"
  not_when: "the question is how an edge's weight is computed in the first place — that is the two-channel S_eff rule, upstream of this"
triggers:
  positive:
    - "how does magicite spread activation over the graph"
    - "magicite personalized pagerank dangling mass"
    - "magicite softmax personalization temperature seeds"
    - "why is magicite's activation graph COO sparse"
  negative:
    - "how is a magicite edge's routing weight derived"
context_affinity: [magicite, routing, graph, numerics]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [activation-vector]
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
1. Read `core/activation.py` as a framework-free module. It takes plain node ids and edge weights and returns plain numpy vectors, with no database handle, which is what lets the hot-path router import it without breaching the P0 import boundary.
2. Understand the representation before changing it. The graph is COO sparse — parallel `src_idx`, `dst_idx`, and `weight` arrays — not a dense matrix, because a registry's edge count is linear in node count (declared composition edges plus a bounded top-`m` nearest-neighbour set), never quadratic.
3. Note that `build_graph` row-normalizes at construction: each edge's weight is divided by its source node's outgoing total. The power iteration is therefore a plain scatter-accumulate with no per-iteration renormalization.
4. Note what `build_graph` silently drops: any edge naming a node outside the supplied universe, and any edge with non-positive weight. `inhibits` edges are never fed in at all — inhibition is a separate multiplicative pass, not negative graph mass.
5. For seeding, use `softmax_personalization`, which places `softmax(cosine / temperature)` over the seed set and zero everywhere else. It subtracts the max before exponentiating, so do not "simplify" that away — it is what keeps the softmax numerically stable.
6. For propagation, read the iteration as written: the next vector is `restart * p + (1 - restart) * (W-transpose applied to the current vector, plus dangling mass redistributed through p)`. A node with no outgoing edges redistributes its mass through the personalization vector rather than dropping it, which is what makes an edgeless graph's fixed point exactly `p`.
7. Take the restart value from `Config`, not from the function signature. This is the sharp edge: the function's own default is the original specification value, while the configured `ppr_restart` was amended upward on measurement. Reading the signature and assuming it describes production behaviour will mislead you.
8. Re-measure after any change here, one field at a time, against the benchmark baselines rather than by eye.

## Pitfalls
- (x1) Assuming the function-signature default for restart is what production uses. `Config.ppr_restart` was amended after measurement and is the authoritative value; the signature default was never updated to match.
- (x1) Feeding `inhibits` edges into the propagation graph. They carry zero positive type gain by design and belong to the separate inhibition pass.
- (x1) Removing the max-subtraction in the softmax as a micro-optimisation, reintroducing overflow for large cosine-over-temperature values.
- (x1) Switching to a dense matrix "for clarity". It changes the honest complexity class from linear to quadratic in node count for no behavioural gain.

## Examples
+ "activation is uniform across the whole registry" -> check the seed set and the restart value, steps 5 and 7
+ "should inhibits edges be in build_graph" -> no, step 4
- "why does a declared edge weigh 1.0 when its storage_strength is 0" -> NOT this engram (the two-channel rule)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
