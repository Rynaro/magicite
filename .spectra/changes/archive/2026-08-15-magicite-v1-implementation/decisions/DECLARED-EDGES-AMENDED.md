---
eidolon: ramza
kind: decision
id: DECLARED-EDGES-AMENDED
version: 1.0.0
created_at: 2026-08-15
change_id: magicite-v1-implementation
supersedes: "spec.md §3.3 route steps 4, 5 and 10 as emitted; spec.md §2.6 step 4's silence on where a declared edge's routing weight comes from; the routing defaults ppr_restart=0.15, w_similarity=0.30, w_retrieval=0.15"
status: recorded
disposition: "formal spec amendment — a design gap no code patch can legitimately close, plus two evidence-driven default changes. New acceptance criteria land in a marked addendum; the frozen criteria file is byte-identical and is NOT re-frozen."
authorizing_verdict: "FORGE (Reasoner) — 'adjudicating the partial falsification of H-BODY', 2026-08-15, composite ROOT-CAUSE + TRADE-OFF + CONSTRAINT-SATISFACTION, standard depth (2 passes), requires_checker true. §4 [VERDICT] 'Damp, don't amputate' (Tier-1 items 1–3); §5 remedy (a)/(b)/(c) — amend the spec to state where a declared edge's S_edge comes from, write a NEW AC whose GIVEN names the ingestion path, and do it WITHOUT editing frozen AC-023."
authorizing_verdict_path: "scratchpad/falsification-verdict.md (session-local; ephemeral — every decisive clause is reproduced below so this record stands alone)"
evidence_path: "scratchpad/atlas-synapses/scout-report.md (ATLAS, confirmed by execution: probe1–probe4.py, run2.log, before.json, after.json) and scratchpad/scale-benchmark.md (70 engrams / 210 pre-registered queries, component sweep §6)"
implemented_by: vivi
recorded_by: ramza
appendonly: true
---

# DECLARED-EDGES-AMENDED — an authored edge asserts its own weight; only the Hebbian channel earns

> **Append-only.** This record is written once. If the rule is amended again, write
> `DECLARED-EDGES-AMENDED-2.md` and mark this record superseded; do not edit it in place.

---

## 1. The gap, stated as a spec defect rather than a bug

Author-declared edges (`needs`→`depends_on`, `composes`, `inhibits`) carry **exactly zero**
activation mass, permanently, and **no code path in the shipped system can ever raise them.**

Confirmed by ATLAS **by execution**, not by reading: on a 7-engram registry with 2 `composes`,
2 `depends_on` and 1 `inhibits` declared, `route()` at spec defaults and `route()` with all
declared `type_gain`s **and** `inhib_gain` set to zero return **bit-identical scores to 12
decimal places**. Forcing a declared `S_edge = 0.9` *does* change the ranking — the code path is
live; it is simply never fed. `build_graph` drops `w <= 0`, so `composes`/`depends_on` are not
weak, they are **absent from the graph**: `composes 2 rows -> 0 kept`, `depends_on 2 rows -> 0
kept`, `similar_to 35 rows -> 35 kept`.

The mechanism has three links, each verified in source:

| Link | Site |
|---|---|
| Declared edges are inserted with `storage_strength` as a hardcoded literal `0.0`, and the `ON CONFLICT` clause updates only `dst_id`/`dangling`, so re-`sync()` never raises it | `storage/durable.py:203-205` |
| Dream can only potentiate `co_activation` — the only type with candidate rows or edge tags (`insert_edge_tag` has exactly one caller, `edge_type="co_activation"` hardcoded) | `core/dream.py:194`, `core/signals.py:152-164` |
| Activation weight is `S_edge × type_gain`, and `build_graph` drops non-positive weights | `core/router.py:295`, `core/activation.py:56` |

**Why this is the spec's problem and not Vivi's.** Three shipped, client-visible behaviours are
mathematically dead as a direct consequence, and two of them are things this spec *asked for*:

1. **`spec.md:593` §3.3 step 10** defines `plan_confidence = mean(S_edge over plan edges) ×
   (resolved_deps / declared_deps)`. Plan edges are `depends_on`/`composes` — always `declared`,
   never a type Dream can potentiate — so `mean(S_edge)` is **structurally** 0.0 and
   `plan_confidence` is **permanently 0.0 for every multi-node plan**. Measured by ATLAS:
   `plan_confidence = 0.0`, `edge_strength = {…: 0.0, …: 0.0, …: 0.0}`. It reaches clients through
   `core/router.py:378,395` → `mcp/schemas.py:150` → `mcp/bind_retrieval.py:66`. **The spec asked
   for something unsatisfiable as written.** No patch can fix that without a spec decision.
2. **`apply_inhibition` is a numeric no-op** (`a_i *= (1 − 0.0 × 0.7) = 1`), so **AC-023 is
   unreachable in production**. It passes only because `tests/unit/core/test_router.py:80` injects
   `storage_strength = 0.8` by direct SQL — a state production cannot produce. Independently
   corroborated by the component sweep: `inhib_gain = 0` changes nothing to four decimals.
