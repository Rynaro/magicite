---
spec: engram/0.2
name: magicite-eval-baselines-abcd
id: egr_18eece55
version: 1
provenance: authored
intent:
  does: "Work on Magicite's four-baseline evaluation harness, which is the machinery that makes the project's own claims falsifiable"
  use_when: "changing the bench harness, adding an ablation, or interpreting what a difference between two baselines means"
  not_when: "you only need to run the standing benchmark, read its numbers, or decide what those numbers license you to claim"
triggers:
  positive:
  - "what do magicite baselines a b c and d actually measure"
  - "magicite bench baseline d is route unmodified"
  - "add an ablation arm to the magicite bench harness"
  - "why does magicite baseline a use token overlap not an llm"
  negative:
  - "just run the magicite benchmark and give me the numbers"
  - "is it fair to say magicite beats plain embedding search"
context_affinity: [magicite, evaluation, benchmark, falsifiability]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-run-retrieval-benchmark, magicite-edge-weight-two-channels]
yields: [valid-ablation-arm]
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
1. Read the four baselines as cumulative, each adding one design commitment over the last: description-only lexical matching, then dense embedding cosine, then embedding plus structural graph activation, then the full pipeline.
2. Note that the last baseline *is* the production router, called unmodified. That is deliberate: a change to routing behaviour cannot silently drift out of sync with what the harness measures, because there is no second implementation to drift from.
3. Note why the first baseline is not an LLM. The server ships no generative model, so the harness uses a deterministic, offline token-overlap proxy over description-level fields only — intent and positive triggers, never the body and never an embedding. Calling a model here would make the harness non-hermetic and non-reproducible.
4. Note that the third baseline suppresses the *learned* channel rather than removing the graph, using the dedicated learned-suppressed weighting helper. It is an ablation of one commitment, not a different algorithm.
5. Preserve the one deliberate asymmetry in that helper: a derived nearest-neighbour edge still contributes its stored value, because for that provenance the column holds a raw cosine rather than a Hebbian weight — suppressing it would flatten every neighbour to a single type gain.
6. Keep composition ground truth label-driven. It is derived once per distinct expected answer from that engram's own declared closure using the same expansion the router uses, so no baseline gets to define its own truth.
7. Add an ablation as an arm over the same query set rather than as a new harness. The value of the design is the *difference* between arms on identical inputs; a new harness measures nothing comparable.
8. Report negative results. The harness exists to make the project falsifiable, and a baseline that fails to beat its predecessor is the harness working, not a bug to tune away.

## Pitfalls
- (x1) Reimplementing routing inside the harness "to isolate it", which is exactly the drift the reuse-the-real-path design prevents.
- (x1) Introducing a generative model into a baseline, which breaks hermeticity and makes runs incomparable across time.
- (x1) Comparing arms measured on different corpora, query sets, or embedding providers, which makes the difference meaningless no matter how careful the arithmetic.
- (x1) Quietly dropping an arm that produced an unflattering number, which converts a falsification instrument into an advertisement.

## Examples
+ "I want to test whether inhibition helps" -> add an arm over the same queries, step 7
+ "why is baseline a so weak" -> it is description-only and LLM-free by design, step 3
- "what is our current Hit@1" -> NOT this engram (run the standing benchmark)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
