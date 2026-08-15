---
eidolon: ramza
kind: decision
id: R12-FIRED
version: 1.0.0
created_at: 2026-08-15
change_id: magicite-v1-implementation
supersedes: "nothing. DECLARED-EDGES-AMENDED is NOT superseded: S_eff = max(storage_strength, w_authored), the declared_edge_strength = 1.0 default, w_authored, every call site and every acceptance criterion are unchanged. This record supersedes only the ppr_restart row's published figures and baseline (c)'s published numbers, both of which that record itself obliged to be re-measured (MO-1, MO-2)."
status: recorded
disposition: "measurement record + default CONFIRMED, not changed. R12 fires, is narrowed, and stays OPEN with four pre-registered reversal conditions. No rule change, no acceptance-criteria change, no re-plan, no re-scoring, no phase re-walk."
authorizing_trigger: "the release obligation written into DECLARED-EDGES-AMENDED §8 (MO-1/MO-2/MO-3) and spec.md §3.3.1, discharged by the orchestrator's re-run at commit 2d25abb"
evidence_path: "scratchpad/r12-remeasure.md (session-local; every load-bearing number is reproduced below so this record stands alone). Corpus: 70 engrams / 210 lexically-independent pre-registered queries, cold registry, real fastembed BAAI/bge-small-en-v1.5, nothing re-authored — the same corpus and queries as the original falsification run."
implementing_commit: "2d25abb (Vivi) — 476 tests, 94.23% coverage, AC-034…AC-042 pass, all 33 frozen criteria pass"
recorded_by: ramza
appendonly: true
---

# R12-FIRED — the knob is confirmed; the channel the delta came from was never isolated

> **Append-only.** Written once. If `declared_edge_strength` is later changed, write
> `R12-FIRED-2.md` and mark this record superseded; do not edit it in place.

---

## 1. What R12 said, and what fired

R12 (added by `DECLARED-EDGES-AMENDED`, 2026-08-15) recorded that `ppr_restart = 0.85` had been
measured on the **old, kNN-only** graph — one in which declared edges carried zero mass — and that
the amendment changed that graph underneath it. Its mitigation was a **release obligation**, not a
suggestion: re-run the cold 210-query bench after implementation and **publish** the new (b)/(c)/(d)
numbers, with the inhibition delta reported **separately** so the edge-weight change and the
`ppr_restart` change are not conflated (MO-1, MO-2, MO-3).

The mitigation **worked as designed**: the obligation forced the re-measurement *before* release
rather than after, and it produced a result that the amendment's authors did not expect. That is
the mitigation succeeding, exactly as R10's falsification was.

## 2. The measurement, published

Run at commit `2d25abb`. Hit@1, same corpus, same 210 queries, cold registry, real embeddings:

| configuration | (a) lexical | (b) dense | (c) emb+graph | (d) full |
|---|---|---|---|---|
| **amended defaults** — `declared_edge_strength = 1.0`, `ppr_restart = 0.85` | 0.4048 | 0.5476 | 0.5286 | **0.5190** |
| `declared_edge_strength = 0.0`, `ppr_restart = 0.85` | 0.4048 | 0.5476 | 0.5286 | **0.5476** |
| `declared_edge_strength = 0.0`, `ppr_restart = 0.15` — pre-amendment | 0.4048 | 0.5476 | 0.4333 | 0.4905 |

Pre-amendment reference (old weights too): a 0.4048, b 0.5476, c 0.4571, d 0.4619.

All twelve figures are integer-consistent at n = 210 (0.4048 = 85, 0.5476 = 115, 0.5286 = 111,
0.5190 = 109, 0.4905 = 103, 0.4333 = 91), which is a cheap sanity check that they come from a real
210-query run and not from a summary.

