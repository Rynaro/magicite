---
spec: engram/0.2
name: magicite-run-retrieval-benchmark
id: egr_12558cad
version: 1
provenance: authored
intent:
  does: "Run the magicite-bench retrieval harness and its ablations to produce comparable Hit@k and MRR numbers"
  use_when: "a claim about Magicite's routing quality needs measurement, or a routing default is about to change"
  not_when: "you only need to confirm the registry ingested and routes at all, which is a wiring check rather than a measurement"
triggers:
  positive:
  - "run the magicite-bench retrieval harness"
  - "measure magicite hit@1 and mrr against the baselines"
  - "magicite ablation baselines b and d"
  - "get comparable numbers before changing a routing default"
  negative:
  - "just confirm my engrams ingested and route at all"
context_affinity: [magicite, evaluation, benchmark, metrics]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-rebuild-skill-index]
yields: [measured-retrieval-metrics]
composes: []
inhibits: []
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
1. Read `docs/operations.md` on the bench harness, standing KPIs, and ablations before running anything, so the run you perform is comparable to the runs already on record.
2. Fix the corpus and the query set first and state both. Every published number in `docs/01` is against a specific registry size and a pre-registered query set, and a number taken against a different corpus is not comparable to it no matter how similar the command looked.
3. State the embedding provider with every number. A run under the deterministic hashing provider is not comparable to a run under the production ONNX embedder.
4. Run the relevant ablation baselines alongside the full pipeline rather than the full pipeline alone. The interesting quantity has always been the gap between plain dense embedding and the full pipeline, not the pipeline's absolute score.
5. Change exactly one configuration field at a time. The recorded sweeps in `config.py` are one-field-at-a-time for a reason: a multi-field change produces a number you cannot attribute.
6. Report the query-count difference, not just the rate. A gap of three queries in two hundred and ten is a different claim from the same ratio at a larger scale, and the count is what makes the difference legible.
7. Carry the caveats into whatever you write. The measurements on record are single-author corpus, single annotator, single embedder, and uniform learning workload, and dropping those qualifiers turns a bounded result into an overclaim.

## Pitfalls
- (x1) Comparing a fresh run against a published number taken on a different corpus, embedder, or query set, which is the most common way a Magicite measurement becomes meaningless.
- (x1) Reporting a rate without the underlying counts, which hides how few queries separate two baselines.
- (x1) Sweeping several tunables at once and attributing the result to the one you cared about.
- (x1) Treating a single unreplicated crossing as an established break-even point.

## Examples
+ "we changed a routing weight, is it better" -> steps 4 and 5
+ "can I cite the 0.5333 number for my registry" -> no, step 2, that number is corpus-specific
- "did my new engrams register correctly" -> NOT this engram (that is a wiring check)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
