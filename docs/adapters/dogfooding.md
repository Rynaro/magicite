# Dogfooding: Magicite routing for the Magicite repository

**Status:** v1. **Change of record:**
`.spectra/changes/magicite-dogfoods-itself/spec.md`. **Related:**
`docs/adapters/claude-code.md` (the Tier-2 hook adapter this builds on),
`docs/05-protocol-and-signals.md` (the signal ladder),
`docs/01-vision-and-hypotheses.md` (the Falsification Record, which bounds
every claim below).

Until this change, Magicite shipped a skill router with no skills of its
own. The repository contained seven `.egr.md` files, all of them Proton/Steam
fixtures under `tests/fixtures/toy-registry/`, and `.mcp.json` wired four
sibling servers but not Magicite. This document describes the first-party
registry that closes that gap, how to run the loop, and — because it is the
part most worth reading — what the exercise actually exposed.

---

## 1. What is wired

| Piece | Location | Tracked |
|---|---|---|
| First-party registry, 16 engrams | `.spectra/engrams/*.egr.md` | yes |
| Content-hash id stamper | `scripts/dogfood_ids.py` | yes |
| Authored-state restore | `scripts/dogfood_reset.py` | yes |
| Full 16-tool session driver | `scripts/dogfood_session.py` | yes |
| Tier-2 trust-boundary probe | `scripts/dogfood_tier_probe.py` | yes |
| `.mcp.json` entry generator | `scripts/dogfood_mcp_entry.py` | yes |
| Tier-2 signal hook | `.claude/hooks/magicite-signal.py` | yes |
| Hook wiring | `.claude/settings.json` | yes |
| The MCP entry itself | `.mcp.json` | **no** — gitignored, machine-local |

`.mcp.json` carries absolute paths and is deliberately untracked, so the
committed artifact is the generator rather than the file:

```sh
uv run python scripts/dogfood_mcp_entry.py --mode local           # print
uv run python scripts/dogfood_mcp_entry.py --mode local --apply   # merge in place
```

`--mode local` binds the working tree; `--mode container` binds the
digest-pinned published image. The change spec records why local is the
default: dogfooding exists to make us feel our own defects, and only the
local binding routes through the code being edited. The container path is
already covered mechanically by `tests/acceptance/test_docker_smoke.py`.

## 2. The registry

Sixteen authored engrams covering how to operate *this* repository — engram
authoring and SKILL.md import, index rebuild, embedding provisioning,
the container privilege boundary, the agent-facing route/signal loop, Tier-2
hooks, flat-plasticity diagnosis, Dream consolidation, the approval-gated
lifecycle, the frozen verify command, the benchmark harness, routing-default
amendment, claim-scope honesty, container release, and the ESL change
lifecycle.

They are not sixteen isolated nodes. The composition blocks declare a
connected graph — **12 `depends_on` and 5 `inhibits` edges**, resolved by
name at ingest with zero dangling targets — alongside the 80 `similar_to`
edges the index derives. The `inhibits` edges are the interesting ones,
because they encode genuine mutual exclusions rather than similarity:
`magicite-container-privilege-boundary` inhibits
`magicite-offline-embedding-setup` because the two share a symptom (the
server never answers) and have exclusive causes.

Rebuild the index from the files, which are the only source of truth:

```sh
rm -f .spectra/engrams/skill-graph.db*
uv run magicite sync --project-root .
```

Expected: `synced: 16`, and empty `validation_errors`, `removed`, and
`dangling`. All sixteen land `status: nascent`, `verification_status:
verified` — routable, with nothing caught by the injection scan.

## 3. Running the loop

```sh
uv run python scripts/dogfood_session.py --out /tmp/transcript.json
```

This speaks real JSON-RPC over stdio to `magicite serve` rather than
importing `magicite.core`, so a broken server fails the script. It walks
the whole surface: `introspect`, then four `route` → `load_skill_body` →
`signal_use` → `signal_outcome` cycles on questions a maintainer would
actually ask, then `session_end`, `consolidate`, `checkpoint`, `flag_dead`,
`sync`, `export`, and finally the four approval-gated R3 tools.

A recorded run of this script lives at
`.spectra/changes/magicite-dogfoods-itself/mcp-session-transcript.json`.

**On the routing numbers.** The four probes each found their intended
engram in the top 3, three of them at rank 1. That is a *wiring* check: it
says the registry ingested, embedded, and retrieves. It is **not** an
evaluation. The queries were written by the same author as the engrams they
match, there are four of them, and there is no held-out set — which is
close to the worst possible conditions for inferring anything about
retrieval quality. Nothing here bears on the hypotheses in
`docs/01`'s Falsification Record, and in particular nothing here tests
whether spreading activation over declared edges helps, which remains
untested as designed.

