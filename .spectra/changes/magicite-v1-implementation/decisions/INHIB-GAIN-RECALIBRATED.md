---
eidolon: ramza
kind: decision
id: INHIB-GAIN-RECALIBRATED
version: 1.0.0
created_at: 2026-08-15
change_id: magicite-v1-implementation
supersedes: "the routing default inhib_gain = 0.7, and DECLARED-EDGES-AMENDED §11's listing of it among 'deliberately not changed'. Nothing else. S_eff = max(storage_strength, w_authored), declared_edge_strength = 1.0, every call site and every acceptance criterion stand."
status: recorded
disposition: "ROUTING DEFAULT CHANGED on a derived magnitude, after RC-1 discharged both halves. inhib_gain 0.7 -> 0.245 (= theta_synapse 0.35 x 0.7). PRECAUTIONARY PENDING RC-2. declared_edge_strength = 1.0 stands CONFIRMED. No rule change, no acceptance-criteria change, no re-plan, no re-scoring, no phase re-walk."
authorizing_trigger: "RC-1, the reversal condition pre-registered by errata R12-FIRED (spec.md §3.3.1), run by the orchestrator at commit 9963b89"
evidence_path: "scratchpad/rc1-results.md (session-local; every load-bearing number reproduced below). Same 70-engram / 210-query corpus, real fastembed, cold registry, nothing re-authored."
recorded_by: ramza
appendonly: true
---

# INHIB-GAIN-RECALIBRATED — the mechanism came from the corpus; the magnitude did not

> **Append-only.** Written once. If `inhib_gain` changes again, write
> `INHIB-GAIN-RECALIBRATED-2.md` and mark this record superseded; do not edit it in place.

---

## 1. RC-1 fired, and it discharged both halves

R12-FIRED pre-registered RC-1 as *"MO-3, still owed"* with a decision rule attached, plus an
instruction to check the caveat that record raised **against its own key argument**. Both were run.

### 1.1 RC-1b — the self-caveat, discharged in RAMZA's favour

R12-FIRED §4 reading 2 said: *"(c)'s invariance could alternatively be a HARNESS ARTEFACT if the
(c) arm did not pick up the config override … whoever runs RC-1 must confirm (c) responds to the
knob at all before concluding anything from its invariance."*

| `declared_edge_strength` | (c) Hit@1 | (d) Hit@1 |
|---|---|---|
| 0.0 | 0.5286 | 0.5476 |
| 1.0 (default) | 0.5286 | 0.5190 |
| **5.0 (extreme)** | **0.5333** | 0.5048 |

**(c) responds.** Its invariance between 0.0 and 1.0 is therefore a **real property of the corpus**,
not an instrumentation failure. Full authored mass in the diffusion graph genuinely changes **zero
of 210 top-1 answers** at the shipped magnitude. **`declared_edge_strength = 1.0` stands
confirmed**, and R12-FIRED's refusal to damp a channel measured inert was correct.

Worth recording for its own sake: at the **extreme** 5.0, (d) degrades further (0.5048) while (c)
improves slightly (0.5333). The two arms move in **opposite** directions under more authored mass —
consistent with §2's finding that the harm in (d) lives in a channel (c) does not have.

### 1.2 RC-1a — inhibition is the channel

At `declared_edge_strength = 1.0`:

| `inhib_gain` | (d) Hit@1 |
|---|---|
| 0.7 (shipped) | 0.5190 |
| 0.0 (inhibition off) | **0.5429** |

**0.0239 of the 0.0286 gap — 84%.** The residual is **0.0047, one query**, which is community
re-clustering or noise and is **not separately resolvable on this corpus**; R12's community clause
is closed on that basis rather than left open as though it were a live concern.

**The mechanism was named before the measurement.** R12-FIRED §4.1 stated it in advance:
*"`inhib_gain = 0.7` predates this amendment and was set when an `inhibits` edge's `S` was expected
to be a LEARNED value distributed over [0,1] … the shipped multiplier is `1 − 1.0 × 0.7 = 0.3`: a
70% cut of the inhibited node's activation from one unweighted line of author YAML."* A prediction
made before the experiment and confirmed by it is worth more than the same claim made after, and
that is most of why this record reaches a different verdict than R12-FIRED did about
`declared_edge_strength`.

### 1.3 The calibration sweep

At `declared_edge_strength = 1.0`; dense-embedding reference Hit@1 0.5476, Hit@3 0.7429:

