---
spec: engram/0.2
name: magicite-route-and-signal-loop
id: egr_2daf4dde
version: 1
provenance: authored
intent:
  does: "Consume a Magicite route() result correctly and close the signal loop so the registry actually learns from the session"
  use_when: "an agent has Magicite wired as an MCP server and needs to know which tools to call, in what order, around using a routed skill"
  not_when: "plasticity is already known to be flat and you are diagnosing why signals are not moving storage strength"
triggers:
  positive:
  - "what do i call after magicite route returns candidates"
  - "how does an agent report skill outcomes back to magicite"
  - "magicite signal_use and signal_outcome ordering"
  - "close the magicite session so consolidation is enqueued"
  negative:
  - "magicite storage strength never changes no matter what i send"
context_affinity: [magicite, mcp, signals, agent-loop]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-rebuild-skill-index]
yields: [closed-signal-loop]
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
1. Call `route()` with the user's actual task phrasing rather than a keyword you invented; the triggers were written to match real requests, and rewriting the query into keywords discards the signal the router is built on.
2. Read the `instructions` field of the response instead of ignoring it. That field is how Tier-1 signal fidelity works on any host with no hook support at all, and following it is the difference between a registry that learns and one that only serves.
3. Fetch the body with `load_skill_body()` for the candidate you actually intend to follow, not for every candidate returned. Loading is itself an observable event.
4. Call `signal_use()` with the specific `skill_ids` you are about to apply, before applying them. Both `route` and `load_skill_body` are read-only; `signal_use` is the first call that records intent.
5. Call `signal_outcome()` once the effect is observable, with `valence` reflecting direction and `salience` reflecting your confidence in that reading. A clean non-zero exit code deserves a high salience; a guess inferred from conversational tone deserves a low one.
6. Pass `skill_ids` explicitly on `signal_outcome` whenever you know which skill was responsible. An explicit list is always honoured, whereas an omitted list only produces retroactive credit when salience clears the configured threshold.
7. Call `session_end()` when the task closes. It is the call that enqueues consolidation work; skipping it leaves the session's evidence sitting in ephemeral state until the TTL expires.
8. Let the Dream worker do the durable writing. Nothing in this loop should be writing storage strength directly — the loop produces evidence, consolidation turns evidence into durable state.

## Pitfalls
- (x1) Sending a high-confidence salience for a guess. Salience is confidence in the valence reading, not the strength of the outcome, and inflating it causes retroactive credit to spray onto skills that were merely present in the session.
- (x1) Calling `signal_use` for every candidate the router returned. That teaches the registry that all of them were used and destroys the precision the negative triggers were written to provide.
- (x1) Expecting storage strength to move immediately after a signal. Tier-0 inferred signals carry zero weight toward storage strength by design, and even Tier-1 evidence only becomes durable through a consolidation pass.
- (x1) Letting the session expire instead of ending it. The default session TTL is measured in hours, and an expired session's tags cannot be credited retroactively.

## Examples
+ "magicite returned three candidates, now what" -> steps 3 through 5
+ "should I report a failure or stay silent" -> report it; a negative valence is evidence, silence is not
- "I have been sending signals for a week and S is still zero" -> NOT this engram (diagnose flat plasticity instead)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
