---
spec: engram/0.2
name: magicite-amend-routing-default
id: egr_bcf05165
version: 1
provenance: authored
intent:
  does: "Change a routing or plasticity default in Magicite's Config with the evidence trail this project requires for a tunable"
  use_when: "a Config default such as a scoring weight, restart probability, or gain constant needs to move"
  not_when: "the value is being changed temporarily for one experiment — that is an ablation switch, not an amendment"
triggers:
  positive:
  - "change a magicite routing weight default"
  - "how do i amend ppr_restart or inhib_gain"
  - "what evidence is required to move a magicite tunable"
  - "magicite config declared_edge_strength ablation switch"
  negative:
  - "temporarily override a magicite config value for one experiment"
context_affinity: [magicite, config, routing, evidence]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-run-retrieval-benchmark, magicite-frozen-verify]
yields: [evidenced-config-amendment]
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
1. Read the existing comment on the field first. Every amended default in `config.py` carries an inline record of what it was, what was measured, and under what caveats, and that comment is the amendment protocol demonstrated rather than described.
2. Classify the change honestly as one of three kinds: measured, meaning a benchmark moved; derived, meaning the value follows arithmetically from another constant; or precautionary, meaning there is evidence against the old value but none for the new one. Each is legitimate and each must be labelled as what it is.
3. Produce the measurement before the edit, one field at a time, and record the corpus, query set, and embedding provider alongside the numbers.
4. Write the justification into the field's own comment, including the previous value. A default whose history lives only in a commit message is a magic number to the next reader.
5. Prefer a derived value to a tuned one where the arithmetic exists, and say what it was derived from, so a later change to the upstream constant tells the reader to re-derive rather than re-tune.
6. Preserve an exact revert path where the change alters a mechanism rather than a magnitude. A field whose zero value is a bit-for-bit revert to prior behaviour is an ablation switch, and saying so in the comment is what makes the change falsifiable later.
7. State explicitly when a value is precautionary rather than optimal. Claiming a measured optimum you do not have is the failure mode this whole protocol exists to prevent.
8. Run the frozen verify command afterwards, since several tunables are load-bearing for acceptance tests that assert routing arithmetic.

## Pitfalls
- (x1) Rounding a derived value to something prettier, which severs it from the constant it was derived from and turns it back into a magic number.
- (x1) Presenting a precautionary change as a measured improvement.
- (x1) Sweeping multiple fields together and attributing the aggregate result to one of them.
- (x1) Setting a mechanism-gating constant to zero without checking which acceptance tests become arithmetically unprovable as a result.

## Examples
+ "we think the restart probability is too low" -> measure first, steps 2 and 3
+ "can I just round this to 0.25" -> no, step 5, it is derived
- "I want to flip this value for a single ablation run" -> NOT this engram (use the config override, not an amendment)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
