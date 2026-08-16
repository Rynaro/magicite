---
spec: engram/0.2
name: magicite-edge-weight-two-channels
id: egr_75e9a9ef
version: 1
provenance: authored
intent:
  does: "Derive an edge's routing weight through Magicite's two-channel S_eff rule instead of reading storage_strength directly"
  use_when: "any code needs an edge's routing weight, or a declared edge appears to carry weight its storage_strength does not explain"
  not_when: "you are changing how activation propagates once weights already exist — that is the PPR module, downstream of this"
triggers:
  positive:
  - "why does a magicite declared edge weigh 1.0 when storage_strength is 0"
  - "magicite effective_strength versus raw storage_strength"
  - "magicite declared_edge_strength ablation revert"
  - "magicite AC-040 single weighting site"
  negative:
  - "how does magicite spread activation over the graph"
context_affinity: [magicite, routing, graph, invariants]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-p0-hot-path-boundary]
yields: [correct-edge-weight]
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
1. Call `core/edge_weight.py::effective_strength` and never read `edge.storage_strength` for a routing weight. This is enforced: `test_edge_weight_helper_is_the_only_weighting_site` fails the build if another module derives a weight itself.
2. Understand the rule as two channels, not one number. The effective strength is the maximum of the learned channel and the authored channel, where the authored channel is the configured declared-edge strength for a `declared` provenance edge and zero for everything else.
3. Understand why the authored channel exists. A `needs`, `composes`, or `inhibits` declaration is an assertion, not a statistic — there is no observable whose accumulation makes "A inhibits B" more true, and co-activation of A and B is evidence against that claim rather than for it.
4. Keep `storage_strength` meaning exactly one thing: the learned Hebbian channel. It starts at zero for a declared edge, only Dream raises it, and only for the types Dream can potentiate.
5. Remember the weight is computed at read and never stored. There is no column and no migration, and Dream's decay, prune, and renormalise passes keep operating on the learned column alone — so an authored assertion can never be decayed, pruned, or renormalised away.
6. Treat it as a floor rather than a replacement: learning may exceed an assertion but may never erase one.
7. Use the zero value as the exact ablation switch. Setting the declared-edge strength to zero reproduces pre-amendment behaviour bit for bit, because the maximum of a value and zero is that value.
8. Note the one deliberate asymmetry in the learned-suppressed variant used by the evaluation harness: a `derived` nearest-neighbour edge still passes its `storage_strength` through, because for that provenance the column holds a raw cosine similarity rather than a Hebbian value.

## Pitfalls
- (x1) Adding a local weighting expression "just here" rather than calling the helper. Three separate workarounds had already grown in the tree before the helper existed, which is precisely why the enforcement test was written.
- (x1) Treating `distilled` as authored. It is zero by explicit decision rather than by omission, so that a reserved-but-unused provenance cannot silently acquire full authored weight the day something starts emitting one.
- (x1) Expecting a declared edge's weight to grow with evidence. The authored channel is constant; only the learned channel moves, and only for potentiable types.
- (x1) Reaching for a new column to persist the effective value, which reintroduces the decay and prune interactions the compute-at-read design exists to avoid.

## Examples
+ "a brand new declared edge already influences routing" -> correct, steps 2 and 3
+ "how do I ablate authored edges entirely" -> set the strength to zero, step 7
- "activation is spreading to the wrong neighbours" -> NOT this engram (that is propagation, not weighting)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
