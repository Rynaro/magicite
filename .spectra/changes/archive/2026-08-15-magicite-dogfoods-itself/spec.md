# magicite-dogfoods-itself (tier: full)

## GIVEN / WHEN / THEN

**GIVEN** Magicite ships a 16-tool MCP skill-router surface, an `.egr.md`
portable skill format, a rebuildable SQLite index, a Dream consolidation
worker, a lifecycle/approval governance layer, and a documented Tier-2 hook
adapter for Claude Code — and yet its own repository contains **zero**
engrams of its own (`.spectra/engrams/` does not exist; the only `.egr.md`
files in the tree are the seven Proton/Steam fixtures under
`tests/fixtures/toy-registry/`), is **not** registered in its own
`.mcp.json` (which lists `crystalium`, `atomos`, `atlas-aci`, `junction`,
and `tonberry` but not `magicite`), and wires **none** of the Tier-2 hooks
its own `docs/adapters/claude-code.md` specifies,

**WHEN** an agent works on this repository,

**THEN**:

1. The repository SHALL carry a first-party, authored engram registry at
   `.spectra/engrams/` describing how to operate *this* project, ingestible
   under the `strict` lint profile with zero errors.
2. Every declared composition edge (`needs`/`composes`/`inhibits`) in that
   registry SHALL resolve to a registered engram — an authored graph, not a
   set of isolated nodes.
3. The full capability set SHALL be exercised end-to-end against that
   registry and the transcript recorded as evidence, not asserted.
4. The Tier-2 hook adapter SHALL be wired for this project, with the
   secret held only in host config and never in a tracked file.
5. The dogfooding SHALL be documented as an adapter/runbook so it is
   reproducible by a third party.
6. Nothing added SHALL be presented as evidence for the design hypothesis
   that `docs/01`'s Falsification Record records as untested.

## Tradeoff of record

**Which Magicite does this project's own `.mcp.json` run — the pinned
published container digest, or the local working tree?**

- *Pinned container* (`ghcr.io/rynaro/magicite@sha256:d4de4ea…`) is what
  `README.md` tells every external user to run, and is reproducible. But it
  is frozen at v0.1.0: dogfooding it exercises the *released* server, never
  the code being edited, so a regression introduced in `src/` is invisible
  to the very loop meant to catch it.
- *Local working tree* (`uv run magicite serve --project-root .`) routes
  through the code under development. A defect in an uncommitted edit
  surfaces immediately in the next `route()` call. The cost: it is not the
  artifact users run, and it depends on a synced `.venv`.

**Decision: local working tree.** The purpose of dogfooding is to feel our
own defects, and only the local-tree binding does that. The container path
is already covered mechanically by `tests/acceptance/test_docker_smoke.py`
(5 tests, including a `--network none` handshake and register/route cycle),
so choosing the local tree here loses no coverage of the published artifact.
Both wirings are recorded in the adapter doc; `.mcp.json` is gitignored
(machine-local), so the tracked deliverable is the documented snippet plus
the generator script, not the file itself.

## Acceptance checks

- **AC-D1** GIVEN the authored `.spectra/engrams/*.egr.md` registry, WHEN
  the durable index is deleted and `magicite sync --project-root .` is run,
  THEN every engram SHALL ingest under the `strict` profile with zero
  validation errors and the registered count SHALL equal the number of
  `.egr.md` files on disk.
  `verify_method`: `rm -f .spectra/engrams/skill-graph.db* && uv run magicite sync --project-root .`

- **AC-D2** GIVEN the synced registry, WHEN every declared `needs`,
  `composes`, and `inhibits` target is resolved against the set of
  registered engram names, THEN there SHALL be zero dangling declared
  edges.
  `verify_method`: `uv run python scripts/dogfood_check.py --edges`

- **AC-D3** GIVEN the synced registry, WHEN `route()` is called once per
  engram using that engram's own `intent.use_when` as the query, THEN the
  engram SHALL appear in its own top-3 candidates for at least 80% of the
  registry. This is a wiring sanity check on a single-author corpus, NOT a
  retrieval evaluation and NOT evidence for any routing hypothesis.
  `verify_method`: `uv run python scripts/dogfood_check.py --selfroute`

- **AC-D4** GIVEN a live stdio MCP session against the local tree, WHEN the
  capability set is exercised in order (`introspect`, `route`,
  `load_skill_body`, `signal_use`, `signal_outcome`, `session_end`,
  `consolidate`, `checkpoint`, `export`, `flag_dead`), THEN every call
  SHALL return a non-error result and the JSON-RPC transcript SHALL be
  recorded under the change folder.
  `verify_method`: `uv run python scripts/dogfood_session.py` + recorded transcript

- **AC-D5** GIVEN `.claude/settings.json` wired per `docs/adapters/claude-code.md`,
  WHEN a signal call carries the configured `MAGICITE_HOOK_TOKEN` as
  `adapter_token`, THEN `assign_tier` SHALL return tier 2; and WHEN the same
  call omits it or sends any other string, THEN it SHALL return tier 1.
  `verify_method`: existing AC-015 tests plus a hook-script dry run

- **AC-D6** GIVEN all changes, WHEN the frozen verify command runs, THEN
  `ruff check .`, `mypy src`, `pytest --cov=src/magicite --cov-fail-under=70`,
  and `pytest -m acceptance` SHALL pass, and `magicite tools` SHALL still
  report exactly 16 tools.
  `verify_method`: `uv run ruff check . && uv run mypy src && uv run pytest -q --cov=src/magicite --cov-fail-under=70 && uv run pytest -q -m acceptance`

- **AC-D7** GIVEN every document added or edited by this change, WHEN read,
  THEN none SHALL claim that authoring a first-party registry constitutes
  evidence that hierarchy-aware routing or declared-edge activation
  improves retrieval, and each SHALL point at `docs/01`'s Falsification
  Record where it touches the question.
  `verify_method`: manual review, recorded in the verification note

## Scope

`.spectra/engrams/*`, `.claude/settings.json`, `.claude/hooks/*`,
`scripts/dogfood_*.py`, `docs/adapters/dogfooding.md`, `docs/operations.md`,
`README.md`, `.gitignore`.

Out of scope: any change to `src/magicite/*` behaviour, the 16-tool surface,
routing defaults, or the evaluation harness. This change adds a *consumer*
of Magicite, not a modification of it.