3. The same zero silently removes declared `composes`/`depends_on` from the activation graph, so
   the "skill graph" on any un-consolidated registry **is exactly and only the embedding kNN
   graph** — a lossy, query-independent re-derivation of a signal already scored at
   `w_similarity`.

---

## 2. The counter-argument, engaged rather than dismissed

`storage/durable.py:304-309` documents the 0.0 as deliberate, and it deserves quoting in full
because it is a coherent design position and not a slip:

> a declared `needs`/`composes`/`inhibits` edge … encodes intent and must still be *learned*
> (Dream, M4) before it carries weight (spec Approach commitment 2: writers — and by extension,
> weight — are earned, not assumed).

Two things are true about it simultaneously.

**It is coherent.** "Weight is earned, not assumed" is a real principle of this design, it is the
same principle that bars Tier-0 from S and makes Dream the only S writer, and a system that let
an author assert routing weight by writing a line of YAML would be trivially self-promotable.

**It is not a defence of the current state.** The mechanism by which a declared edge would *earn*
weight **was never built**, and M4 has shipped. `insert_edge_tag` is called from exactly one site
with `edge_type` hardcoded; Dream's Phase-1 candidate selection is `WHERE type = 'co_activation'`;
Dream's edge branch *would* potentiate any type it was handed and is never handed another one.
There is no path — not `register()`, not `sync()`, not SKILL.md import, not `sharpen()`, not
`promote()`, not any of Dream's seven phases, not any migration, not any of the 16 tools — that
can move a declared edge off 0.0. A principle whose enforcement mechanism is total and whose
earning mechanism does not exist is not a policy; it is a permanent zero wearing a policy's
clothes.

**The strongest available evidence for oversight over intent** is that this codebase diagnosed
this exact failure mode **three separate times** and worked around it locally each time, always
with the same premise:

- `core/registry.py:60-71` — `_COMMUNITY_WEIGHT_FLOOR = 0.1`, applied as `max(S_edge, 0.1)`:
  *"Weighting community structure purely by S_edge would … make every declared needs/composes/
  co_activation edge structurally invisible until Dream (M4) exists."*
- `core/router.py:14-25` — the hub-penalty PageRank uses `type_gain` only, *"as an honest
  until-Dream-exists proxy"*, because S_edge weighting *"would … make the hub penalty permanently
  inert on any registry that has never been through a consolidation run."*
- `eval/bench.py:70-78` — baseline (c) uses fixed `type_gain`, *"never S_edge … S_edge starts at
  0.0 for every declared edge until Dream potentiates it."*

All three share the premise *"inert **until Dream/M4** exists."* **M4 shipped.** Dream still
cannot potentiate a declared edge type. Each site patched its own metric; nobody patched the
three places where the same zero is load-bearing — the activation graph, `plan_confidence`, and
inhibition. FORGE §5: *"A known fix applied inconsistently is an oversight, not a design intent."*

---

## 3. Ruling — FORGE's position is **adopted**, and its mechanism is **sharpened**

FORGE's §5(a) recommended position:

> declared mutual exclusion is an *authored assertion*, not an earned Hebbian weight, and gating
> it behind "strength must be earned" was a category error.

**Adopted on the substance.** `inhibits: [proton-clean-install]` is a *statement about semantics*
— these two things are mutually exclusive — not a *statistic about co-occurrence*. There is no
evidence for it to accumulate and no number of observations that would make it more true. The
same holds for `needs:` and `composes:`: "this skill requires that skill" is a fact of the
composition DAG the author is authoritative over. Requiring an assertion to be corroborated by
usage before it may act is a category error, and docs/04 presents these fields as live
composition contracts, which an author has every reason to believe.

**Sharpened in three places, because the position as phrased gives away more than it needs to.**

1. **The category error was the *conflation*, not the principle.** "Weight is earned" is true and
   stays true — of the **Hebbian channel**. `edge.storage_strength` is a *learned statistic*: it
   is comparable across edges, it is what decay erodes, what prune thresholds, what
   `theta_synapse` gates, and what Dream renormalises. The defect is that one scalar was made to
   carry two incompatible semantics and every consumer read only that scalar. So this amendment
   does **not** say "declared edges get weight assumed instead of earned." It says: **an edge's
   routing weight has two channels — an authored one and a learned one — and `S_edge` is only the
   second.** The `durable.py` docstring's position survives verbatim in the channel it actually
   describes.
2. **A floor, not a replacement.** FORGE's "fixed gain independent of `S_edge`" would make an
   authored edge deaf to learning forever. `max(S_edge, authored)` instead: **learning may exceed
   an assertion; it may not erase one.** This also keeps `S_eff ∈ [0,1]`, so every formula that
   assumed an S in that range (notably `1 − S·inhib_gain > 0`) keeps its arithmetic.
3. **Scope: all authored edge types, not just `inhibits`.** FORGE's §5 remedy is written about
   inhibition and flags the broader case as a "scope note." Fixing inhibition alone would leave
   `composes`/`depends_on` dropped from the graph and `plan_confidence` at 0.0.

