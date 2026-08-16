# magicite-codebase-skill-tranche (tier: full)

## GIVEN / WHEN / THEN

**GIVEN** the `magicite-dogfoods-itself` change authored 16 engrams that are
all *operational* — how to run, govern, release, and reason about Magicite —
and none of them describe the code an agent must actually change (the P0
hot-path boundary, the writer lease, the activation math, the two-channel
edge weight, the Dream phase pipeline, the writer's determinism contract,
the embedder protocol, the error envelope, or the evaluation baselines),

**WHEN** an agent is asked to modify `src/magicite/**`,

**THEN**:

1. The registry SHALL carry a second tranche of engrams covering Magicite's
   own codebase domains, each grounded in the module it describes rather
   than in recollection.
2. The two tranches SHALL form **one** connected graph, not two disjoint
   components — tranche-2 engrams declare edges into tranche-1 engrams
   where a real dependency exists.
3. Every new engram SHALL ingest under `strict` lint with zero errors and
   zero dangling declared edges, exactly as the first tranche does.
4. The combined registry SHALL still route: adding nodes SHALL NOT push a
   tranche-1 engram out of its own top-3.

## Tradeoff of record

**One registry, or two?** Operational runbooks and codebase internals are
different kinds of knowledge with different audiences.

- *Two registries* (`.spectra/engrams/` plus a second directory) keeps each
  set tight and makes precision easy: a query about running Magicite can
  never collide with a query about editing it. But Magicite has exactly one
  registry path per project root (`Config.registry_dir`), so a second set
  would need a second project root — meaning two servers, two indexes, and
  a routing decision the *host* has to make before Magicite ever sees the
  query. That pushes routing back into the layer Magicite exists to replace.
- *One registry* keeps a single `route()` call authoritative and lets the
  negative triggers and `inhibits` edges do the separating — which is
  precisely the mechanism the format provides for this. The cost is real:
  30 nodes instead of 16 means more near-misses, and an operational query
  can now surface an internals engram.

**Decision: one registry.** The cost is the mechanism working as designed,
and paying it is how we find out whether the mechanism is any good. AC-T4
exists to catch the case where this judgement turns out to be wrong.

**Correction, recorded after measurement.** This section originally claimed
the mitigation would be "authoring discipline, not partitioning: every
tranche-2 engram carries a negative trigger naming the operational engram it
is most likely to be confused with." **That mitigation does not exist.**
Measured directly: going from 16 to 30 engrams left all four tranche-1
probes in the top 3 but dropped rank-1 accuracy from 3/4 to 2/4, with
`magicite-eval-baselines-abcd` displacing `magicite-honest-claim-scope` on a
claim-honesty query. Adding a negative trigger and widening `not_when` on
the displacing engram changed the ranking **not at all** — because
`core/registry.py::embeddable_text` composes the embedded text from
`intent.does`, `intent.use_when`, `triggers.positive`, and the Procedure
steps only, and `core/router.py` never reads the `engram_trigger` table at
all. Negative triggers and `intent.not_when` are linted, fitness-scored,
stored, hashed into the engram id, rendered by the writer, and included in
the durable projection — and consulted by nothing at query time.

A declared `inhibits` edge *is* applied by routing, as a multiplicative
pass, so it was the obvious remaining lever — and it was tried, reactively,
on exactly this pair. **It made the result worse.** Declaring mutual
`inhibits` between `magicite-honest-claim-scope` and
`magicite-eval-baselines-abcd` pushed the intended engram out of the top 3
entirely (4/4 became 3/4), because symmetric inhibition suppresses the
weaker node harder when one already leads: the leader's activation scales
the follower down more than the reverse. That edge pair was reverted, and
the registry ships without it. The four `inhibits` edges that remain were
authored on their merits before any probe was run, and encode genuine
symptom-collisions (an authorizer denial versus a lease denial; a value that
moved by decay versus by potentiation).

So the honest position is: **no authoring-side mitigation for this cost is
known to work.** Rank-1 accuracy fell from 3/4 to 2/4 on enlargement, both
candidate mitigations were tried and neither helped (one was inert, one
backfired), and the registry ships with the degradation recorded rather than
disguised. Making negative triggers affect retrieval, or making inhibition
asymmetric, are routing changes requiring measurement under this project's
own amendment protocol; both are deliberately **not** attempted here.

## Acceptance checks

- **AC-T1** GIVEN the tranche-2 engrams, WHEN the durable index is deleted
  and `magicite sync --project-root .` is run, THEN all engrams SHALL
  ingest under the `strict` profile with zero validation errors, the
  registered count SHALL equal the number of `.egr.md` files on disk, and
  every one SHALL land `verification_status: verified`.
  `verify_method`: `rm -f .spectra/engrams/skill-graph.db* && uv run magicite sync --project-root .`

- **AC-T2** GIVEN the combined registry, WHEN declared edges are resolved,
  THEN there SHALL be zero dangling targets and the declared-edge graph
  SHALL be **connected across tranches** — at least three edges SHALL join
  a tranche-2 engram to a tranche-1 engram.
  `verify_method`: `uv run python scripts/dogfood_graph_check.py`

- **AC-T3** GIVEN each tranche-2 engram, WHEN its factual claims about
  `src/magicite/**` are checked against the module it describes, THEN every
  named symbol, constant, and threshold SHALL exist in the tree at the
  stated location.
  `verify_method`: `uv run python scripts/dogfood_graph_check.py --symbols`

- **AC-T4** GIVEN the enlarged registry, WHEN the tranche-1 probe set from
  `scripts/dogfood_session.py` is re-run unchanged, THEN every tranche-1
  probe SHALL still find its expected engram in the top 3 — adding nodes
  SHALL NOT regress the original wiring check.
  `verify_method`: `uv run python scripts/dogfood_session.py` (4/4 in top-3, as before)

- **AC-T5** GIVEN all changes, WHEN the frozen verify command runs, THEN
  `ruff check .`, `mypy src`, `pytest --cov-fail-under=70`, and
  `pytest -m acceptance` SHALL pass and `magicite tools` SHALL report 16.
  `verify_method`: the frozen verify command

- **AC-T6** GIVEN the tranche-2 engrams, WHEN read, THEN none SHALL present
  the enlarged registry as evidence for any hypothesis in `docs/01`'s
  Falsification Record.
  `verify_method`: manual review recorded in the verification note

## Scope

`.spectra/engrams/*` (new tranche-2 files, plus declared-edge additions to
existing tranche-1 files), `scripts/dogfood_graph_check.py`,
`docs/adapters/dogfooding.md`.

Out of scope: any change to `src/magicite/**`. Editing a tranche-1 engram's
`needs:` block is in scope; editing its `intent`/`triggers` is **not**,
because those fields are the id preimage and changing them would rotate an
id that is already registered (CR-8).