| `inhib_gain` | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| 0.0 | **0.5429** (114/210) | 0.7429 (156) | **0.6381** |
| 0.1 | 0.5333 (112) | 0.7429 (156) | 0.6348 |
| 0.2 | 0.5333 (112) | 0.7476 (157) | 0.6335 |
| 0.3 | 0.5286 (111) | **0.7571** (159) | 0.6358 |
| 0.5 | 0.5095 (107) | 0.7524 (158) | 0.6260 |
| **0.7 — shipped** | 0.5190 (109) | 0.7333 (154) | 0.6227 |

*(One arithmetic correction to the RC-1 report, since precision is the point here: with inhibition
off, (d) 0.5429 trails (b) 0.5476 by **one** query at n = 210 — 114 against 115 — not two.)*

---

## 2. The question actually put, answered narrowly

The orchestrator framed it exactly right, and the framing is the reason this record can reach a
clean answer: **"whether the replacement is 0.0, 0.3, or something only a second corpus can reveal
is separate from whether 0.7 is still defensible."** Those are two questions and they have two
different evidentiary standards. Conflating them is what produces argmax-chasing.

### 2.1 `inhib_gain = 0.7` is not defensible — three grounds, none an argmax

**(a) It is Pareto-dominated by four of the five alternatives swept.**
`0.7 = (109, 154, 0.6227)` is worse than `0.0`, `0.1`, `0.2` **and** `0.3` on **Hit@1, Hit@3 and
MRR simultaneously**, and it is the **worst value in the whole sweep** on both Hit@3 and MRR. Only
`0.5` fails to dominate it (109 > 107 on Hit@1), and `0.5` is itself dominated by `0.3`.

This is the load-bearing statement because it is **metric-independent and argmax-independent**. It
does not require deciding whether Hit@1 or Hit@3 matters; it does not require picking a winner; and
it is a *within-sweep* comparison, which is the design in which the single-author confound
**cancels across arms** — the same property that made the `ppr_restart` sweep acceptable evidence
(FORGE rated that measurement type E-2, reliability H, for exactly this reason). The confound
attacks *absolute* levels and *argmax selection*. It does not attack *dominance ordering*.

**(b) It never had any evidence for it, in any regime.** This is the decisive asymmetry. Until
DECLARED-EDGES-AMENDED, `apply_inhibition` was a **numeric no-op** — that is §3.3.1's founding
finding, confirmed by execution to twelve decimal places. `inhib_gain = 0.7` was therefore a
plausible-looking constant **in a dead code path**, never measured against anything, ever. Its very
first exposure to measurement Pareto-dominates it away.

The usual reason to hold a default against weak evidence is that the incumbent has standing — it
was chosen for a reason, or it survived prior measurement. **This incumbent has neither.** "Do not
change on weak evidence" protects values that earned something; it is not a general licence to
keep whatever is currently written down.

**(c) The mis-calibration is diagnosable from the design's own constants, without any corpus.**
`theta_synapse = 0.35` is the strength at which an edge becomes a **genuine synapse**
(`src/magicite/core/dream.py:503,515` — candidates below it, or with `evidence_count < 3`, are
excluded from `synapses:`). So for a *learned* `inhibits` edge, the design's intended effect
`S × inhib_gain` ranged over:

```
[ theta_synapse × 0.7 , w_max × 0.7 ]  =  [ 0.245 , 0.7 ]
     "just became a synapse"              "maximally reinforced"
```

§3.3.1 pins `S_eff = 1.0` for **every** authored `inhibits` edge **on day zero**. So an assertion
with **no accumulated evidence whatsoever** was being granted the effect magnitude the design
reserved for a **maximally-potentiated, sustained-evidence** synapse. That is the error, and it is
visible by reading two constants — no benchmark required.

### 2.2 The replacement — derived, not selected

```
inhib_gain = theta_synapse × inhib_gain_learned = 0.35 × 0.7 = 0.245
```

An authored assertion enters at the magnitude the design assigns to an edge that has **just**
become a genuine synapse — the **bottom** of the intended range, not the top. A declared `inhibits`
now scales the inhibited node's activation by `1 − 1.0 × 0.245 = 0.755` (was `0.3`).

