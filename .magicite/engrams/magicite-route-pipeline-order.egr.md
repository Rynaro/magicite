---
spec: engram/0.2
name: magicite-route-pipeline-order
id: egr_5372d906
version: 1
provenance: authored
intent:
  does: "Locate the right stage of Magicite's route() pipeline before changing retrieval behaviour, and know which config knob governs each stage"
  use_when: "retrieval returns the wrong candidate and you need to find which stage caused it, or you are adding a stage to route()"
  not_when: "the registry itself never ingested the engram you expected — fix ingestion before debugging ranking"
triggers:
  positive:
  - "which stage of magicite route ranked this candidate"
  - "magicite routing pipeline order of operations"
  - "magicite hub penalty structural pagerank"
  - "where does magicite apply context conditioning and community rerank"
  negative:
  - "my engram never registered or landed quarantined"
context_affinity: [magicite, routing, retrieval, debugging]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-activation-ppr, magicite-edge-weight-two-channels]
yields: [located-routing-stage]
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
1. Walk the stages in order rather than guessing: cosine seeds, then sparse activation, then inhibition, then the weighted score, then the hub penalty, then context conditioning, then community rerank, then composition-plan expansion. A wrong result is a wrong stage, and the stages are separable.
2. For the seed stage, check the embedding provider first. Cosine seeds come from `eph_embedding` rows, so a provider mismatch between registration and query time produces nonsense before any graph work happens.
3. For inhibition, note that it applies the effective strength directly rather than multiplying by a type gain, because the type gain for `inhibits` is zero by design. Inhibition is multiplicative and separate from propagation, never negative graph mass.
4. For the weighted score, remember the four weights are meant to sum to one: activation, similarity, retrieval, and excitability. Changing one without rebalancing the others changes the score's scale as well as its shape.
5. For the hub penalty, do not assume it uses learned weights. It computes a deliberately *structural* PageRank with edge weight taken from the type gain alone, ignoring both the raw storage strength and its effective-strength successor.
6. Understand why that is deliberate, because it looks like an oversight. The original reason — that declared edges started at zero strength and would make the penalty inert — is now obsolete. The restated reason is that this is the one graph mechanism the benchmark measured as helping, and rewiring a measured-good component onto an unmeasured hunch is not warranted. Whether it should instead be weighted by learned topology is a recorded open experiment.
7. Treat the recent-failures branch of context conditioning as honestly inert rather than broken. It resolves against the fault-class column on procedure steps, which is the only such column the schema offers, and nothing currently populates it.
8. Change one stage at a time and re-measure against the baselines. The stages compose multiplicatively, so a two-stage change produces a number you cannot attribute.

## Pitfalls
- (x1) Assuming the hub penalty is usage-weighted. It is structural on purpose, and "fixing" it to use learned weights discards a measured gain in favour of an untested intuition.
- (x1) Adjusting one scoring weight without rebalancing the rest, which silently rescales every score rather than reweighting them.
- (x1) Debugging ranking when the real problem is ingestion. An engram that is quarantined or unverified is not routable at all and will never appear regardless of scoring.
- (x1) Reading the context-conditioning failure branch as a live signal. It is real code awaiting a producer.

## Examples
+ "a general engram outranks the specific one" -> look at the hub penalty and community rerank, steps 5 and 7
+ "everything scores nearly identically" -> check the seeds and the weight balance, steps 2 and 4
- "route never returns my new engram at all" -> NOT this engram (check registration and verification status)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