**FORGE's arithmetic objection to a floor is answered, not accepted.** §4 rejects a floor because
*"with a 0.1 floor, the 35 declared edges contribute ≈ 3.5 units against ≈ 126 for the 350
`similar_to` edges — about 2.7% of total mass."* That objection is to the **magnitude 0.1**, which
it inherited from `_COMMUNITY_WEIGHT_FLOOR`; it is not an objection to the **form**. At the
authored magnitude (§4) the same arithmetic gives 35 × 1.0 = 35 against ≈ 126 → **21.7% of global
graph mass**, and because `W` is *row-normalised*, the number that actually governs is the per-row
share: a node with one declared edge and five kNN neighbours gives the declared edge
`1.0 / (1.0 + 5 × 0.36) = 36%` of its outflow; with two declared edges, **53%**. Declared
structure participates. That is the whole point, and it is FORGE's own arithmetic with the
magnitude corrected.

**One site is deliberately withheld — see §4.4.** The hub-penalty PageRank keeps its `type_gain`-
only weighting. Applying "one rule everywhere" there would perturb the only graph mechanism the
benchmark measured to *help* (+0.0286 Hit@1, +0.0362 MRR), and whether it should instead be
weighted by *learned* topology is FORGE's D3 experiment, which is open. Consistency is not worth
an unmeasured change to a measured-good component.

**Rejected: "preserve 'earned' and make declared edges earnable instead."** This was the live
alternative and it is the one the counter-argument points at. It fails on three grounds. (i) It
does not answer *what* would be earned: there is no observable whose accumulation makes "A
inhibits B" more true — co-activation of A and B is *evidence against* the assertion, not for it,
so the Hebbian rule would push an `inhibits` edge in the wrong direction. (ii) It leaves the
system wrong in the interim by construction: a freshly-authored registry — the state every new
user is in, and the state `magicite-bench` measures — would still route as if the author had
declared nothing, which is precisely the "declared structure is invisible until Dream" complaint
all three local workarounds were written to escape. (iii) It is strictly larger: it needs a new
tag type, a new candidate-edge type, a Phase-1/Phase-2 branch per declared type, and an answer to
what a *negative* outcome does to a declared `depends_on`. **The earnable path is not closed** —
§4.2's floor form is exactly what keeps it open, since a potentiated declared edge above the
authored floor already wins today. It is deferred as CF-3, not refused.

---

## 4. The amended rule, normatively

### 4.1 Effective edge weight `S_eff`

```
S_eff(edge) = max(edge.storage_strength, w_authored(edge))

w_authored(edge) = declared_edge_strength   if edge.provenance == 'declared'
                 = 0.0                      if edge.provenance in ('learned', 'derived', 'distilled')

declared_edge_strength: float = 1.0    # magicite.toml, ablation-switchable
```

`edge.storage_strength` remains **the learned (Hebbian) channel and nothing else**: it starts at
0.0 for a declared edge, only Dream may raise it, and only for the types Dream can potentiate.
`wire_declared_edges` (`durable.py:198-208`) is **unchanged** — it still writes the literal 0.0.

`distilled` is `0.0` **by explicit decision, not by omission**: no code path in v1 writes an edge
row with `provenance='distilled'` (the value exists in the DDL CHECK and in
`engram/model.py:22`), and a reserved-but-unused provenance must not silently acquire full
authored weight the day something starts emitting it. If distillation later produces edges, they
enter the authored channel only through the docs/06 approval gate, and that is a separate
amendment.

### 4.2 Properties — all normative, all load-bearing

- **Computed at read, never stored.** No new column, no migration, no checkpoint field, no
  `synapses:` change. Therefore: the durable projection is unchanged (**AC-009/AC-010 untouched**);
  Dream Phase 3 decay, Phase 2 prune (`provenance='learned'` only) and Phase 4 renormalise
  (`WHERE storage_strength > 0`) are unchanged and continue to operate on the learned column
  alone. **An authored assertion can never be decayed, pruned, or renormalised away.** It ends
  when the author deletes the line, not when a scheduler forgets it.
- **Floor, not replacement.** If a declared-provenance edge is ever potentiated above
  `declared_edge_strength`, the learned value wins.
- **Range-preserving.** `S_eff ∈ [0,1]` given `S_edge ∈ [0, w_max=1.0]` and
  `declared_edge_strength ∈ [0,1]`.
- **Exactly revertible.** `declared_edge_strength = 0.0` reproduces pre-amendment behaviour
  bit-for-bit (`max(S, 0) == S`). One config line; bisectable; covered by AC-039.
- **One implementation.** A single pure helper `core/edge_weight.py::effective_strength(...)` —
  framework-free, no DB handle, no `storage.durable`/`engram.writer` import, so `core/router.py`
  may import it without breaching **AC-024**. Every consumer goes through it (**AC-040**).

### 4.3 Why `1.0`, and why that is not a new magic number

`S_eff × type_gain[type]` at `S_eff = 1.0` **is** `type_gain[type]`. The relative weighting *among*
declared edge types is therefore expressed entirely by `type_gain` — the knob that already exists
for exactly that purpose and already carries this spec's values (`composes` 1.0, `depends_on` 1.0,
`co_activation` 0.8, `similar_to` 0.6). Any other value introduces a **second magnitude knob
underneath the first**, with no measurement to set it and two places to look when routing
surprises someone.

