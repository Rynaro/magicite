# Claude Code Host Adapter (Tier-2 Hook Adapter)

**Status:** v1, M3. **Normative source:** `docs/05-protocol-and-signals.md`
§"Tier-2: Host Adapter Matrix" and §"Signal Fidelity Model: The Three-Tier
Ladder"; `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/spec.md` §3.3 (`signal_use`/
`signal_outcome`) and §6.2 (the P0 enforcement point). This document does not
redefine anything those two sources already say — it is the concrete,
copy-pasteable configuration for one specific host.

Magicite's signal-fidelity ladder (docs/05 D3) works on **any** MCP host with
zero configuration: Tier 0 (passive server-side inference) is always on, and
Tier 1 (`signal_use`/`signal_outcome` called by an agent following the
`route()` response's `instructions` field) works on any host whose agent
follows instructions reasonably reliably. This adapter describes the
**optional, additive** Tier-2 acceleration available specifically on Claude
Code, via its hook system. Nothing here is required for Magicite to be
useful — it only raises signal confidence (85–95% vs Tier-1's 70–85%,
docs/05) and removes the per-session Tier-1 caps for hook-verified calls.

---

## 1. What Tier-2 actually changes

Per docs/05 §"How Plasticity Scales Across Tiers" and spec §6.2's Tier gate
(`core/plasticity.py::TIER_WEIGHT`):

| Tier | `tier_weight` (Dream's Δw, M4) | Per-session cap | Provenance stamped |
|---|---|---|---|
| 0 (inferred) | `0.0` — never reaches storage strength (S) | n/a (R + bookkeeping only) | `inferred` |
| 1 (self-reported) | `0.6` | `per_skill_session_cap` (default 3) applied by `core/signals.py::signal_use` | `self_reported` |
| 2 (hook-verified) | `1.0` | none (verified externally) | `hook_verified` |

**The tier is never something a client claims.** `core/signals.py::assign_tier`
is the only place a call's tier is decided, and it does so with exactly one
input: whether the call's `adapter_token` matches the server's own
`MAGICITE_HOOK_TOKEN` (via a constant-time comparison). Sending
`adapter_token="hook_verified"`, or any other string, without the matching
secret earns Tier 1 — the same as sending no token at all (AC-015). This is
true even for a host that *does* have hooks configured: a bug or a malicious
client cannot self-upgrade a call's trust level.

## 2. Configuring the adapter

1. **Generate a secret.** Any sufficiently random string works; it is never
   sent back to the model, never logged, and never derivable from anything a
   client can observe.

   ```sh
   openssl rand -hex 32
   ```

2. **Hold the secret only in host adapter config** — the Claude Code hook
   scripts (below), *not* in any prompt, skill file, or MCP tool-call
   argument a model constructs on its own. Set it as an environment variable
   the MCP server process (and only the hook scripts) can read:

   ```json
   {
     "mcpServers": {
       "magicite": {
         "command": "docker",
         "args": ["run", "--rm", "-i", "--user", "1000:1000",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "-v", "<project_root>:<project_root>:z", "-w", "<project_root>",
                   "ghcr.io/rynaro/magicite@sha256:486f3c510ad48d7e6a3ca32dfa2e40ba29b06e3572f13da9264e088834c87b67", "serve", "--project-root", "<project_root>"],
         "env": { "MAGICITE_HOOK_TOKEN": "<the secret from step 1>" }
       }
     }
   }
   ```

   **`--user 1000:1000` is required here, not optional** (M7 finding,
   `tests/acceptance/test_docker_smoke.py`): `magicite serve` calls
   `Config.ensure_dirs()` at every boot, creating `.magicite/{archive,
   approvals,runtime}` if absent. A bind mount preserves the *host's* file
   ownership, so a container running as the image's baked-in default (UID
   10001, i.e. this flag omitted) hits a bare `PermissionError` against any
   normal host-owned `<project_root>` before it can even complete
   `initialize` -- it is not merely a file-ownership hygiene nicety, the
   server cannot boot without it. Replace `1000:1000` with your own
   `$(id -u):$(id -g)` if your host user is not UID/GID 1000. See the
   Dockerfile's own PRIVILEGE-BOUNDARY NOTE for the full reasoning.

3. **Configure Claude Code's hooks** (`.claude/settings.json`, project- or
   user-scoped) to call `signal_use`/`signal_outcome` with the same secret as
   `adapter_token`. The hook scripts are the *only* place that reads
   `MAGICITE_HOOK_TOKEN` on the client side — they are trusted host config,
   not model-authored text.

If `MAGICITE_HOOK_TOKEN` is unset on the server, Tier 2 is categorically
unreachable for every call, regardless of hook configuration — this is the
safe default (v1 ships with no token configured; Tier-1 self-report is the
out-of-the-box experience on every host, Claude Code included).

