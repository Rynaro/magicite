# Verification note — magicite-dogfoods-itself

Checker: `magicite-self-verify`. The checking oracle for this change is
Magicite's own deployed surface: the registry is accepted or rejected by
`register()`'s strict lint and trust gate, the tools are exercised over real
stdio JSON-RPC against `magicite serve`, and the tier boundary is asserted
against live servers. Those are mechanical and external to the maker; AC-D7
alone is a human-reviewable judgement and is recorded as such.

## AC-D1 — registry ingests under strict lint — **PASS**

```
rm -f .spectra/engrams/skill-graph.db*
uv run magicite sync --project-root .
-> {"synced": 16, "removed": [], "validation_errors": [], "dangling": [],
    "detector": "leiden", "consolidation_scheduled": false}
```

16 `.egr.md` files on disk, 16 synced, zero validation errors. All sixteen
landed `status: nascent`, `verification_status: verified` — routable, and
nothing was quarantined by `injection_scan` (no exec blocks, no over-broad
triggers, no suspicious pitfall text).

Note that `verification_status` is server-assigned: every authored file ships
`pending` and the server upgraded it, which is the trust gate working rather
than the files asserting their own trust.

## AC-D2 — zero dangling declared edges — **PASS**

`dangling: []` in the sync result, plus a direct query of the `edge` table:

| type | provenance | count |
|---|---|---|
| `depends_on` | declared | 12 |
| `inhibits` | declared | 5 |
| `similar_to` | derived | 80 |

All 17 declared edges resolve to registered engram names. The graph is
connected, not sixteen isolated nodes.

## AC-D3 — self-route wiring check — **PASS (4/4)**

| probe | result |
|---|---|
| "the magicite container dies with a permission error before the handshake finishes" | rank 1 |
| "I edited an engram file but route still gives me the old procedure" | rank 3 |
| "is it fair for the README to say we beat plain embedding search" | rank 1 |
| "what should an agent call after route returns its candidates" | rank 1 |

4/4 in top-3 (bar: 80%), 3/4 at rank 1. **This is a wiring check, not a
measurement.** Four queries, written by the author of the engrams they match,
with no held-out set. It licenses "the registry ingested, embedded, and
retrieves" and nothing beyond that. See AC-D7.

## AC-D4 — full 16-tool surface over stdio MCP — **PASS**

`uv run python scripts/dogfood_session.py` exits 0. Handshake reports
`serverInfo.name = magicite` and 16 tools advertised. Transcript recorded at
`mcp-session-transcript.json` in this folder.

Every tool returned a non-error result: `introspect` (registry_size 16,
`autonomous_mode: false`), four `route`/`load_skill_body`/`signal_use`/
`signal_outcome` cycles, `session_end`, `consolidate` (idempotent — returned
the run already enqueued by `session_end`), `checkpoint` (13 engrams written
on the first pass, 0 on the second — correctly idempotent), `flag_dead`,
`sync`, `export`, and the four R3 tools.

All four R3 tools returned `requires_approval: true` and mutated nothing,
which is review mode (the default) behaving correctly. `promote` reported
`rubric_score: 12` against `rubric_min: 8` — it would clear the evidence bar
and is still gated behind an approval, which is the intended posture.

Two findings from this AC, both recorded in `docs/adapters/dogfooding.md` §5:
`export` refuses an `out_dir` outside the project root
(`path_outside_project`), and `export` accepts only `min_status` of
`consolidated`/`promoted`, so a freshly authored all-nascent registry exports
zero shims.

Incidentally confirmed: the v0.1.0 release fix (AC-1 of
`magicite-v0-1-0-release`) works outside its own test — a checkpoint-appended
`dream-worker` provenance entry was readable back from the `.egr.md` file's
own bytes on a fresh parse.

## AC-D5 — Tier-2 trust boundary — **PASS (5/5)**

`uv run python scripts/dogfood_tier_probe.py`, against two live servers:

| server | token sent | tier | want |
|---|---|---|---|
| token configured | the matching secret | 2 | 2 |
| token configured | a wrong secret | 1 | 1 |
| token configured | literal `"hook_verified"` | 1 | 1 |
| token configured | none | 1 | 1 |
| **no** token configured | the real secret | 1 | 1 |

A client cannot talk its way into Tier 2, and a server with no token makes
Tier 2 categorically unreachable.

The hook itself (`.claude/hooks/magicite-signal.py`) is wired into
`.claude/settings.json` and is inert until `MAGICITE_HOOK_TOKEN` is set:
measured at ~12 ms per invocation on the unconfigured path (interpreter
startup only — the token check precedes every non-stdlib import).

## AC-D6 — frozen verify — **PASS**

`ruff check .` clean; `mypy src` clean (61 files); full suite green with
coverage above the 70% floor; acceptance marker pass green; `magicite tools`
reports exactly 16. No assertion was weakened anywhere.

## AC-D7 — no overclaiming — **PASS (manual review)**

Reviewed every document this change adds or edits:

- `README.md` — the new Dogfooding section states outright that a
  self-authored registry is not evidence for any hypothesis in docs/01's
  Falsification Record.
- `docs/adapters/dogfooding.md` — §3 qualifies the 4/4 routing result as a
  wiring check with named limitations (single author, four queries, no
  held-out set); §6 restates the falsification numbers with counts and says
  the central claim remains untested as designed.
- `docs/adapters/claude-code.md` — the new §4 makes a claim about hook
  verifiability only; it touches no routing hypothesis.
- `docs/operations.md` §14 — operational instructions only.
- `.spectra/engrams/*.egr.md` — `magicite-honest-claim-scope` encodes the
  claim-scope discipline directly, and `magicite-run-retrieval-benchmark` and
  `magicite-diagnose-flat-plasticity` both route the reader to docs/01 rather
  than implying a benefit.

No document claims that authoring declared edges tests the declared-edge
hypothesis. That distinction is called out explicitly in
`docs/adapters/dogfooding.md` §3 and in the `magicite-honest-claim-scope`
engram's own procedure, step 5.

## Out of scope, confirmed unchanged

No file under `src/magicite/` was modified by this change. The 16-tool
surface, routing defaults, and evaluation harness are untouched: this change
adds a consumer of Magicite, not a modification of it.