And 1.0 is not invented here: it is the convention this codebase **already reached independently
at two of the three workaround sites** when each hit this same zero — the hub PageRank
(`router.py:14-25`, "edge weight = `type_gain` only") and bench baseline (c) (`bench.py:70-78`,
"fixed `type_gain` … never S_edge"). The third (`_COMMUNITY_WEIGHT_FLOOR = 0.1`) chose the right
*form* at an arithmetically inert *magnitude*. This amendment promotes the convention from three
local workarounds to one rule and deletes two of the workarounds.

### 4.4 Call sites — exhaustive, including the ones deliberately excluded

| # | Site | Before | After |
|---|---|---|---|
| 1 | §3.3 step 4 — activation graph (`core/router.py:293-297`) | `W_ij = S_edge × type_gain[type]` | `W_ij = S_eff × type_gain[type]` |
| 2 | §3.3 step 5 — inhibition (`core/activation.py:144-167`, fed by `router.py::_fetch_inhibition_edges`) | `a_i *= (1 − S_edge_ji × inhib_gain)` | `a_i *= (1 − S_eff_ji × inhib_gain)`. **`type_gain['inhibits'] = 0.0` by design** (an `inhibits` edge is never positive diffusion mass), so this pass uses `S_eff` **directly** and never multiplies by `type_gain`. At defaults a declared `inhibits` scales the inhibited node's activation by `1 − 1.0 × 0.7 = 0.3` |
| 3 | §2.6 step 9 — community weights (`core/registry.py:494-517`) | `max(S_edge, _COMMUNITY_WEIGHT_FLOOR = 0.1)` | `S_eff`; **`_COMMUNITY_WEIGHT_FLOOR` is deleted** — it was this defect's first local workaround and two competing floors is a maintenance trap. Low risk: the community rerank is measured inert (Δ Hit@1 = 0.0000, Δ Hit@3 = 0.0000 with 5 real communities) |
| 4 | §3.3 step 9 — cycle-break (`core/composition.py:129-139`) | "break the weakest edge", which degenerated to "break whichever edge dict iteration hits first" — every candidate tied at 0.0 | Break the edge minimal under the **total order** `(S_eff, dep_name, dependent_name)`. Under the amendment declared plan edges tie at 1.0, so the tiebreak is what makes the plan reproducible, not the strength (**AC-042**) |
| 5 | §3.3 step 10 — `plan_confidence` (`core/composition.py:159-189`) | `mean(S_edge over plan edges) × (resolved/declared)` | Redefined structurally — **§5** |
| 6 | `introspect`/`inspect` edge rows (`storage/queries.py:61-71`) | reports `storage_strength: 0.0` for every authored edge, forever | reports **both** `storage_strength` (learned channel, still 0.0) **and** a new `effective_strength` field. Additive field on one R0 read-only tool; **no tool is added, removed or renamed — AC-003's 16 and INV-4 are untouched** (**AC-041**) |
| 7 | `eval/bench.py:70-78` baseline (c) | fixed `type_gain`, never `S_edge` | the same shared helper **with the learned channel suppressed**: `max(w_authored(edge), S_edge if provenance == 'derived' else 0.0) × type_gain`. Keeps docs/07's "no learned weights" true for (c) while giving kNN edges their cosine instead of a flat gain. **Changes (c)'s published numbers — they must be re-measured and re-published, see §8** |
| — | **§3.3 step 7 — hub-penalty structural PageRank (`core/router.py:14-25`, `:324-334`) — NOT CHANGED** | `type_gain` only | `type_gain` only. Its docstring's stated reason ("S_edge would make it permanently inert") is now obsolete, and its **restated** reason is that it is deliberately a *structural* centrality metric. It is the one graph mechanism measured to help (**+0.0286 Hit@1, +0.0362 MRR**); whether it should be *learned-topology* weighted is FORGE's D3 (open). Changing it here would be an unmeasured change to a measured-good path |
| — | Dream Phases 2/3/4, `_build_synapses`, `obs/kpi.py` — **NOT CHANGED** | — | They read the learned column, which is exactly what they should read (§4.2) |

---

## 5. `plan_confidence` — the same category error, one layer up

**The defect is not only that the number is 0.0.** `PLAN_EDGE_TYPES` is
`('depends_on', 'composes')`; both are always `provenance='declared'`; Dream potentiates only
`co_activation`. So `mean(S_edge over plan edges)` is a **structural constant** under every
possible future in which Dream's tagging is unchanged — 0.0 today, and exactly 1.0 the moment
§4.1 lands. **A factor that can only ever be a constant carries no information in either
regime.** Simply letting §4.1 float it to 1.0 would make step 10 satisfiable while leaving a dead
term in the formula and leaving `plan_confidence` blind to the three ways a plan is actually
untrustworthy.

The original formula conflated *"is this plan complete?"* with *"are these edges strong?"* — the
same conflation as §3, one layer up. **Plan confidence is a statement about the plan's structural
completeness, not about Hebbian strength.** Amended §3.3 step 10:

```
10. plan_confidence — structural satisfaction, never Hebbian:
      E     = every declared depends_on/composes edge whose src is a node in the emitted plan
              (the bounded closure), dangling targets INCLUDED
      E_sat = { e in E : e resolves to an engram (dangling = 0 AND dst_id IS NOT NULL)
                         AND e.target is present in `order`
                         AND `order` respects e (index(target) < index(src)) }
      plan_confidence = round(|E_sat| / |E|, 4)   if |E| > 0
                      = 1.0                        if |E| == 0
```

