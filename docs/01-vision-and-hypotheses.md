# 01. Vision and Hypotheses: Organic Skill Learning

**Status:** Draft-refined / v1  
**Provenance:** exploratory/consolidated-research-graph-mcp-proposal.md Part III, engram-format.md §0, FINDING-005, FINDING-008  
**Decisions implemented:** Problem statement & hypothesis register

---

## The Problem: Static Skill Routing

Today's agent-skill systems use a **flat, static, description-only routing model**:

- A skill is a directory containing `SKILL.md` with a ~100-token description and a Markdown body.
- At startup, the agent's harness injects all skill names + descriptions into the system prompt or a retrieval index.
- When the agent needs a skill, it matches against that description using in-context judgment (or a thin embedding retrieval).
- The description never changes; the routing never improves.

This model has explicit known limits, quantified by 2026 research:

### Empirical Evidence: Three Findings

**[HYPOTHESIS] H-BODY: Body content drives routing accuracy.**

> **Source:** SkillRouter (arXiv 2603.22455, Mar 2026, UNVERIFIED-2026)  
> **Claim:** On an ~80K-skill SkillsBench-derived benchmark with 75 expert-verified queries, hiding the skill *body* and routing on metadata alone costs **−31–44 percentage points** of routing accuracy across sparse, dense, and reranking baselines.

**Implication:** A 100-token description is insufficient. The full skill body (examples, pitfalls, procedural steps) carries routing signal that expert models exploit. Routing systems that ignore it are systematically worse.

---

**[HYPOTHESIS] H-SCALE: Flat registries degrade logarithmically with size.**

> **Source:** "Scaling Laws of Skills in LLM Agent Systems" (DeepSignal, May 2026, UNVERIFIED-2026)  
> **Claim:** Single-step routing accuracy decays **logarithmically with library size** (\(R^2 > 0.97\) across models). Error taxonomy: local skill competition → cross-family drift → capture by overly general "black-hole skills".

**Implication:** As skill libraries grow from 10s to 100s to 1000s, routing accuracy falls predictably. The scaling law is not a gradual curve—it's a power law. Flat lists hit a ceiling; hierarchy and hub-suppression are structural, not tuning, fixes.

---

**[HYPOTHESIS] H-COMPOSE: Real tasks are compositional.**

> **Source:** CompSkillBench (Jun 2026, UNVERIFIED-2026)  
> **Claim:** Real agent tasks require decomposition and multi-step skill chaining. 300 queries over 2,209 real MCP skills decompose into plans of 2–5 skills, applied in sequence.

**Implication:** Routing must return *ordered sets of skills* that compose toward a goal, not atomic single-skill answers. A router that returns one skill at a time, even perfectly, is undershooting what the task demands.

---

### What the Status-Quo Model Cannot Do

1. **Learn from usage.** A skill routed today, applied successfully, yields zero signal for tomorrow's routing. The index is read-only.
2. **Adapt to context.** The same skill description is shown to every agent, every session. No memory of "this skill works for problem class X but fails for Y".
3. **Surface hidden failures.** A skill may be correctly stored (learnable) but inaccessible due to poor triggers or description mismatch—without instrumenting the system, this is silent rot.
4. **Route on task composition.** Each skill is selected independently; there is no way to ask "what sequence of skills solves this plan?".

---

## The Vision: Organic Skills

We propose **skills as learnable, trace-refined artifacts**:

1. **A portable format** (ENGRAM `.egr.md` files) that carries:
   - Routing data (intent, triggers, context affinity)
   - Plasticity state (learned edge weights, outcome counts, per-step fault attribution)
   - Provenance (who authored, who refined, when, why)

2. **A plasticity engine** that:
   - Routes on full skill content via embeddings + learned weighted-graph spreading activation
   - Updates edge weights through outcome-gated, two-phase-commit learning (signals are tagged in-session, captured only when outcomes are verified)
   - Consolidates offline in a Dream-cycle worker (prunes weak edges, renormalizes weights, distills new composite skills, refreshes drifted embeddings)

3. **An outcome-feedback loop** that:
   - Observes skill application via hooks (PreToolUse/PostToolUse), tool-mediated self-report, or passive inference
   - Assigns outcome signals (success/failure) with provenance-weighted confidence
   - Drives plasticity only on high-confidence signals, never on noise

