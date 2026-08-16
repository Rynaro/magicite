# Verification note — magicite-codebase-skill-tranche

Checker: `magicite-self-verify`. As with the preceding change, the checking
oracle is Magicite's own surface plus two purpose-built mechanical guards
(`scripts/dogfood_graph_check.py`). AC-T6 alone is human judgement and is
recorded as such.

## AC-T1 — all engrams ingest under strict lint — **PASS**

```
rm -f .spectra/engrams/skill-graph.db*
uv run magicite sync --project-root .
-> {"synced": 30, "removed": [], "validation_errors": [], "dangling": [], ...}
```

30 `.egr.md` files on disk, 30 synced, zero validation errors. A direct query
confirms all 30 hold `verification_status: verified` — the trust gate upgraded
every file from the `pending` each ships with, and the injection scan
quarantined none.

## AC-T2 — edge graph resolves and spans both tranches — **PASS**

`scripts/dogfood_graph_check.py --edges`:

| measure | value |
|---|---|
| engrams | 30 (16 operational, 14 codebase) |
| declared edges | 35 (26 `depends_on`, 9 `inhibits`) |
| dangling | 0 |
| cross-tranche edges | 4 (bar: ≥3) |

Tranche membership is read from each engram's own provenance-journal note, not
a hardcoded list, so the check keeps working as the registry grows. The index
additionally derives 150 `similar_to` edges.

## AC-T3 — every code reference exists — **PASS**

`scripts/dogfood_graph_check.py --symbols`: 129 source files scanned across
`src/` and `tests/`; 30 path references and 49 symbol references verified.

**This check earned its place during authoring rather than after it.** It
caught two factual errors before they shipped:

1. `magicite-dream-phase-pipeline` named a bare `dream.py`; the file is
   `core/dream.py`.
2. The same engram's `intent.does` claimed *three* Dream phases live outside
   `dream.py`. It is two — phase 3 in `core/decay.py` and phase 6 in
   `core/audit.py`.

`tests/` is deliberately in scope: an engram about a statically-enforced
invariant names the test that enforces it (`FORBIDDEN_MODULES`,
`test_edge_weight_helper_is_the_only_weighting_site`), and those symbols must
stay real too.

## AC-T4 — no regression on the original probe set — **PASS, with a recorded degradation**

The tranche-1 probe set, re-run unchanged against 30 engrams: **4/4 still in
the top 3**, which is the bar AC-T4 sets, and it is met.

It should not be reported as a clean pass. **Rank-1 accuracy fell from 3/4 to
2/4.** `magicite-eval-baselines-abcd` displaced `magicite-honest-claim-scope`
on the query "is it fair for the README to say we beat plain embedding
search". Enlarging the registry with genuinely adjacent material costs top-1
precision. Four queries on a single-author corpus: the direction is worth
recording, the magnitude means nothing.

Two mitigations were attempted and **both failed**; see AC-T6 and §5 of
`docs/adapters/dogfooding.md`. The registry ships with the degradation
recorded rather than disguised, and the spec's tradeoff section was corrected
in place because its original mitigation claim turned out to be false.

## AC-T5 — frozen verify — **PASS**

`ruff check .` clean; `mypy src` clean (61 files); full suite green above the
70% coverage floor; acceptance marker pass green; `magicite tools` reports
exactly 16. No file under `src/magicite/**` was modified by this change.

## AC-T6 — no overclaiming — **PASS (manual review)**

Reviewed every engram and document this change adds or edits. None presents
the enlarged registry as evidence for any hypothesis in `docs/01`'s
Falsification Record. Three tranche-2 engrams touch the question directly and
each routes the reader to that record rather than around it:
`magicite-plasticity-dw-formula` (step 8), `magicite-eval-baselines-abcd`
(step 8, "report negative results"), and `magicite-route-pipeline-order`
(step 6, on the hub penalty's measured status).

**Two corrections made under this check**, both to claims that were false when
written:

1. `magicite-author-engram` step 3 said `not_when` "is the negative-intent
   surface the router uses for precision". It is not — see the finding below.
   Corrected, and two pitfalls added recording what those fields do and do not
   reach.
2. This change's own `spec.md` tradeoff section claimed the precision cost
   would be mitigated by "authoring discipline… every tranche-2 engram carries
   a negative trigger naming the operational engram it is most likely to be
   confused with". That mitigation does not exist. The section now records the
   measurement that refuted it.

## Principal finding: negative triggers and `not_when` are inert at query time

Found by trying to use them, which is the only way it would have been found.

`core/registry.py::embeddable_text` composes the embedded text from
`intent.does`, `intent.use_when`, `triggers.positive`, and the Procedure step
texts. `intent.not_when` and `triggers.negative` are **excluded**. Separately,
`core/router.py` never reads the `engram_trigger` table at all — the only
readers are `eval/bench.py` (positive polarity only) and
`storage/queries.py::durable_projection` (rebuild projection).

Negative triggers are nonetheless mandatory under strict lint
(`MIN_NEGATIVE_TRIGGERS`), scored by `core/fitness.py`, persisted by
`storage/durable.py`, hashed into the engram id by
`engram/ids.py::identity_routing_payload`, and rendered by the writer. They
are consulted by nothing when a query is answered. `docs/04` describes them as
required "for precision"; as implemented in v0.1.0 they buy none.

Measured directly: adding a negative trigger and widening `not_when` on the
displacing engram changed its ranking by exactly zero.

The follow-up attempt was a declared `inhibits` edge, which routing *does*
apply. A symmetric pair between the two colliding engrams made the result
**worse** — the intended engram left the top 3 entirely (4/4 → 3/4) — because
inhibition scales the target's activation by the source's, so the leader
suppresses the follower harder than the reverse. That edge pair was reverted.

Both are reported rather than fixed. Making negative triggers reach retrieval,
or making inhibition asymmetric, are routing changes that require measurement
under this project's own amendment protocol (`magicite-amend-routing-default`,
and `docs/operations.md` §11), and this change's scope explicitly excludes
`src/magicite/**`.