**Deliberately not rounded to `0.25`.** The un-round figure is the audit trail: `0.245` is visibly
derived, `0.25` would look tuned. **If `theta_synapse` ever moves, this is re-derived, not
re-tuned** — and that dependency is stated here so it is not discovered later.

**Why this is not the overfitting this spec has twice refused.** The magnitude comes from
`theta_synapse` and the original `inhib_gain`, **both of which predate the amendment and neither of
which came from this corpus**. The corpus's role is **corroboration, not selection**: `0.245` falls
between the swept points `0.2` and `0.3` — both on the constrained Pareto frontier — and it is the
**argmax of nothing**. Taking the **mechanism** from the corpus (which the sweep establishes
robustly, at 84%) while taking the **magnitude** from the design's own constants is the only
construction here that a single-author corpus cannot contaminate.

### 2.3 `inhib_gain = 0.0` is forbidden — and it is the corpus's own Hit@1 argmax

At 0.0 the pass computes `a_i *= (1 − S × 0) = 1`, so **AC-023**'s *"the inhibited engram's score
SHALL be strictly lower"* and **AC-034**'s *"strictly lower than at `declared_edge_strength = 0.0`"*
become arithmetically **unprovable** — not failing, *unprovable*. This is the identical argument
that rejected `w_activation = 0` for a 0.0048 Hit@1 gain: **0.0048 Hit@1 is noise; a frozen
criterion is the tamper-evidence anchor.** It would also ship an "inhibition" mechanism that does
not inhibit — precisely the dead-mechanism state DECLARED-EDGES-AMENDED exists to end.

Note what this does: it is a **non-corpus constraint that excludes the corpus's own
Hit@1-maximising answer**. Had the constraint not existed, the Hit@1 data alone would have argued
for 0.0, and following it would have been the argmax error in its purest form.

### 2.4 Options rejected

| option | why rejected |
|---|---|
| **ship `0.7` unchanged, let RC-2 settle it** | Considered seriously — the orchestrator explicitly offered it and was not fishing. Rejected because *consistency requires applying the same **test**, not returning the same **answer***. R12-FIRED's test was: is the mechanism identified? does the value have prior standing? what does reversal forfeit? For `declared_edge_strength`: mechanism **not** identified, value **derived** from an existing knob (`S_eff × type_gain` at 1.0 **is** `type_gain`), reversal forfeits three shipped behaviours → **keep**. For `inhib_gain`: mechanism identified **and pre-registered**, value has **no** standing in the regime it operates in, reversal forfeits **nothing structural** provided it stays > 0 → **change**. Same test, opposite inputs, opposite answer. Keeping 0.7 would mean defending a number that is Pareto-dominated by four of five alternatives, was never measured in any regime, and is now known mis-calibrated by a mechanism named in advance |
| **`0.3`** (the Hit@3 argmax) | The overfitting the orchestrator flagged, and he is right. Six numbers from one corpus whose relations and queries share an author; a sweep that picks its own winner has learned about the corpus. That `0.245` lands near `0.3` is **corroboration**, not the reason for it — and if the derivation had pointed at 0.5 this record would have said 0.5 |
| **`0.0`** (the Hit@1 argmax) | Forbidden — §2.3 |
| **`0.25`** (a rounded 0.245) | Cosmetic rounding would obscure the one thing that makes the value defensible: that it was computed, not chosen |
| **re-derive `theta_synapse` too** | Out of scope and unmeasured. It governs Dream's `synapses:` membership, not routing; nothing here bears on it |

---

## 3. What this does **not** rescue

Stated plainly so no one reads a knob change as a product win.

- **Even with inhibition entirely off, (d) = 0.5429 is still below (b) = 0.5476** — one query.
  Turning inhibition down makes the full pipeline **stop losing by much**; it does not make it win.
- **H-BODY-b is not rescued.** R12-FIRED §6's negative finding stands **unaltered**: at full
  authored mass the diffusion channel changed **zero of 210** top-1 answers, and the claim that
  authored graph structure improves routing still has **zero supporting measurements**.
- What this record changes is that the full pipeline is no longer being **actively degraded** by a
  constant that was calibrated for a regime which never existed. That is a defect repair, not
  evidence for the thesis.

---

## 4. The Hit@1 / Hit@3 divergence — pre-registered as RC-5, not resolved

Across the sweep, Hit@1 falls roughly monotonically with inhibition while **Hit@3 peaks at 0.3
(0.7571), above dense embedding's 0.7429** — the **first measurement in this project of the graph
layer beating the embedding baseline at anything**.