4. **Governance and trust** such that:
   - Imported or model-generated engrams are quarantined until verified
   - Lifecycle transitions (probation → consolidated → promoted) require accumulated evidence, not single-session luck
   - Rollback and version history are native (archived-never-deleted)

### How This Addresses the Gaps

| Gap | Solution |
|---|---|
| **H-BODY** (body content ignored) | Seeding: query embedding + skill body embedding; learned edge weights trained on full traces, not extracted descriptions |
| **H-SCALE** (log decay with size) | Hierarchy: Leiden community detection partitions the graph; queries route to community first, then within it (two-level routing) |
| **H-COMPOSE** (no task composition) | Composition DAG: `needs:` / `yields:` / `composes:` edges declared in ENGRAM; router expands winner into a topologically-sorted plan |
| **H-LEARN** (routing is static) | Outcome-gated plasticity: every session → signals → Dream consolidation → weight updates → next routing is better (or worse, if outcome was fail) |

---

## The Hypothesis Register

Each gap maps to a falsifiable hypothesis tested in **doc 07 (Evaluation)**:

| ID | Hypothesis | Prediction | Result (2026-08-15, 70 engrams / 210 queries) | Status |
|---|---|---|---|---|
| **H-BODY-a** | Body-aware embedding beats description-only lexical routing | Hit@1 gain ≥20pp | **+14.3pp Hit@1** (0.5476 vs 0.4048), 95% CI [+6.7, +21.9], exact McNemar p = 0.00064 | **SUPPORTED (direction).** The registered ≥20pp effect size is **not demonstrated**; the CI does not exclude it. |
| **H-BODY-b** | Graph- and learning-aware routing beats naive baselines | (d) > (c) > (b) > (a) | Observed **(b) > (d) > (c) > (a)**. (d) Hit@1 0.5333 vs (b) 0.5476: (d) − (b) = −0.0143, statistically indistinguishable (gap 3 queries; prior measurement 0.4619, gap 18 queries, p = 0.00053). (d) − (c) = 0.0047 (p not significant). (d) − (a) gain 0.1285 (p < 0.05). | **FALSIFIED as implemented.** Pipeline no longer actively hurts but does not beat dense-embedding baseline. Hit@3 open: (d) 0.7476 > (b) 0.7429 (pre-registered open question, not adopted per RC-5). |
| **H-SCALE** (mechanism) | Leiden community rerank contributes to ranking | ablation shows a loss | ΔHit@3 = **0.0000**, ΔMRR = **+0.0005**, with 5 real communities (19/19/13/11/8) | **FALSIFIED at 70 skills.** |
| **H-SCALE** (claim) | Hierarchy flattens the log-decay curve | slope drops ≥50% | one unreplicated crossing between 40 and 70 engrams on a 39-query slice (SE ≈ 0.08); does not replicate on 120 queries | **INCONCLUSIVE — unevidenced in both directions.** |
| **H-COMPOSE** | Topological plan expansion improves compositional success | Plan F1 gain | **No compositional queries were run** (0 of the ≥20 docs/07 requires). Plan F1 as implemented is a monotone re-encoding of Hit@1. | **UNTESTED.** |
| **H-LEARN** | Outcome-gated plasticity improves routing over time | Hit@k gain ≥10pp after N cycles | Under an **oracle** teacher, held-out Hit@1 fell **0.4697 → 0.1061**; train-split Hit@1 fell 0.4583 → 0.2847 | **FALSIFIED as implemented, under uniform demand.** Direction is opposite to the prediction. |

### What the Evidence Licenses

These results come from a single benchmark authored by one agent (corpus and queries by the same author, single annotator, one embedder, uniform learning workload, no `context` conditioning). The paired ordering results and the within-system component sweep are robust to that authorship bias — the bias applies equally to every arm. The absolute Hit@k levels are not. H-SCALE and H-COMPOSE remain open questions, not settled negatives.

### Falsification Record (2026-08-15)

