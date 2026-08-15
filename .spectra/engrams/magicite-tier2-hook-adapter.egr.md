---
spec: engram/0.2
name: magicite-tier2-hook-adapter
id: egr_f21621ef
version: 1
provenance: authored
intent:
  does: "Wire the optional Tier-2 hook adapter on a Claude Code host so signal calls are externally verified rather than self-reported"
  use_when: "raising Magicite signal fidelity above Tier-1 self-report on a host that has a hook system, such as Claude Code"
  not_when: "the host has no hook system — Tier-1 and Tier-0 already work everywhere with no configuration at all"
triggers:
  positive:
  - "wire magicite tier 2 hooks in claude code settings"
  - "what does MAGICITE_HOOK_TOKEN actually change"
  - "make magicite signals hook verified instead of self reported"
  - "magicite adapter_token constant time comparison"
  negative:
  - "my mcp host has no hook support at all"
context_affinity: [magicite, claude-code, hooks, signals, security]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-route-and-signal-loop]
yields: [hook-verified-signal-tier]
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
1. Decide whether you need it. Tier 2 buys a full-weight contribution to storage strength and removes the per-session self-report cap; it does not unlock any tool. Tier-1 self-report is the shipped default on every host, deliberately.
2. Generate a high-entropy secret, for example with `openssl rand -hex 32`. It is never returned to the model, never logged, and never derivable from anything a client can observe.
3. Put the secret in the MCP server's environment in host config only, as `MAGICITE_HOOK_TOKEN`. If the server has no token set, Tier 2 is categorically unreachable for every call regardless of how the hooks are configured, which is the safe default.
4. Configure the host's hooks to pass that same secret as `adapter_token` on signal calls: fire `signal_use` before the routed skill is applied, and `signal_outcome` once its effect is observable.
5. Keep the secret out of every model-reachable surface — no prompt, no skill file, no tracked config, no tool-call argument the model composes itself. The hook script is trusted host configuration; anything the model can read or write is not.
6. Understand the trust model rather than trusting the label: the call's tier is decided server-side by one input only, a constant-time comparison of the supplied token against the server's own. Sending the literal string that names the tier, or any other value, without the matching secret earns Tier 1 exactly as sending nothing would.
7. Expect hooks to be probabilistic and design for it. A hook may fire late, fire against an expired session, or not fire at all, and the ladder degrades to Tier 1 and then Tier 0 without breaking plasticity.

## Pitfalls
- (x1) Storing the token in a tracked settings file. That publishes the secret to everyone with repository access and converts an external verification signal into something any reader can forge.
- (x1) Believing a client can assert its own tier. It cannot, and a bug or a hostile client that tries earns Tier 1 rather than a silent upgrade.
- (x1) Treating a missing hook as data loss. It is not; the same call still lands at Tier 1 through the routing instructions, and passive Tier-0 inference runs server-side with zero host cooperation.
- (x1) Wiring hooks but leaving the server's token unset, which silently produces Tier-1 calls that look configured.

## Examples
+ "our signals are all self_reported and we want them verified" -> steps 2 through 4
+ "is it safe to put the token in .claude/settings.json in git" -> no, step 5
- "we run magicite on a host with no hooks" -> NOT this engram (Tier 1 already works there)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
