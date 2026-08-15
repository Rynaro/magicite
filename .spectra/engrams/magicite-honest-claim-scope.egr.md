---
spec: engram/0.2
name: magicite-honest-claim-scope
id: egr_3579bb59
version: 1
provenance: authored
intent:
  does: "State what Magicite's evidence does and does not license when writing its README, CHANGELOG, docs, or diagnostics"
  use_when: "writing or reviewing any user-facing claim about Magicite's routing, graph, or learning capability"
  not_when: "producing the measurement itself — run the benchmark first, then describe what it licenses"
triggers:
  positive:
  - "how should i describe magicite's routing results honestly"
  - "what does the magicite falsification record actually say"
  - "is it fair to claim magicite beats plain embedding"
  - "review a magicite claim for overstatement"
  negative:
  - "run the magicite-bench retrieval harness"
context_affinity: [magicite, docs, evaluation, honesty]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-run-retrieval-benchmark]
yields: [calibrated-claim]
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
1. Start from the Falsification Record in `docs/01` rather than from the feature list. That section, not the README's opening paragraph, is the project's own statement of what the evidence supports.
2. Say plainly that on the measured workload the full pipeline does not beat plain dense embedding on Hit@1, and give the counts rather than only the rates so a reader can see how narrow the gap is.
3. Do not describe a statistically indistinguishable result as a tie in the project's favour. Not significantly worse is not the same as as good as, and neither is evidence of benefit.
4. Keep the distinction between mechanism repair and validation. An improvement produced by fixing a defect that was suppressing measurement is a repair; it is not confirmation of the hypothesis the mechanism was meant to test.
5. State that the design's central claim — spreading activation over declared edges rather than re-derived similarity — has not been tested as designed, and do not let a corpus that happens to contain declared edges be presented as having tested it.
6. Do not report a reference registry size as a break-even point. The number in `docs/07` came from a pre-falsification heuristic, and a single unreplicated crossing does not establish it.
7. Carry the limitations every time: single-author corpus, single annotator, single embedder, uniform learning workload. They are not boilerplate; they are the reason the numbers are bounded.
8. Describe the capability surface separately from the hypothesis. A verified skill router with a portable format, a rebuildable index, and lifecycle governance is a real and defensible product claim that needs no evidential support from the untested routing hypothesis.

## Pitfalls
- (x1) Quoting a headline metric without the corpus, embedder, and query set that produced it, which converts a bounded measurement into a general claim.
- (x1) Letting a diagnostic message imply that crossing a registry size threshold means the routing machinery is now paying off.
- (x1) Reporting an improvement between two of the project's own versions as evidence against an external baseline.
- (x1) Softening a falsified result into a neutral one during a docs edit, which is how an honest record quietly decays.

## Examples
+ "can the README say magicite outperforms embedding search" -> no, steps 2 and 3
+ "our new registry has declared edges, does that test the hypothesis" -> no, step 5
- "I need the actual Hit@1 numbers first" -> NOT this engram (measure, then describe)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
