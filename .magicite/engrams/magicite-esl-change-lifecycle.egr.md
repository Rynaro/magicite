---
spec: engram/0.2
name: magicite-esl-change-lifecycle
id: egr_3369f7bf
version: 1
provenance: authored
intent:
  does: "Drive a change through this repository's ESL governance lifecycle from proposal to archive without stranding it"
  use_when: "starting, resuming, verifying, or closing a non-trivial change in a repository that carries a .spectra/changes tree"
  not_when: "the edit is a genuinely trivial fix that the right-sizing gate would classify as needing no spec at all"
triggers:
  positive:
  - "open an esl change for this repository"
  - "what status should this .spectra change be in"
  - "resume the in-flight esl change before starting new work"
  - "archive a verified change and record drift"
  negative:
  - "this is a one-line typo fix"
context_affinity: [magicite, esl, governance, process]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-frozen-verify]
yields: [archived-change-record]
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
1. Check for an in-flight change before opening a new one. A repository that already has a change in progress wants that one finished first, and stacking a second one is how both end up half-recorded.
2. Right-size before speccing. The gate takes three signals — files touched, a rubric score, and whether a real tradeoff exists — and returns a tier deterministically, so the same inputs always yield the same ceremony level.
3. Write the spec as given, when, and then, with acceptance checks in the same form and a concrete verify method on each one. An acceptance check whose verify method is an opinion is not a check.
4. Record the tradeoff explicitly when the tier demands it. Naming the option not taken, and why, is the part a later reader actually needs.
5. Move the status deliberately through the lifecycle rather than jumping to the end. Code-bearing states require code to exist, and archiving requires a recorded drift check.
6. Keep maker and checker distinct. The separation is mechanically enforced, and a change that verifies itself has not been verified.
7. Run the drift check against the spec of record before archiving, and treat a mismatch as a return to in-progress rather than something to note and move past.
8. Archive only at the end. Archiving moves the change folder, so the active folder stops existing and the record becomes the snapshot.

## Pitfalls
- (x1) Opening a new change while one is in progress, which leaves the first stranded at a status nobody later trusts.
- (x1) Writing acceptance checks with no executable verify method, which converts the gate into a formality.
- (x1) Self-verifying to save a round trip, which is exactly the property the maker and checker separation exists to prevent.
- (x1) Archiving before recording a drift check, or archiving a change whose spec no longer matches the tree.

## Examples
+ "there is an in-flight change and I have new work" -> finish the first, step 1
+ "how much ceremony does this change need" -> step 2, let the gate decide
- "I am fixing a typo in a comment" -> NOT this engram (below the spec threshold)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