Four load-bearing negatives were found and are recorded below to preserve accurate falsification history. **Update (2026-08-15):** The declared-edges amendment (`S_eff = max(storage_strength, w_authored)`) and inhib_gain recalibration (0.7 → 0.245) fixed defects 1–2 below, raising (d) Hit@1 from 0.4619 to 0.5333; this is mechanism repair (fixing things that were broken), not evidence that the design hypothesis is correct. Defects 3–4 remain.

- **The activation graph, on any registry that has not been through Dream, contains only derived `similar_to` kNN edges:** declared `composes`/`depends_on`/`inhibits` edges are written at `S_edge = 0.0` (hardcoded in `durable.py:203`) and Dream potentiates only `co_activation` edges (`dream.py:194`, `signals.py:154-160`). Spreading activation therefore re-derives the cosine signal already scored at `w_similarity`, and contributes the entire (c)/(d) deficit versus (b) in the benchmark. `ppr_restart = 0.85` or `w_activation = 0` recovers (b)-level accuracy (scale-benchmark §6).

- **Declared `inhibits` edges have never had any effect** in production use, for the same reason. Inhibition scales by `S_edge` in `activation.py:144-167`; with `S_edge = 0.0` on all declared edges, all 11 `inhibits` relations in the 70-engram registry remain inert. (scale-benchmark §6, component sweep).

- **Retrieval strength `R` is an unconditioned popularity prior** entering the score at 63% the amplitude of the query-conditioned similarity signal (scale-benchmark §5c: w_retrieval · R spread = 0.0354 vs w_similarity · cosine spread = 0.0561), with no calibration and no misalignment detector. The hub penalty, the mechanism meant to prevent hub capture, is computed on a *structural* PageRank (`router.py:14-25`) and is blind to learned hubs by construction. (scale-benchmark §5b–c).

- **No offline benchmark can exercise Magicite's learning without faking the clock.** `compute_eta_eff_untiered` gates every weight change on a ~6h spacing term and pins first observations at spacing 0.0; 144 real production-path turns produced `committed_nodes: 0, committed_edges: 0`. Two observations of the same engram must be ≥ ~54 minutes apart to move any weight. (scale-benchmark §5a).

---

## Principles Underlying the Design

**[DECISION D1–D4] Principle 0 — "Never learn from the hot path alone."**

Online signals (in-session, adversarial-prone, noisy) are not trustworthy for durable updates. Learning happens in two phases:
1. **Hot path (cheap, reversible):** tag activated skills, set candidate edges, boost retrieval strength.
2. **Dream cycle (offline, outcome-gated):** capture tags into durable storage-strength changes only when outcome signals are verified.

Mitigates feedback loops, adversarial confusion, and transient noise. Structurally underpins the three-tier state model (doc 03).

---

**[DECISION D4–P2] Principle 1 — "Retrieval is a write to the ledger, not to the weights."**

Every routing decision is logged and audited; **retrieval strength (R) moves only on confirmed application, storage strength (S) moves only on outcome capture**. This fixes an internal contradiction in the exploratory corpus (rule 6 vs rule 7 at exploratory/consolidated-research-graph-mcp-proposal.md:241–242):

- Old rule 6: "update R for every node route() returns" → position bias, returned skills keep getting returned.
- Old rule 7: "never update on listing" (rule 6 violates this).
- **Refined:** Retrieval updates *bookkeeping* (exposure counts, last_activated), never weights. Weights move only on `signal_use` (application) + `signal_outcome` (verification).

---

## Open Questions

1. **How does plasticity degrade gracefully on hookless hosts?** (Addressed in doc 05: Tier-0 passive inference + Tier-1 tool-mediated self-report are both valid learning channels; outcome capture simply slows. Not dead.)
2. **Will outcome signals from average agents be too noisy to drive learning?** Measured 2026-08-15: with a perfect (oracle) teacher and zero signal noise, held-out routing still degraded 3.6× on Hit@1. Signal noise was never the binding constraint; the score-combination rule is.
3. **Does Leiden hierarchical routing scale efficiently at 10³–10⁵ nodes?** (Deferred to implementation; the algorithm is linear-time. If needed, exact Leiden is replaced with approximate/approximate community detection.)

---

*Section authored by IDG, 2026-08-14, drawing on exploratory/consolidated-research-graph-mcp-proposal.md Parts III–IV.*
