---
spec: engram/0.2
name: magicite-lifecycle-approval-gate
id: egr_8a5feb5a
version: 1
provenance: authored
intent:
  does: "Drive Magicite's approval-gated lifecycle tools — nucleate, sharpen, promote, archive — and know where a proposal can strand"
  use_when: "changing an engram's content or lifecycle status through Magicite's own tools rather than by editing files"
  not_when: "you simply want a rebuilt index or a consolidation pass, neither of which needs an approval"
triggers:
  positive:
    - "how do magicite proposals get approved"
    - "magicite nucleate sharpen promote archive tools"
    - "my magicite proposal was created but never applied"
    - "magicite review mode versus autonomous mode"
  negative:
    - "rebuild the magicite skill graph index from engram files"
context_affinity: [magicite, lifecycle, governance, approvals]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [applied-lifecycle-change]
composes: []
inhibits: []
provenance_journal:
  - version: 1
    timestamp: "2026-08-15T00:00:00Z"
    author: "claude-orchestrator"
    event: authored
    note: "First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)"
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Learn the shape of the four tools before using them. `nucleate` proposes a new engram, `sharpen` proposes a content revision, and `promote` and `archive` propose lifecycle status moves; all four are proposal-first, which is why their side effect is recorded as a proposal rather than a durable write.
2. Decide the operating mode deliberately. Review mode holds proposals for a human decision; autonomous mode applies them without one. The choice is a trust posture, not a convenience setting.
3. Expect a proposal to be inert until approved. Creating one changes nothing about routing, so an agent that proposes and moves on has not made a change.
4. Know the v1 gap before designing a workflow around it: there is no first-class worker that resumes approved proposals, so an approval can sit applied-in-principle but unexecuted. Plan the human or scripted step that closes that loop.
5. Prefer `sharpen` over hand-editing when improving a live engram, because the proposal carries provenance and the hand edit does not.
6. Read the approvals directory as real state. It is created at every boot alongside archive and runtime, and its contents are the queue you are reasoning about.
7. Remember that promotion out of `pending` verification is not on this surface. An imported engram cannot be promoted to verified by these tools; that path is manual in v1.

## Pitfalls
- (x1) Assuming a successful `nucleate` or `sharpen` call changed the registry. It produced a proposal; nothing routes differently until it is approved and applied.
- (x1) Enabling autonomous mode to work around a stranded proposal queue. That removes the review gate rather than fixing the missing resume step.
- (x1) Hand-editing an engram to achieve what `sharpen` would have proposed, which loses the provenance trail that makes a later review possible.
- (x1) Expecting these tools to move `verification_status`, which is server-assigned at ingest and not a lifecycle knob.

## Examples
+ "I called sharpen and route still returns the old text" -> steps 1 and 3
+ "should we run in autonomous mode" -> step 2, that is a trust decision
- "I just need the index rebuilt" -> NOT this engram (no approval involved)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