### Keeping the registry reproducible

A Dream checkpoint legitimately writes durable state back into the `.egr.md`
files: exposure counts move, `last_checkpoint` is stamped, `dream-worker`
entries are appended to the provenance journal, and the declared edges are
materialised into a `synapses:` block. That is the system working — it is
also probe history, not authored content, and committing it would make the
registry drift a little further on every run. So:

```sh
uv run python scripts/dogfood_reset.py           # restore authored state
uv run python scripts/dogfood_reset.py --check   # assert it, exit non-zero on drift
```

The reset touches only checkpoint-owned fields, and it keeps *learned*
synapses while dropping the declared ones, which are re-derived from the
composition block on every sync.

## 4. Tier-2 hooks

`.claude/hooks/magicite-signal.py` is wired into `.claude/settings.json` on
`PreToolUse` and `PostToolUse`. It is **inert until an operator sets
`MAGICITE_HOOK_TOKEN`** on the server: the token check is the first thing
the script does, above every non-stdlib import, so the unconfigured path
costs about 12 ms of interpreter startup and nothing else. Tier-1
self-report and Tier-0 passive inference are unaffected either way.

Verify the trust boundary against live servers rather than trusting the
label:

```sh
uv run python scripts/dogfood_tier_probe.py
```

It asserts five cases: with a token configured, the matching secret earns
tier 2 while a wrong secret, the literal string `hook_verified`, and no
token at all each earn tier 1; and with no token configured on the server,
even the real secret earns tier 1.

### What a hook can and cannot verify

This is the finding worth carrying back into `docs/adapters/claude-code.md`.
The adapter matrix assigns both `signal_use` and `signal_outcome` to hooks,
but the two are not equally verifiable from the host:

- **An outcome is host-observable.** Exit codes, tool errors, and whether
  the turn ended in a correction are all visible to a hook, so
  `signal_outcome` is genuinely hook-verified evidence.
- **A use is not.** *Which* routed skill the agent chose to apply is known
  only to the agent. A hook that guessed would be manufacturing
  hook-verified evidence for a skill that may never have been applied —
  precisely the fabrication Tier 2 exists to rule out.

The hook therefore fires `signal_use` only when the agent has recorded its
choice in `.spectra/runtime/hook-current-skill` (one id or name per line,
consumed and cleared by the outcome hook). Absent that file, use stays
Tier-1 self-report by design. Salience is pinned to 0.5 for hook-inferred
outcomes, because salience is confidence in the *valence read* and a
heuristic read does not deserve a confident one.

## 5. What dogfooding actually exposed

Four things that reading the code did not surface:

1. **`export` cannot run on a fresh registry.** `min_status` accepts only
   `consolidated` or `promoted`, and every newly authored engram is
   `nascent`. A new registry therefore exports zero SKILL.md shims until
   something consolidates. This is defensible — shims are a compile target
   for settled skills — but it means "author a registry, export shims for
   your stock host" is not a first-session workflow, and nothing said so.
2. **`export` enforces path containment.** An `out_dir` resolving outside
   the project root is refused with `path_outside_project`. Good behaviour,
   found by writing to `/tmp` and being told no.
3. **The AC-1 checkpoint fix works in the wild.** A checkpoint appended a
   `dream-worker` provenance entry and it was readable back from the file's
   own bytes on a fresh parse — the v0.1.0 release fix, confirmed outside
   its own test.
4. **The `signal_use` correlation gap** described in §4, which is a real
   limit of the documented Tier-2 design rather than a bug in it.

## 6. Scope of claims

Per `docs/01`'s Falsification Record and this change's AC-D7: authoring a
first-party registry is **not** evidence that hierarchy-aware routing,
declared-edge activation, or the learning layer improves retrieval. On the
measured workload the full pipeline does not beat plain dense embedding on
Hit@1 (0.5333 vs 0.5476, a three-query gap in 210), authored graph structure
has zero supporting measurements and two non-supporting ones, and the
design's central claim has never been tested as designed.

What this change demonstrates is narrower and still worth having: the
sixteen-tool surface, the portable format, the rebuildable index, the trust
gate, the approval-gated lifecycle, and the tier boundary all work
end-to-end against a real registry, driven over the same protocol an
external host uses. That is a claim about the product, not about the
hypothesis.