All three failure modes fall out of one rule with **no additional constants**: a dangling target
fails clause 1; a target cut by `plan_max_depth`/`plan_max_size` fails clause 2; an edge dropped
by cycle-breaking fails clause 3. It is 1.0 exactly when the plan is complete, acyclic and fully
resolved, 0.0 when nothing the winner declared could be honoured, and monotone in between.

**One deliberate behaviour change to flag:** the emitted implementation short-circuits
`len(plan.order) <= 1 → 1.0`. Under the amended rule a lone winner with two unresolvable `needs`
reports **0.0**, not 1.0. That is the honest answer — the client is being told "here is a skill
whose declared prerequisites do not exist in this registry" — and it is exactly the case the old
short-circuit hid. AC-038 pins it.

---

## 6. New acceptance criteria — and the coverage defect in the frozen set

### 6.1 AC-023 is provenance-underspecified in its GIVEN

Recorded here as a **coverage defect in the frozen criteria, not a checker failure and not a
test-fidelity failure.** AC-023 reads:

> GIVEN an engram whose `inhibits` edge targets a competitor engram / WHEN both are activated by
> a query / THEN the inhibited engram's score SHALL be strictly lower than without the inhibition
> edge

It says **nothing about provenance or about how that edge came to exist**.
`tests/unit/core/test_router.py:80` inserts an `inhibits` row with `storage_strength = 0.8` by
direct SQL and the assertion then holds. **The test proves the criterion exactly as written.**
Kupo's 33/33 is not impeached: the criterion was satisfied. What the criterion never required is
that the state it describes be **reachable by any production path** — and it is not. The fault is
in the GIVEN, and the remedy is a new criterion whose GIVEN names `register()`, which is
**AC-034**.

`ramza-freeze` exists precisely so that this cannot be quietly repaired by editing AC-023.
It is not edited. `acceptance-criteria.md` is byte-identical at
`7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`, `amendments[]` stays empty,
`criteria_sha256` is untouched, and the file was **not** re-frozen.

### 6.2 The addendum

Nine new criteria, **AC-034 … AC-042**, live in
`.spectra/changes/magicite-v1-implementation/acceptance-criteria-addendum.md` — a separate,
clearly-marked file outside the frozen anchor. They are linted by `ramza-ears-lint` and their
tamper anchor is their own `sha256` recorded in `spec.yaml artifacts[]`; they are deliberately
**not** run through `ramza-freeze`, because that tool writes `plan-state.json criteria_sha256`,
which must keep pointing at the frozen 33.

| ID | Form | What it pins | Why the frozen set could not |
|---|---|---|---|
| **AC-034** | event-driven | **Its GIVEN names the `register()` ingestion path**: a registry ingested only through `register()` from `.egr.md` frontmatter `inhibits:` — inhibition must strictly lower the inhibited engram's score versus the same run at `declared_edge_strength = 0.0` | AC-023's GIVEN is silent on provenance, so it is satisfiable by direct SQL |
| **AC-035** | event-driven | a declared `needs:` edge is **present in the activation graph** at `declared_edge_strength × type_gain['depends_on']` | nothing asserted graph membership; `build_graph` dropped them silently |
| **AC-036** | state-driven | `edge.storage_strength` for a never-potentiated declared edge **stays 0.0** — the guard that keeps §4.2 true and AC-009 unmoved | — |
| **AC-037** | event-driven | `plan_confidence == 1.0` for a fully-resolved multi-node plan | nothing ever asserted a `plan_confidence` value |
| **AC-038** | event-driven | `plan_confidence == 0.5` for one-of-two resolved | — |
| **AC-039** | unwanted-behavior | `declared_edge_strength = 0.0` is an **exact revert** | — |
| **AC-040** | ubiquitous | **no module computes an edge routing weight from `edge.storage_strength` outside `core.edge_weight`** — an AST guard of the same shape as AC-024, so the three-workarounds pattern cannot recur | this is the criterion whose absence let the defect ship |
| **AC-041** | event-driven | `introspect` reports `effective_strength` | `inspect` reported 0.0 forever |
| **AC-042** | event-driven | cycle-breaking is **deterministic** across two expansions | ties at 0.0 made the old rule iteration-order-dependent |

---

## 7. Two evidence-driven default changes

Both come from `scale-benchmark.md` §6 — a one-`Config`-field-at-a-time sweep on the same 70-engram
corpus and the same 210 pre-registered queries. FORGE rates this **the strongest evidence in the
whole report (E-2, reliability H)**: authorship bias is *identical across arms*, so it cancels
exactly.

### 7.1 `ppr_restart: 0.15 → 0.85` — measured

| variant | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| (d) as shipped (`ppr_restart = 0.15`) | 0.4619 | 0.7000 | 0.5913 |
| **(d) `ppr_restart = 0.85`** | **0.5476** | **0.7476** | 0.6398 |
| (d) `w_activation = 0, w_similarity = 0.75` | 0.5524 | 0.7429 | 0.6414 |
| (b) dense embedding, reference | 0.5476 | 0.7429 | 0.6667 |