It is mechanistically coherent: suppressing a mutually-exclusive competitor improves the candidate
**set** while risking the **top-1** pick in exactly the cases where the gold answer is what got
suppressed. It is also **six numbers from one corpus**, and Hit@1 is **not** monotone across it
(0.5 gives 107 while 0.7 gives 109), which is a noise signature that should temper any reading of
fine structure in this sweep.

**It is therefore pre-registered as RC-5 with the metric named in advance, so it cannot be chosen
after the fact:**

- **Hit@1 is the metric of record** — it is what the Hypothesis Register uses and what H-BODY was
  adjudicated on. A **Hit@3-only** improvement does **not** justify raising `inhib_gain`.
- If RC-2 **reproduces** the Hit@3 peak **and** Hit@1 is flat across that region, the question
  stops being a knob and becomes a **product decision** — is the deliverable the top-1 pick or the
  candidate set? — and it goes to the **human**, not to RAMZA.

Naming the metric before the second corpus runs is the whole point. Deciding afterwards which
metric mattered is how a sweep becomes a story.

---

## 5. What RC-2 / RC-3 must now answer

RC-1's outcome **changes the job** of the remaining conditions; they are not merely still open.

| | before RC-1 | after RC-1 |
|---|---|---|
| **RC-2** | test (d) at `declared_edge_strength` 1.0 vs 0.0, paired | **three** things: (i) that comparison; (ii) the **derived `inhib_gain = 0.245`** against its swept neighbourhood — and if it loses at **p < 0.05 paired** on Hit@1, the **derivation** is wrong and the value is **re-derived**, *not* replaced by the second corpus's argmax either; (iii) whether **RC-5's divergence reproduces** |
| **RC-3** | a compositional query set | **raised in priority.** Inhibition's claimed benefit is suppressing mutually-exclusive competitors, which should register in **candidate-set** quality — and RC-5's Hit@3 peak is the first sign of it. RC-3 is now the most likely place for the design's claim to be *supported* rather than merely not-falsified |
| **RC-4** | unchanged | unchanged |

**Independence requirement, restated because it is the confound that matters:** RC-2's second
corpus must be independent in the **authorship of the declared relations**, not merely of the
queries. A new query set over the same authored relations tests almost nothing here.

---

## 6. Acceptance criteria — nothing changes, and one obligation is created

**No criterion is added, edited or removed.** `acceptance-criteria.md` is byte-identical at
`7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`; the addendum is byte-identical
at `2888886351b9d658ab98bf1d2e94d77e43850201130a15b26be9c63f3e27ef02`.

| criterion | at `inhib_gain = 0.245` |
|---|---|
| **AC-023** (frozen) | **holds** — multiplier `0.755 < 1` strictly, so the inhibited score is strictly lower. Its fixture injects `storage_strength = 0.8` by direct SQL; `S_eff = max(0.8, 1.0) = 1.0`, unchanged |
| **AC-034** (addendum) | **holds** — strictly lower than the `declared_edge_strength = 0.0` run, by a smaller but still non-zero margin |
| **AC-039** | **unaffected** — at `declared_edge_strength = 0.0` the multiplier is `1 − 0 × inhib_gain = 1` for any gain, so exact-revert is independent of this change |
| **AC-035, AC-036, AC-037, AC-038, AC-040, AC-041, AC-042** | **unaffected** — none references `inhib_gain` |

**Verification obligation (VO-1).** AC-023 and AC-034 must be **re-attested at 0.245**. Both are
satisfiable by construction, but if either holds only **by a hair** rather than robustly, that is
itself evidence the magnitude is too low and **must be reported, not absorbed**. Owner: Vivi
(config) + Kupo (verify).

---

## 7. What changed in the plan artifacts