## 3. Available hooks and what they should call

| Hook | When it fires | What to call |
|---|---|---|
| `SessionStart` | A new Claude Code session begins | Optional priming: `route()` with a broad query, or `introspect()`, to prime context with high-`R` ("hot") skills. Does not itself require `adapter_token`. |
| `PreToolUse` | Immediately before Claude applies a tool/skill selected via a prior `route()` call | `signal_use(skill_ids=[<id>], adapter_token=<secret>)` |
| `PostToolUse` + `Stop` | After the tool/skill's effect is observable (exit code, diff applied, task marked complete) | `signal_outcome(valence=<inferred>, salience=<confidence>, skill_ids=[<id>], adapter_token=<secret>)` |

### Valence inference (`PostToolUse`/`Stop`)

Per docs/05 §"Valence inference":

- **Positive** (`valence` close to `+1.0`): the driving command's exit code
  was `0`, the associated test suite passed, or the user's next message
  reads as confirmation ("works", "thanks", "ship it").
- **Negative** (`valence` close to `-1.0`): non-zero exit code, an error
  matched in tool output, or the user's next message reads as a correction
  or rejection.
- **Neutral** (`valence` near `0.0`, or skip the call): a timeout, an
  ambiguous/no-op result, or the user moves on without confirming either way.

`salience` should track *confidence* in the valence read, not the valence
itself — a clean, unambiguous exit code deserves a high `salience` (`0.8`–
`1.0`); a heuristic guess from chat text deserves a lower one (`0.3`–`0.5`).
Recall spec §3.3's credit-set rule: an *explicit* `skill_ids` list is always
honored regardless of salience; when `skill_ids` is omitted, only a
high-salience `|valence| > theta_salience` (default `0.7`) outcome triggers
retroactive credit to every skill still tagged (live) in the session.

### Caveat: hooks are probabilistic, by design

A hook may fire late, not at all (the agent's turn ends before `Stop`
triggers, the process is killed, the hook script itself errors), or against
a session whose tags have already expired (`session_ttl`, default 3h).
**This is expected and does not break plasticity.** The signal ladder
degrades gracefully:

- No `PreToolUse`/`PostToolUse` firing at all → the routing
  `instructions` field still asks the agent to call `signal_use`/
  `signal_outcome` itself → **Tier-1 fallback** (docs/05 §"Tier-1 Fallback
  for Hookless Hosts").
- No Tier-1 compliance either (a "dumb"/forgetful model, or a host with no
  tool-call loop at all) → **Tier-0 passive inference** still runs on every
  `route()`/tool call, server-side, with zero host cooperation required
  (`obs/events.py`, spec §3.3's "Tier-0 passive-inference capture path").
  Learning continues via exposure and co-retrieval alone — slower, but never
  dead (GAP-003, closed by design, docs/05 §"Question (FINDING-012)").

## 4. Asymmetry: what a hook can actually verify

The matrix in §3 assigns both `signal_use` and `signal_outcome` to hook
points, but wiring this adapter against a real registry (see
`docs/adapters/dogfooding.md`, which is this project's own instance of it)
showed the two are not equally verifiable from the host:

- **An outcome is host-observable.** A driving command's exit code, an
  error in tool output, and whether the turn ended in a correction are all
  visible to a `PostToolUse`/`Stop` hook. `signal_outcome` from a hook is
  therefore genuine external verification, which is what Tier 2 claims.
- **A use is not host-observable.** *Which* routed skill the agent chose to
  apply is known only to the agent. A hook firing `signal_use` on a guess
  would be manufacturing hook-verified evidence for a skill that may never
  have been applied — exactly the fabrication the tier gate exists to
  prevent. Tier 2 is only meaningful if the thing it attests is actually
  checked.

The practical resolution is a **correlation channel**: the agent records its
chosen skill where the hook can read it (this project uses one id or name
per line in `.magicite/runtime/hook-current-skill`, consumed and cleared by
the outcome hook), and the hook fires `signal_use` only when that record
exists. With no record, use stays Tier-1 self-report by design. This costs
nothing — a `signal_use` the hook declines to send is not a lost signal,
because the routing `instructions` field already asks the agent for the
Tier-1 call.

This does not weaken §1's trust model: the tier is still decided server-side
by the token comparison alone. It narrows *when* a host should claim Tier 2
for a use, which is a separate question from whether it can.

## 5. Future adapters

The Tier-2 mechanism itself is host-agnostic: any host with an equivalent
pre/post-tool-use hook system (Cursor, AgentKit, custom harnesses) can
implement the same three calls against its own hook points, holding its own
`MAGICITE_HOOK_TOKEN` in its own trusted config. This document is the first
instance of that pattern, not a Claude-Code-specific mechanism.