At 0.85, Hit@1 **matches the embedding baseline exactly** (0.5476) and Hit@3 is **above** it
(0.7476 vs 0.7429). Diagnosis, also measured: at `ppr_restart = 0.15`, **85% of activation mass
diffuses along the 350 derived `similar_to` kNN edges**; mean weighted spread `w_similarity·cosine`
= 0.0630 vs `w_activation·PPR` = 0.0233, so the PPR term contributed 37% as much ranking spread as
similarity — and that spread reflects **graph neighbourhood mass, not query match**. Harmless at 7
nodes; smearing at 70.

**Why not `w_activation = 0`, which measures 0.0048 Hit@1 better.** Because it would **break a
frozen acceptance criterion to buy one query in 210.** Inhibition acts *only* on the activation
vector (`core/activation.py:144-167`, applied at `router.py:304` before scoring). Zero the
activation weight and the inhibition pass has arithmetically zero effect on any score, so
AC-023's *"the inhibited engram's score SHALL be strictly lower"* becomes **unprovable** — not
failing, *unprovable*. It would also turn 45% of the score into a constant zero, i.e. ship
"spreading activation" that spreads nothing, and it would kill the hub penalty
(the one graph mechanism measured to help) and the community rerank along with it. 0.0048 Hit@1
is noise; a frozen criterion is the tamper-evidence anchor. `ppr_restart` is additionally a single
scalar with a clear physical meaning that a larger registry can sweep, and it leaves **every graph
path live**.

**Interaction with §4, stated so it is not discovered later.** These two changes push in opposite
directions on purpose: §4 puts declared structure *into* the graph for the first time, and 0.85
*damps how far anything travels through it*. That is deliberate — FORGE's "damp, don't amputate."
The measurement at 0.85 was taken on a graph in which declared edges were still inert, so **the
0.5476 figure is a measurement of the old graph at the new restart**, and it does not transfer
unchanged. This is exactly why §8 makes re-running the cold bench a release obligation rather
than a suggestion.

### 7.2 `w_retrieval: 0.15 → 0.05`, `w_similarity: 0.30 → 0.40` — **precautionary pending further experiment**

Labelled precautionary in `magicite.toml`, in the spec, and here. **It is not a measured optimum
and must not be published as one.**

The justification is an **evidence-balance asymmetry**, not a new magic number: `w_retrieval =
0.15` has **one strong measurement against it and zero measurements ever for it.** Against it —
under an *oracle* teacher and a **matched** train/test distribution, held-out Hit@1 fell
**0.4697 → 0.1061** (a 3.6× collapse); it fails on its own training split too (0.4583 → 0.2847),
so it does not even memorise its supervision; and the target-only variant (0 learned edges, R and
S only) still collapses to 0.1818, so composition-plan credit assignment is not the cause. The
mechanism is measured: `w_similarity · cosine` spread = 0.0561 against `w_retrieval · R` spread =
**0.0354 — 63% of the entire query-conditioned signal's amplitude**, injected by a pure
usage-frequency prior with **zero query conditioning**, into a router with no mechanism to
attenuate it when it is uninformative.

**The honest limitation, carried:** that workload is uniform (exactly 3 queries per engram), which
makes a popularity prior *maximally* uninformative; under a skewed real workload `R` would carry
genuine signal. That is why this is precautionary and reversible, not a retirement of `R`. The
downside it guards against is catastrophic; the upside it forfeits is unmeasured; the change is
one config line. `w_activation` is left at 0.45 and the 0.10 lost from `w_retrieval` is given to
`w_similarity`, keeping the four weights summing to 1.00.

**Reversal condition (FORGE D1/D2):** if component normalisation (rank- or z-scoring each of the
four components before the weighted sum) recovers cold-level held-out Hit@1 at `w_retrieval =
0.15`, then scale-mixing was the flaw and this change should be reverted rather than kept. If a
matched-and-stratified Zipf skew sweep shows the learned−cold delta rising monotonically with `s`
and crossing zero at moderate skew, `w_retrieval` should be restored on evidence. Neither
experiment has been run.

### 7.3 Both defaults ship **with the measurement that produced them**

`magicite.toml` comments and the spec carry the number, the corpus, and the date. A default that
changed because of evidence carries the evidence; otherwise the next reader has a magic number
and no way to challenge it.

---

## 8. Measurement obligations this amendment creates

Recorded as obligations with owners, not as suggestions. None is a new validation gate — the nine
VG commands are unchanged (§10) — because the 70-engram / 210-query corpus is session-local and a
VG must be reproducible for Kupo.

| ID | Obligation | Owner |
|---|---|---|
| **MO-1** | Re-run the 210-query **cold** bench after §4 and §7 land and **publish** the new (b)/(c)/(d) numbers. §7.1's 0.5476 was measured with declared edges inert; §4 changes the graph underneath it. A default that ships on an obsolete measurement is the failure mode this record exists to correct | Vivi (config) + Kupo (verify) |
| **MO-2** | Baseline (c)'s published numbers move (§4.4 row 7). Re-publish or explicitly mark the old ones superseded | Vivi |
| **MO-3** | Inhibition is live for the first time — 11 declared `inhibits` relations in the 70-engram registry that have **never had any effect** now scale their targets' activation by 0.3. Report the Δ separately from §7.1's, so the two are not conflated | Vivi |