| Artifact | Change |
|---|---|
| `decisions/INHIB-GAIN-RECALIBRATED.md` | **new** — this record |
| `spec.md` | eight located patches: the errata banner; §3.3 route **step 5** pseudocode (`inhib_gain=0.7` → `0.245`, superseded value struck in-comment); the routing-defaults table (new `inhib_gain` row); §3.3.1 ground 2's closing sentence; the RC table (RC-1 **discharged**, RC-2/RC-3 sharpened, **RC-5 added**); the new normative **§3.3.2**; the §Risks **R12** row; the Handoff bullet |
| `spec.yaml` | fifth `errata:` entry; `risks[R12]` updated; refreshed `artifacts[]` + one new entry |
| `spec.envelope.json` | integrity tag re-stamped; **fifth** `x_ramza_amendment` entry appended |
| `plan-state.json` | one appended `errata[]` entry; `amendments[]` and `criteria_sha256` untouched |
| `acceptance-criteria.md`, `acceptance-criteria-addendum.md` | **untouched, byte-identical** |
| `decisions/R12-FIRED.md` | **untouched** — append-only, and **not superseded**: its `declared_edge_strength` ruling is *confirmed* by RC-1b |
| `magicite.toml` | **ONE LINE CHANGES** — `inhib_gain = 0.7` → `0.245`. Vivi's to apply; outside RAMZA's write boundary |
| `src/`, `tests/`, `docs/`, `change.json` | **not touched.** No code change is implied — `inhib_gain` is already read from config at `core/router.py`'s inhibition pass |

### 7.1 `spec.md` hash transition

```
old sha256  757313fc568c2b2332d67e255f25b493b7f03c16583fb6a8e1693b4e29200440   (130531 bytes)
new sha256  6d3bff8e6a4507b4afc758d843c85075e7cd3e5da1b14117909c13579138c4fe   (143106 bytes)
```

Chain, now six links: `92372ad6…` → `9fca1c08…` (A1-REVISED) → `57148a2d…` (R1-RESTATED) →
`e9efab60…` (DECLARED-EDGES-AMENDED) → `757313fc…` (R12-FIRED) → `6d3bff8e…` (this record).

The ECL envelope re-stamp follows the identical mechanism and reasoning as the four before it;
`ramza-freeze --amend` was again **deliberately not used**, because it would move the
acceptance-criteria anchor.

---

## 8. Deliberately not changed

- **`declared_edge_strength = 1.0`** — **confirmed** by RC-1b, not merely retained. R12-FIRED is
  **not superseded**.
- **`S_eff = max(storage_strength, w_authored)`**, `w_authored`, all seven call sites, the withheld
  hub-penalty PageRank, the read-time/never-stored property.
- **`theta_synapse = 0.35`** — it is the *source* of the derivation, not a subject of it.
- **`ppr_restart = 0.85`** (confirmed by the R12 re-measurement), `w_activation`, `w_similarity`,
  `w_retrieval`, `type_gain`, `hub_penalty`, `w_max`, and every plasticity constant.
- **`acceptance-criteria.md`** — byte-identical, not re-frozen, `amendments[]` still empty,
  `criteria_sha256` untouched, AC-023 not edited. **The addendum too.**
- **The 16-tool surface**, INV-1…INV-4, the P0 guards, the Tier A/B/C split, all nine VG commands,
  the M0–M7 decomposition, all eight §9 CR resolutions.
- **The confidence score (84.75 → VALIDATE), the complexity score, the explore scores, the
  refine-cycle count, the critic record and the phase walk** — not re-run, not re-scored, per the
  same discipline as the four prior errata.

---

## 9. Maker≠checker, and the one thing a re-verifier should be sceptical of

Disclosed as in R12-FIRED §13: RAMZA is changing a constant on evidence RAMZA asked for, having
named the mechanism in advance. A prediction confirmed is stronger than a claim asserted — **and it
is also the shape a motivated reasoner produces**, since naming a mechanism in advance makes any
subsequent confirmation feel decisive.

So the honest scepticism to hand forward is this: **the derivation in §2.2 is the part to attack.**
`inhib_gain_authored = theta_synapse × inhib_gain_learned` treats the *product* `S × inhib_gain` as
the quantity the design calibrated, and reads `theta_synapse` as the entry point of the intended
range. That reading is defensible — `theta_synapse` is what gates synapse-hood — but it is a
**reconstruction of intent**, not a documented derivation, because the original `0.7` came with no
recorded rationale at all. If a reader rejects the reconstruction, what survives unaffected is
§2.1: **`0.7` is Pareto-dominated by four of five swept alternatives and never had evidence for it
in any regime.** That case stands without the derivation. The derivation only answers *"then what
instead?"* — and its wrongness would be caught by RC-2's decision rule, which re-derives rather
than re-tunes.

Kupo attests VO-1 and RC-2 at ESL `verify`; the frozen criteria hash, not this record, is the
tamper-evidence anchor.