**MO-1 discharged** (numbers published, here and in spec.md §3.3.1). **MO-2 discharged** (baseline
(c)'s figures are re-published above and supersede the earlier ones). **MO-3 NOT discharged** — §4.

## 3. `ppr_restart = 0.85` — CONFIRMED on the new graph shape

This is the thing R12 actually asked to be checked, and it survives cleanly. Holding
`declared_edge_strength = 0.0` and moving only the restart, rows 3 → 2 give **(c) 0.4333 → 0.5286**
and **(d) 0.4905 → 0.5476**. The recovery of both graph baselines is attributable to the restart
alone, on a graph whose shape has changed. R12's worry — that the 0.5476 figure was an artefact of
the old kNN-only graph and would not transfer — **does not materialise**.

Note the direction of the confirmation: 0.85 is *more* damping, i.e. less diffusion. The amendment
predicted these two changes push in opposite directions on purpose ("damp, don't amputate"), and
the measurement is consistent with the damping being the load-bearing half.

## 4. The finding the run did NOT deliver — and it is the one that matters

**MO-3 asked for exactly the isolation that would have made this decidable, and it was not run.**
The re-measurement varied `declared_edge_strength`, which moves **three** semantically distinct
channels simultaneously:

| channel | call site (spec §3.3.1 table) | sensitive to the knob? |
|---|---|---|
| diffusion mass in the activation graph | rows 1 and 3 | yes |
| inhibition magnitude (`a_i *= 1 − S_eff × inhib_gain`) | row 2 | yes |
| community structure (`_compute_communities` weights) | row 3 | yes |
| composition-plan expansion, `plan_confidence`, cycle-break order | rows 4 and 5 | **no** — structural since §3.3 step 10 was redefined; verified in `core/composition.py::_closure`, which walks declared edges regardless of weight |

**But the run contains an accidental isolation that is decisive**, and it was not read out.

Baseline **(c)** carries declared `composes`/`depends_on` into the diffusion graph at **full
authored mass** — `eval/bench.py:198-208` builds its graph from `_GRAPH_EDGE_TYPES = (co_activation,
composes, depends_on, similar_to)` weighted by
`effective_strength_no_learned(..., cfg.declared_edge_strength) × type_gain` — and it carries **no
inhibition** (`inhibits` is not in `_GRAPH_EDGE_TYPES` and there is no `apply_inhibition` call in
`_baseline_c_rank`), **no community rerank, no hub penalty, no `R`, no excitability**. It is
`0.6·PPR + 0.4·cosine` and nothing else.

**(c) is 0.5286 in both arms — 111 of 210, identical.**

So: **putting full declared-edge mass into the activation graph changed zero of 210 top-1 answers.**
The −6 in (d) cannot be attributed to rows 1 or 3's diffusion effect, and must arise in a channel
present in (d) and absent from (c): **inhibition** (row 2) or the **community re-clustering** (row 3
at sync time, feeding route step 8's rerank). The run separated neither — which is precisely what
MO-3 was written to obtain.

Two readings of (c)'s invariance, both actionable, and I cannot distinguish them without re-running
(the 70-engram corpus is session-local and not in-repo, and re-running the bench is not RAMZA's
role):

1. **A true null** — declared mass genuinely does not change (c)'s top-1 on this corpus. This is
   the reading I take forward, and it is a *negative product finding*, recorded as such in §6.
2. **A harness artefact** — the (c) arm did not pick up the config override. `_baseline_c_rank`
   reads `cfg.declared_edge_strength` from the same `Config` the router reads, so a single mutated
   `Config` would propagate to both; but if the arms were configured separately this is possible.
   **Whoever runs RC-1 should confirm (c) responds to the knob at all before concluding anything
   from its invariance.** This is stated as a genuine caveat on my own key argument, not buried.

### 4.1 The uncalibrated interaction the isolation points at

`inhib_gain = 0.7` **predates this amendment** and is explicitly listed among the constants
DECLARED-EDGES-AMENDED §11 left unchanged. It was set when an `inhibits` edge's `S` was expected to
be a **learned** value distributed over [0,1]. Call-site row 2 pins `S` to the **top** of that range
for **every** authored `inhibits` edge — and, unlike every other call site, uses `S_eff` *directly*
without multiplying by `type_gain` (because `type_gain['inhibits'] = 0.0` by design). The shipped
multiplier is therefore `1 − 1.0 × 0.7 = 0.3`: **a 70% cut of the inhibited node's activation from
one unweighted line of author YAML.**

`inhib_gain` **has never been calibrated for `S = 1.0`.** With 11 declared `inhibits` relations in
the 70-engram registry firing for the first time, this is the most plausible mechanism for a
six-query regression, and it has its own dedicated magnitude knob.

**It is deliberately not touched here.** Moving `inhib_gain` on an unisolated six-query delta would
be the identical methodological error at a different address. It is RC-1.

## 5. The decision — `declared_edge_strength` ships at `1.0`

### 5.1 The symmetry argument, taken seriously and answered

The challenge is fair and it is the right challenge: `w_retrieval` was moved 0.15 → 0.05 on an
**evidence-balance asymmetry** — "one strong measurement against it and zero for it" — and
`declared_edge_strength = 1.0` now has **one weak measurement against it and none for it**. If the
principle is real it should bite its author.

It does bite, and the answer is that **the principle as I actually stated it has three qualifiers,
and this case fails all three.** From `DECLARED-EDGES-AMENDED` §7.2, verbatim: *"The downside it
guards against is catastrophic; the upside it forfeits is unmeasured; the change is one config
line."*

| | `w_retrieval` 0.15 → 0.05 | `declared_edge_strength` 1.0 → 0.0 |
|---|---|---|
| **effect size** | held-out Hit@1 **0.4697 → 0.1061**, a 3.6× collapse; fails on its own training split too (0.4583 → 0.2847) | **0.5476 → 0.5190**, six queries in 210; still far above lexical (0.4048) and above the as-shipped 0.4619 |
| **mechanism identified?** | **yes, measured**: `w_retrieval·R` spread = 63% of the query-conditioned signal's amplitude, from a prior with *zero* query conditioning | **no.** The channel this section is about measured **exactly inert** (c: 0/210). The responsible channel was never isolated |
| **what reversal forfeits** | nothing structural — `R` still contributes; no criterion depends on its magnitude | **three shipped behaviours and one acceptance criterion** — §5.3 |
| **statistical standing** | p not needed; a 3.6× collapse under an *oracle* teacher on matched distributions | **no paired test run**, and the *ceiling* on one is p = 0.031 — §5.2 |

The invitation in the brief was explicit: *"including, if you judge it right, concluding that a weak
measurement warrants less than a strong one did."* That is the honest reading. **A 3.6× collapse
justified a precautionary default change. A six-query delta justifies a published measurement, a
narrowed risk, and a pre-registered reversal condition — not a default change.** Treating the two as
equivalent because both are "one measurement against, none for" would be applying the *slogan* of
the principle while discarding its content.

### 5.2 The measurement is at the edge of what a paired test could ever certify

The delta is a net swing of **6** in 210. Under an exact-binomial McNemar test, the *most
favourable possible* discordance pattern — all six pairs running one way, `n01 = 6, n10 = 0` —
gives `p = 2 × 0.5^6 = 0.031`. With as few as four discordances the other way (`n01 = 10,
n10 = 4`, `n = 14`), `p = 2 × P(X ≤ 4) = 0.180`. The b−d gap this spec previously acted on was 18
queries at p = 0.00053.

So the honest characterisation is not "probably not significant" — it is **"marginal in the best
case and non-significant under any realistic discordance pattern."** Acting on it would be acting
below the evidentiary bar this spec has used for every other default it moved.

### 5.3 The cost of being wrong is asymmetric in the *opposite* direction

At `declared_edge_strength = 0.0` as a **shipped default** (as opposed to as an ablation switch),
three of the four defects §3.3.1 exists to fix come back, and one addendum criterion becomes false:

1. **Inhibition is a numeric no-op again** (`a_i *= 1 − 0.0 × 0.7 = 1`), so **AC-023 is unreachable
   in production again** — the exact state FORGE ruled a category error. AC-034 would still *pass*,
   but only because `tests/acceptance/test_declared_edge_weight.py:33` pins
   `cfg.declared_edge_strength = 1.0` explicitly; the criterion would then be attesting a
   non-default configuration.
2. **AC-035's THEN becomes false as written.** Its raw weight is
   `declared_edge_strength × type_gain['depends_on']`; at 0.0 that is 0.0, `build_graph` drops
   `w <= 0`, and the edge is **absent** from the graph rather than present in it. The proving unit
   (`tests/unit/core/test_edge_weight.py:88`, `assert raw_weight > 0`) reads `cfg.declared_edge_strength`
   and would **fail at the shipped default**. Shipping 0.0 therefore is *not* a free config change:
   it requires restating AC-035's GIVEN to name a non-zero strength **and** a corresponding test
   change in `tests/` — outside RAMZA's write boundary, and a re-verification event.
3. **Community structure loses declared edges outright**, which is **worse than pre-amendment**:
   `_COMMUNITY_WEIGHT_FLOOR = 0.1` was deleted by this amendment precisely because `S_eff`
   subsumed it, so 0.0 is not a clean revert to the prior state — it is the prior state *minus*
   the floor. (Practical impact is likely nil — the community rerank measured ΔHit@1 = 0.0000 —
   but the semantic regression is real and would be undocumented.)
4. Only **`plan_confidence` survives** a 0.0 default, and only because §3.3 step 10 was redefined
   **structurally** (`|E_sat| / |E|`) rather than left strength-weighted. `core/composition.py`
   confirms it: `_closure` walks declared edges regardless of `S_eff`, and `S_eff` enters only as
   the cycle-break sort key, where the `(S_eff, dep_name, dependent_name)` total order stays
   deterministic even when every candidate ties at 0.0 (**AC-042 holds either way**).

**AC-039 remains satisfiable at any shipped default**, as required: its proving unit sets
`cfg.declared_edge_strength = 0.0` explicitly (`tests/unit/core/test_edge_weight.py:120`), so the
exact-revert guarantee is independent of what the default is. That guarantee is what makes every
reversal condition below a config line rather than a code change.

### 5.4 What this corpus can and cannot register — stated as a limit, not used as a shield

The 210 queries are **single-target** retrieval queries: three per engram, one gold answer each.
Diffusion along a *correct* `needs`/`composes` edge moves activation from the target toward its
dependencies — which are, by construction, **not** the gold answer. On a single-target Hit@1 metric,
a **correct** composition edge can only be neutral or harmful. And FORGE's own ruling stands that
**H-COMPOSE is UNTESTED** — zero compositional queries have ever been run, and Plan F1 as
implemented is a monotone re-encoding of Hit@1.

This is a genuine argument, and it is also the kind of argument that becomes unfalsifiable if left
as prose. So it is **converted into RC-3, with a decision rule attached**: if declared mass does not
improve a composition-sensitive metric on a compositional query set either, the diffusion channel
has no measured benefit **on any metric** and ships off by default regardless of RC-1's outcome.

The corpus's other limit — that its `needs`/`composes`/`inhibits` relations were written by the same
agent that wrote the queries, so a null may be measuring poor input rather than the design — is
RC-4. It cuts toward not over-reacting, and it is also the only corpus that exists, which is why it
is a reversal condition rather than a reason to disregard the number.

### 5.5 Options considered and rejected

| option | why rejected |
|---|---|
| **lower to an intermediate value** (0.3, 0.5) | No point between the endpoints has been measured; both endpoints have. Interpolating toward the better endpoint without evidence re-introduces the *second magnitude knob underneath `type_gain`* that §4.3 of the amendment rejected on exactly these grounds, and damps the **inert** channel and the **suspect** channel together in unknown proportion. It buys a fraction of an unmeasured benefit at the cost of a fraction of an unattributed harm |
| **ship the mechanism at 0.0, opt-in** | §5.3. It reverts three defects the amendment exists to fix, makes AC-035 false at the shipped default and thus forces a `tests/` change, and is justified by a delta whose channel is not isolated and whose significance ceiling is p = 0.031. It also makes the *default* configuration one in which no acceptance criterion exercises the mechanism |
| **lower `inhib_gain` now** | It is the right *suspect* (§4.1) and the wrong *time*: there is **zero direct evidence** on it, because the isolation was never run. Changing it now would be the same error this record is refusing at a different address. It is RC-1 |
| **revert the amendment itself** | §7 |

## 6. The negative product finding, recorded as such

Understating this would be the failure mode. **The design's actual claim has now been exercised for
the first time and it did not help.**

- FORGE ruled H-BODY-b *"falsified as implemented, **untested as designed**"* precisely because
  declared edges carried zero mass. That "untested as designed" condition is now discharged.
- The result: at **full** authored mass, declared `composes`/`depends_on` in the activation graph
  changed **zero of 210 top-1 answers** in the isolated arm (c), and the full pipeline (d) was
  **worse by six queries**. The best (d) has ever measured — 0.5476, exactly tying the dense
  embedding baseline (b) — is measured with **declared edges off**.
- Therefore: **H-BODY-b remains falsified as implemented, and is now additionally "not supported as
  designed" on the only corpus that exists.** The claim that authored graph structure improves
  routing has zero supporting measurements and two non-supporting ones (0/210 isolated, −6/210 in
  the full pipeline).

What the amendment *did* deliver, and what this measurement does not touch:

- `plan_confidence` is a real, satisfiable, structurally-meaningful ratio instead of a permanent
  0.0 that reached clients (AC-037, AC-038).
- Inhibition is reachable from `register()` rather than only from direct SQL (AC-034).
- Cycle-breaking is deterministic instead of dict-iteration-ordered (AC-042).
- One weighting site instead of three divergent local workarounds (AC-040).

None of those four are retrieval-accuracy claims, and none of them is contradicted by this run.
**The amendment fixed a correctness and coherence defect; it has not been shown to improve
routing.** Those should be stated separately in any release note, and the second should be stated
without hedging.

## 7. Was the amendment itself wrong? — no, and here is the falsifiable form of that answer

The brief asked for this plainly, so: **no, and I would say so if I thought otherwise.** The
amendment is not primarily a retrieval-accuracy bet, and it should not be judged as one:

- `plan_confidence` was **unsatisfiable as the spec wrote it** — a structural constant reaching
  clients as a confidence number. That is a defect independent of any benchmark, and it is fixed
  in a way that is independent of `declared_edge_strength` entirely.
- **AC-023 was unreachable by any production path.** A criterion that only a direct-SQL fixture can
  reach is a coverage defect whatever the routing numbers say.
- The **three divergent local workarounds** (`_COMMUNITY_WEIGHT_FLOOR`, the hub-PageRank proxy,
  bench baseline (c)) each patched their own metric around the same zero. Collapsing them into one
  rule is right on maintenance grounds alone.

What *is* now in doubt is narrower and I will name it exactly: **the claim that authored edges
belong in the diffusion graph** (call-site rows 1 and 3). That claim has **zero** supporting
measurements and one measured **0/210**. If RC-3 also returns null, that specific claim should be
retired — the mechanism kept, implemented and opt-in, with `declared_edge_strength = 0.0` as the
shipped default and AC-035 restated. **That is a real, pre-committed possibility, not a formality**:
it is RC-3's decision rule, and it does not require RC-2 to reach significance.

The part of the amendment that would survive even that outcome is everything in §6's second list,
plus `S_eff`'s **floor** form, which is what keeps the earnable-declared-edge path (CF-3) open.

## 8. R12's disposition

**FIRED** 2026-08-15 (mitigation executed as designed) · **stays OPEN at P1, narrowed** ·
owner unchanged: Vivi (config) + Kupo (verify).

| | before | after |
|---|---|---|
| what is unmeasured | "newly-live declared-edge mass" in general | **the inhibition magnitude** (`inhib_gain = 0.7` never calibrated for `S = 1.0`) and **the community re-clustering** |
| `ppr_restart = 0.85` | measured on the old graph; may not transfer | **CONFIRMED on the new graph shape**; this clause is closed |
| release obligation | MO-1, MO-2, MO-3 all owed | **MO-1 discharged**, **MO-2 discharged**, **MO-3 still owed** (= RC-1) |
| exit condition | "re-run and publish" | four **pre-registered reversal conditions with decision rules**, RC-1…RC-4 (spec.md §3.3.1) |

Carried forward unchanged from `DECLARED-EDGES-AMENDED`: **CF-1** (IDG's docs/01 + docs/07
corrections — and they now need one more line, that the declared-edge design claim was exercised
and returned 0/210), **CF-2** (`synapses:` has no reader — still unfixed, still losing weight every
day), **CF-3** (the earnable path stays open), **CF-4** (FORGE D1/D2/D3), **CF-5** (the uncalibrated
combination rule — this record's `inhib_gain`-at-`S = 1.0` finding in §4.1 is a **second instance of
exactly that failure mode**: a constant calibrated against one implicit distribution, silently
re-pointed at another), **CF-6** (R1-RESTATED's own residuals).

**New: CF-7 — a compositional query set.** H-COMPOSE has never been tested and RC-3 depends on it;
Plan F1 as implemented cannot serve, being a monotone re-encoding of Hit@1. Owner: Vivi / ATLAS.

## 9. What would settle this properly

Ranked by how much each would actually decide, not by cost:

1. **RC-1 — isolate inhibition (MO-3, already owed).** Cheapest and most decisive: one arm,
   `inhib_gain = 0.0` at `declared_edge_strength = 1.0`, same corpus. It partitions the −6 between
   the two candidate channels and tells you *which knob* is even under discussion. Do this first;
   everything below is more expensive and less targeted. **Also confirm (c) responds to the knob at
   all** (§4, reading 2).
2. **RC-2 — a second, independently-authored corpus + a paired McNemar test.** Two defects at once:
   it removes the single-author confound (the same agent wrote the relations *and* the queries) and
   it replaces "6 in 210" with a p-value. Independence must be in the **authorship of the
   relations**, not just the queries — that is the confound that matters here.
3. **RC-3 — a compositional query set (CF-7).** The only way to test the claim declared edges are
   actually *for*. Single-target Hit@1 is structurally near-adversarial to composition edges
   (§5.4), so a null there is uninformative about composition and a null in RC-3 is decisive.
4. **RC-4 — a registry with known-good declared relations.** Constructed so `needs`/`composes`/
   `inhibits` are correct by fiat. Distinguishes "the design does not help" from "these particular
   authored relations are noise."
5. **Not sufficient, and worth saying:** re-running the same corpus with more queries. It shrinks
   the error bar on a measurement whose confound (single authorship of both relations and queries)
   is not statistical. More precision on a possibly-biased estimate is not evidence.

## 10. What changed in the plan artifacts

| Artifact | Change |
|---|---|
| `decisions/R12-FIRED.md` | **new** — this record |
| `spec.md` | four located patches: the reading-contract errata banner; §3.3.1's release-obligation paragraph → the published measurement table, the decision and its five grounds, the rejected intermediate value, and the RC-1…RC-4 table; the §Risks **R12** row (fired, narrowed, reversal conditions); the Handoff obligation bullet |
| `spec.yaml` | fourth `errata:` entry; `risks[R12]` rewritten with `fired: true` + `fired_at`; refreshed `artifacts[]` hashes + one new `artifacts[]` entry for this record |
| `spec.envelope.json` | ECL integrity tag re-stamped to the new `spec.md` hash; **fourth** `x_ramza_amendment` entry appended (the first three left intact, so the envelope carries the whole chain) |
| `plan-state.json` | one appended `errata[]` entry; `amendments[]` and `criteria_sha256` untouched |
| `acceptance-criteria.md` | **untouched, byte-identical** — the tamper anchor |
| `acceptance-criteria-addendum.md` | **untouched, byte-identical** — no criterion is added, edited or removed by this record |
| `magicite.toml`, `src/`, `tests/`, `docs/`, `change.json` | **not touched.** The default does not move, so no config change is implied; RAMZA plans, Vivi implements; `change.json status` is not RAMZA's to move |

### 10.1 `spec.md` hash transition

```
old sha256  e9efab604467129246e0b70fb25ed70a576bc4fee5934a243881e2151101dd8b   (119819 bytes)
new sha256  757313fc568c2b2332d67e255f25b493b7f03c16583fb6a8e1693b4e29200440   (130531 bytes)
```

Full chain, now five links: `92372ad6…` → `9fca1c08…` (A1-REVISED) → `57148a2d…` (R1-RESTATED)
→ `e9efab60…` (DECLARED-EDGES-AMENDED) → `757313fc…` (this record).

## 11. The ECL envelope re-stamp

Identical mechanism and identical reasoning to A1-REVISED §5, R1-RESTATED §8 and
DECLARED-EDGES-AMENDED §10, applied a fourth time. `spec.envelope.json` carries an ECL v2.0
integrity tag over `spec.md`'s bytes; patching `spec.md` necessarily invalidates it, and leaving it
stale would make every future `ramza-verify-emit` report a **false** tamper alarm on an authorized,
recorded change — and tamper signals that are known-broken stop being read.

`ramza-freeze --amend` was again **deliberately not used**: it amends `plan-state.json
criteria_sha256`, i.e. the `acceptance-criteria.md` anchor, which must not move. The re-stamp is a
targeted rewrite of `artifact.sha256`, `artifact.size_bytes` and `integrity.value` plus a **fourth**
appended `x_ramza_amendment` entry, verified afterwards by `ramza-verify-emit --spec spec.md
--envelope spec.envelope.json`. `objective`, `context_delta.summary`, `trace.ts` and
`x_ramza_acceptance_criteria` are left as sent.

## 12. Deliberately not changed

- **`declared_edge_strength = 1.0`** — the subject of this record. Confirmed, not re-derived.
- **`S_eff = max(storage_strength, w_authored)`**, `w_authored`, all seven call sites, the
  withheld hub-penalty PageRank, and the read-time/never-stored property. `DECLARED-EDGES-AMENDED`
  is **not superseded**.
- **`inhib_gain = 0.7`, `w_activation`, `w_similarity`, `w_retrieval`, `type_gain`, `hub_penalty`,
  `ppr_restart`** — no routing constant moves. `ppr_restart` is *confirmed* at its amended value.
- **`acceptance-criteria.md`** — byte-identical, sha256 `7bd3d184…`, verified before and after.
  Not re-frozen, not amended; `amendments[]` stays empty; `criteria_sha256` untouched.
- **`acceptance-criteria-addendum.md`** — byte-identical at `28888863…`. AC-034…AC-042 stand as
  written. **AC-039 stays satisfiable by construction** (§5.3); AC-035's dependence on a non-zero
  default is *recorded* as a consequence of the rejected 0.0 option, not acted on.
- **The 16-tool surface**, INV-1…INV-4, the P0 guards, the Tier A/B/C split, all nine VG commands,
  the M0–M7 decomposition, all eight §9 CR resolutions.
- **The confidence score (84.75 → VALIDATE), the complexity score, the explore scores, the
  refine-cycle count, the critic record, and the phase walk.** Not re-run and not re-scored, per
  the same discipline as the three prior errata. This is a measurement record against a shipped
  spec, not a re-plan.
- **`src/`, `tests/`, `docs/`, `magicite.toml`** — outside RAMZA's write boundary and, since the
  default does not move, not implicated.

## 13. Maker≠checker, disclosed

This record confirms a default that **I** set, on evidence that arrived after I set it. That is a
maker evaluating their own work, and it is disclosed rather than laundered — the same posture as
R11. Three things bound it:

1. The **decision rules are pre-registered** (RC-1…RC-4), so the next person applies them without
   needing to re-litigate my judgement, and one of them (RC-3) can retire the diffusion claim
   **without** requiring RC-2 to reach significance.
2. The **negative finding is recorded at full strength** (§6, §7) rather than softened — including
   the sentence that the best (d) has ever measured is measured with declared edges *off*.
3. The **strongest argument against my own key evidence** is stated in the record (§4, reading 2:
   (c)'s invariance might be a harness artefact) with an instruction to check it first.

Kupo attests RC-1's outcome at ESL `verify`, and the frozen criteria hash — not this record — is
the tamper-evidence anchor.