---

## 9. What changed in the plan artifacts

| Artifact | Change |
|---|---|
| `decisions/DECLARED-EDGES-AMENDED.md` | **new** — this record |
| `acceptance-criteria-addendum.md` | **new** — AC-034 … AC-042, `ramza-ears-lint` clean, **not** frozen, **not** part of the 33 |
| `spec.md` | located patches: the reading-contract errata banner; §2.2 `edge` DDL comment on `storage_strength`; §2.6 step 4 note; §3.3 route steps 4/5/6/9/10 (superseded values struck in-comment, never deleted); the new **§3.3.1 Effective edge weight** block; the §3.3 `introspect` note; §4.3 note that the authored channel is not decayed or renormalised; the Acceptance-Criteria addendum pointer + the AC-023 coverage note; §Risks R10 (fired) and new **R12** |
| `spec.yaml` | third `errata:` entry; `acceptance_criteria.addendum`; `risks[R10].risk` + new `risks[R12]`; refreshed `artifacts[]` hashes + two new `artifacts[]` entries |
| `spec.envelope.json` | ECL integrity tag re-stamped to the new `spec.md` hash; **third** `x_ramza_amendment` entry appended (§10.1) |
| `plan-state.json` | one appended `errata[]` entry; `amendments[]` and `criteria_sha256` untouched |
| `acceptance-criteria.md` | **untouched, byte-identical** — the tamper anchor |
| `src/`, `tests/`, `docs/`, `change.json` | **not touched.** RAMZA plans; Vivi implements. `change.json status` is not RAMZA's to move |

### 9.1 `spec.md` hash transition

```
old sha256  57148a2de70fa723b65cddbce2b57de98327a7ba44b272415101a9263aa79c17   (94679 bytes)
new sha256  e9efab604467129246e0b70fb25ed70a576bc4fee5934a243881e2151101dd8b   (119819 bytes)
```

Full chain, now four links: `92372ad6…` → `9fca1c08…` (A1-REVISED) → `57148a2d…` (R1-RESTATED)
→ `e9efab60…` (this record).

`spec.yaml artifacts[0].sha256` was updated. `artifacts[1]` (`acceptance-criteria.md`,
`7bd3d184…`) was left exactly as emitted and re-verified against the file on disk. `artifacts[]`
entries for `plan-state.json` and `spec.envelope.json` were refreshed because this amendment moves
them; `decisions/A1-REVISED.md`, `decisions/R1-RESTATED.md` (both append-only, untouched) and
`ramza-calibration.jsonl` keep their recorded hashes, re-verified on disk. Two new `artifacts[]`
entries were added, for this record and for the addendum.

---

## 10. The ECL envelope re-stamp

Identical mechanism and identical reasoning to A1-REVISED §5 and R1-RESTATED §8, applied a third
time. `spec.envelope.json` carries an ECL v2.0 integrity tag over `spec.md`'s bytes; patching
`spec.md` necessarily invalidates it, and leaving it stale would make every future
`ramza-verify-emit` report a **false** tamper alarm on an authorized, adjudicated, recorded
change — and tamper signals that are known-broken stop being read. Tamper evidence exists to
detect **unrecorded** change; a recorded amendment with a preserved hash chain extends the audit
chain rather than breaking it.

`ramza-freeze --amend` was again **deliberately not used**: it amends `plan-state.json
criteria_sha256`, i.e. the `acceptance-criteria.md` anchor, which must not move. The re-stamp is a
targeted rewrite of `artifact.sha256`, `artifact.size_bytes` and `integrity.value` plus a **third**
appended `x_ramza_amendment` entry (the first two are left intact, so the envelope carries the
whole chain), verified afterwards by the canonical emission gate `ramza-verify-emit --spec
spec.md --envelope spec.envelope.json`, which recomputes the digest over the payload bytes and
fails on any mismatch. `objective`, `context_delta.summary`, `trace.ts` and
`x_ramza_acceptance_criteria` are left as sent — retroactively editing prose in a delivered ECL
message would be rewriting history rather than amending it.

---

## 11. Deliberately not changed

- **`acceptance-criteria.md`** — byte-identical, sha256
  `7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`, verified mechanically before
  and after. Not re-frozen, not amended; `amendments[]` stays empty and `criteria_sha256` is
  untouched. **AC-023 is not edited** — its coverage defect is recorded (§6.1) and answered by a
  new criterion, which is the only honest way to fix a GIVEN that is too weak.
- **The 16-tool surface**, INV-1…INV-4, the P0 guard mechanism (G1/G2/G3 + the tier gate), the
  Tier A/B/C split, all nine VG commands, the M0–M7 decomposition, and all eight §9 CR
  resolutions. `introspect` gains one **additive output field** (§4.4 row 6); no tool is added,
  removed, renamed or re-signatured, so AC-003 and INV-4 are untouched.
- **`edge.storage_strength` semantics, `wire_declared_edges`, the DDL, and every migration.**
  There is exactly one migration and this amendment does not add a second.
- **Dream** — Phases 1–7, the spacing gate, prune, decay, renormalise, `_build_synapses`, and the
  `theta_*` constants.
- **The hub-penalty PageRank** (§4.4, last-but-one row) — restated, not rewired.
- **`w_activation = 0.45`, `w_excitability = 0.10`, `type_gain`, `inhib_gain = 0.7`,
  `hub_penalty = 0.15`** and every other routing constant not named in §7.
- **The confidence score (84.75 → VALIDATE), the complexity score (10/12), the explore scores, the
  refine-cycle count, the critic record, and the phase walk.** Not re-run and not re-scored, per
  the same discipline as the two prior errata. This is a located amendment to a shipped spec, not
  a re-plan.
- **`docs/`** — outside RAMZA's write boundary. The corrections FORGE §6 mandates to
  `docs/01-vision-and-hypotheses.md` and `docs/07-evaluation-and-observability.md` (the Hypothesis
  Register results column, the H-COMPOSE/Plan-F1 correction, the "no offline benchmark can
  exercise learning without faking the clock" entry) are **not** made here and are routed to IDG
  as **CF-1**.
- **`src/` and `tests/`** — no source file is touched by this record.

---

## 12. Effect on the `verified` status

The change was already escalated from `verified` to `in_progress` by a drift check before this
record was written, so there is no status to protect — but the reasoning is recorded for the
re-verifier.

1. **The measurement anchor did not move.** Kupo verified against `acceptance-criteria.md` at
   `7bd3d184…`; that file is byte-identical.
2. **This amendment, unlike the two before it, does change executable behaviour.** It is not a
   restatement. AC-023 will still pass (its GIVEN is satisfied by the direct-SQL fixture as
   before, and now also by a production path), but **`plan_confidence` values change**, declared
   edges enter the activation graph, inhibition becomes live, communities re-cluster, and two
   routing defaults move. **Every VG must be re-run after Vivi implements**, and AC-034…AC-042 must
   be attested alongside AC-001…AC-033.
3. **AC-009/AC-010 are specifically protected** by §4.2 (read-time computation, nothing stored) and
   pinned by AC-036 — the rebuild invariant must not be collateral damage of this fix.
4. The one thing a re-verifier should re-check independently is the **hash chain**, which moved by
   design: `spec.md` `57148a2d… → e9efab60…`, mirrored in `spec.yaml artifacts[]`,
   `spec.envelope.json` (`artifact.sha256` / `integrity.value` / `x_ramza_amendment[2]`), and
   `plan-state.json errata[2]`.

---

## 13. Carry-forwards

| ID | Item | Owner |
|---|---|---|
| **CF-1** | **FORGE §6's documentation corrections are non-negotiable and are not made here.** docs/01's Hypothesis Register needs a Results column and an H-BODY split (H-BODY-a SUPPORTED in direction, registered ≥20pp **not** demonstrated at +14.3pp; H-BODY-b FALSIFIED as implemented); H-COMPOSE must be marked **UNTESTED** with Plan F1 disclosed as a monotone re-encoding of Hit@1; docs/07 needs the "no offline benchmark can exercise learning without faking the clock" entry. `docs/` is outside RAMZA's write boundary | IDG |
| **CF-2** | **`synapses:` has no reader** (ATLAS VERDICT-1, confirmed by execution): learned edge weights do **not** survive `rm skill-graph.db && sync()`, and the *next* Dream checkpoint then rewrites the file's `synapses:` block to `[]`, making the loss irrecoverable. `spec.md:450-451` §2.6 step 4 requires *"and the `synapses:` block (provenance from the file)"* — only the first half is implemented. This is a **spec-conformance defect** (AC-009 under its own GIVEN), adjacent to but distinct from this amendment, and it is **not** fixed here. Every day of delay is unrecoverable weight | Vivi (implement §2.6 step 4's second half) + RAMZA (if the AC-009 GIVEN is instead to be narrowed) |
| **CF-3** | **The earnable-declared-edge path stays open** (§3, rejected alternative). If a declared type ever becomes taggable, the §4.1 floor form already lets learning exceed the authored value with no further spec change; what would need deciding is what a *negative* outcome means for an authored assertion | RAMZA (future cycle) |
| **CF-4** | **FORGE's D1/D2/D3** — normalisation isolation, matched+stratified Zipf skew sweep, learned-topology hub penalty. D1/D2 gate §7.2's reversal; D3 gates whether the hub PageRank (§4.4) should be re-weighted. All three reuse existing `scalebench/` artifacts | Vivi / ATLAS |
| **CF-5** | **The uncalibrated combination rule** (FORGE §3, argument 1): `route()` linearly sums four raw, un-normalised, differently-scaled quantities at fixed weights, so each term's actual ranking influence is set by the accidental dynamic range of whatever embedder is plugged in. §7.2 damps the worst symptom; it does not fix the cause, and every conclusion here is conditional on `bge-small-en-v1.5` | RAMZA (next planning cycle) |
| **CF-6** | Inherited and still open from R1-RESTATED: **CF-4** (FORGE's reversal conditions; the M7 privilege-boundary check is due) and **CF-5** (Fix-B server-only session minting) | human / RAMZA |
