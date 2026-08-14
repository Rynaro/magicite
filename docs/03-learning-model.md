# 03. Learning Model: The Plasticity Engine

**Status:** Draft-refined / v1  
**Provenance:** exploratory/consolidated-research-graph-mcp-proposal.md Part II.2–II.3, Part IV.4, engram-format.md §2, FINDING-004, FINDING-009  
**Decisions implemented:** [DECISION D1] Three-tier state model with corrected plasticity-locus; [DECISION D4] Neuroscience analogy table (keep/kill/reframe verdicts); Principle 0 (hot-path learning prohibition); Principle 1 (retrieval-as-ledger-write)

---

## Overview

Magicite learns by refining skill weights through **Hebbian co-activation** (skills used together get stronger edges), **outcome-gated consolidation** (weight changes only happen when outcomes are verified), and **offline distillation** (the Dream worker finds new composite skills and prunes dead ends).

This section explains:
1. The corrected neuroscience-to-engine translation table (what we keep, kill, reframe from the exploratory corpus)
2. The three-tier state model that fixes the FINDING-009 contradiction
3. Event pipeline and update rules
4. Forgetting and decay as first-class policy

---

## The Neuroscience-to-Engine Translation: Class A, B, C

[DECISION D4] The neuroscience analogies split into three classes. **Refined corpus rule:** every retained analogy must carry an engineering-native justification stated FIRST; biology is inspiration, never justification.

### Class A: Genuine Load-Bearing Principles (Keep)

These map to engineering patterns that stand on their own; biology validates the intuition.

| # | Mechanism | Engine Analog | Engineering Justification | Biology |
|---|---|---|---|---|
| 1 | Hebbian co-activation | Edge potentiation between co-used skills | Association mining: skills applied together in the same session build statistical co-occurrence edges. The rule \(\Delta w_{ij} = \eta a_i a_j \cdot outcome\) is simple correlation + outcome scaling. | Correlated firing strengthens synapses (Hebb 1949); causal trace analysis confirms (LTP, Kandel 1990s). |
| 3 | Synaptic tagging & capture | Two-phase commit for learning | Distributed systems pattern: cheap provisional intent (tag set in-session) + gated durable commit (only when outcome verified). Protects against noisy in-session signals and adversarial confusion. | Stimulated synapses set molecular tags (~1–3h); plasticity-related proteins captured by tags convert early → late LTP (Frey & Morris 1997). |
| 6 | Testing effect | Weight updates only on application, never on listing | Impression vs conversion: routing a skill is "browsing"; applying a skill is "use". Only use updates weights — prevents display bias where frequently-returned skills are returned more (circular feedback). | Retrieval practice beats restudy; mechanistically, retrieval sets a tagging event similar to encoding (2020s testing-effect literature). |
| 7 | Bjork storage vs retrieval strength | **Dual-timescale state: S (slow, node+edge) vs R (fast, edge only)** | Ranking systems use long-term priors (S) and short-term recency (R) as separate variables; standard practice in information retrieval. Enables stability + adaptability. **Corrected:** S exists at BOTH node (engram consolidation strength, gates lifecycle FSM) AND edge level (association strength, drives propagation). See Three-Tier Model below. | Storage strength = long-term prior cumulative; retrieval strength = context-sensitive short-term boost (Bjork memory research, 2020s). Dual-timescale is well-grounded. |
| 9 | Metaplasticity / LTP saturation | Learning-rate saturation: \(\eta_{eff} = \eta(1 - w/w_{max})\) | Standard stability control: avoid runaway feedback where frequently-reinforced edges monopolize updates. Prevents rich-get-richer blowup. Equivalent to gradient clipping in deep learning. | Synapses have a maximum potentiation ceiling; repeated stimulation produces diminishing returns (metaplasticity, Abraham & Bear 1996). |
| 12 | Sleep replay / systems consolidation | Offline Dream consolidation worker | Hot/cold split is standard batch engineering: accumulate in-session ephemeral state, replay/consolidate in an offline batch. The neuroscience insight is WHAT to consolidate: replay traces → prune weak paths, renormalize, distill recurrent sequences. | Hippocampal replay drives systems consolidation: fast episodic traces → slow neocortical gist summaries (Born & Rasch 2019). Distillation is the analog. |
| 13 | Synaptic homeostasis | Global weight renormalization | Bounded total weight mass: prevent any single skill from monopolizing all activation. Applied lazily after consolidation. | Synaptic scaling globally downscales weights while preserving relative strength ratios (Tonegawa 2012, Turrigiano). |
| 14 | Silent engrams (access ≠ storage) | Diagnostic distinction for dead-skill detection | A skill stored but never retrieved is a *retrieval-cue problem* (bad description, triggers, or context), not a *storage problem* (bad procedure). Re-description targets the root cause. Powers the `flag_dead` diagnostic. | Optogenetic engram identification: sparse ensembles are necessary/sufficient for recall. Amnesia is often retrieval failure, not trace destruction (Tonegawa lab 2012–2015). |

### Class B: Harmless Mnemonics (Keep as Flavor)

These are accurate analogies but not engineering-load-bearing. They aid intuition but aren't cited as justification.

| # | Mechanism | Status | Notes |
|---|---|---|---|
| 2 | LTP / LTD → bidirectional weights | Keep, merged into #1 | Same Hebbian rule, sign reversed. Not a separate row. |
| 8 | Spacing effect → diminishing massed updates | Keep (minor) | Rate-limiting repeated updates within a short window. Low-impact but harmless. |
| 11 | Engram overlap → session co-occurrence edges | Keep, merged into #1 | Same Hebbian machinery on a temporal-link edge type. Helps model workflows. |
| 17 | Representational drift → embedding refresh | Keep (decorative) | Real justification: embedding-model version drift, content edits, changing feature importance. Recompute periodic ally. Mnemonic works well. |

### Class C: Reframe or Kill (Replace with Engineering Names)

These rows conflate mechanism with motivation in ways that mislead or are unverified.

| # | Mechanism | Verdict | Rationale |
|---|---|---|---|
| 4 | Behavioral tagging → retroactive credit to all tagged skills | **REFRAME** — Hypothesis, ablation-required | Uniform retroactive credit is the classic credit-assignment problem: rewards bystanders. Refined rule: uniform credit + salience threshold + recency-weighted credit. Flag as experimental; test in doc 07 ablations. |
| 5 | Reconsolidation → update-on-read | **REFRAME** — See Principle 1 below | As written, contradicts row 6 / rule 7. Redefined: recall re-opens the record for inspection; revision requires evidence. Applies only to R (bookkeeping), not S (weights). |
| 10 | Engram allocation / excitability → exploration bonus | **REFRAME** — Mechanism retained, biology dropped | Real justification is explore-vs-exploit (UCB-style optimism bonus) — a mature non-biological literature. Keep the mechanism (new skills get a bonus, decaying with exposure); drop the neuroscience motivation. |
| 15 | Astroengrams → context nodes | **REFRAME** — RENAME, drop the frontier-literature dependency | Context nodes (projects, toolchains, error classes) participate in activation as bipartite feature nodes — standard GNN design. Drop the dependency on the unverifiable 2026 astroengram literature. Mechanism kept, terminology updated. |
| 16 | Multi-synaptic boutons → typed multi-edges | **KILL the analogy, keep the design** | A typed-property multigraph needs no biological justification. Citing Uytiepo 2025 (unverifiable) as motivation actively misleads. Implement typed edges; drop the biology reference. |
| 18 | Engram reprogramming → weight reset/repair ops | **REFRAME** | Admin silence/restore operations are justified by operability + audit trail (archived-never-deleted); 2026 reversibility literature is inspiration only. Keep ops; drop citations. |

---

## Design Principles Elevated from the Translation

### Principle 0: "Never Learn from the Hot Path Alone"

[DECISION D1] **KEEP, elevate to P0 design principle. Confidence 90%.**

The hot path (per-query routing, per-event signaling) is where **noisy, adversarial, transient signals live**. Online learning from noisy single sessions is fragile:

- A confused agent may call `signal_outcome(+1)` for a skill that failed.
- A single negative example should not flip a weight trained on hundreds of successes.
- Transient network blips, timeout errors, and user cancellations are noise.

**Principle:** Durable weight changes (to S) happen ONLY in the offline Dream cycle, after outcomes are logged and verified. Hot-path signals set **ephemeral tags** and boost **short-term retrieval strength (R)** — both are reversible and expire naturally.

**Benefit:** Separates cheap, reversible in-session state from outcome-gated commits. Makes the system robust to single-session noise and mild adversarial confusion. Allows metaplastic saturation and rate-limiting to work without deadlocking the learning loop.

**Binding:** This principle structurally underpins the three-tier state model (Section below) and D3's fidelity-weighted learning (doc 05). It is architectural, not tunable.

---

### Principle 1: "Retrieval is a Write to the Ledger, Not to the Weights"

[DECISION D4–P2] **REFRAME with a guardrail. Confidence 87%.**

The exploratory corpus stated: "Retrieval is a write" (consolidated-research-graph-mcp-proposal.md:122). This is true but overbroad and harbors an internal contradiction:

- Rule 6 ("update R for every node route() returns") plus
- Rule 7 ("never update on listing") 
- → Contradiction: returning candidates IS listing.

**Unguarded consequence:** Returned skills boost R → returned more frequently → eventually dominate routing (display bias, the exact problem the engine should solve).

**Refined Principle 1:**

> Retrieval updates the *ledger* (last_activated, exposure counts) and drives R only on *confirmed application*. **Storage strength (S) moves only on outcome capture.** The reconsolidation idea survives: recall re-opens the record for revision — but revision requires evidence.

**Specific rules:**
1. `route(query)` updates: exposure_count, last_activated, community-frequency stats. Does NOT change R or S.
2. `signal_use(skill_ids)` (called when skill is actually applied): nudges R upward, sets ephemeral tags for co-activation edges. Does NOT change S.
3. `signal_outcome(valence, ...)` (called when task outcome is verified, off-path): captures tags → Δw ∝ outcome. Moves S, supervised by Dream consolidation.

**Benefit:** Eliminates display bias. Enables the testing effect (application ≠ listing). Makes outcome signals the locus of learning, not retrieval.

---

## The Three-Tier State Model (Fixes FINDING-009)

[DECISION D1] Plasticity state is split into three tiers by timescale, location, and writer.

**Context:** The exploratory corpus modeled S and R per-EDGE (proposal :230) but stored only node-level S in the file (engram-format :63), leaving edge-level learned state homeless—undermining the "rebuildable index" claim (engram-format :172).

**[VERDICT] Layered model. Confidence 85%.**

The contradiction resolves by distinguishing **two distinct S variables**:
- **S_node:** Node-level storage strength, gates the lifecycle FSM (nascent → consolidated). Checkpointed to file.
- **S_edge:** Edge-level association strength, drives routing propagation. Consolidated edges checkpointed to file; unconsolidated edges ephemeral in DB.

| Tier | State | Location | Writer | Analogy | Lifespan |
|---|---|---|---|---|---|
| **A (durable, node)** | S_node, outcome counts (lifetime successes/failures), per-step stats, status | `.egr.md` frontmatter | Dream checkpoint only | Cortical consolidated trace | Sessions → years |
| **B (durable, edge)** | Consolidated learned edges: S_edge (association strength), type, evidence counts, provenance | NEW `synapses:` frontmatter block in `.egr.md` (one per engram, declaring learned outbound edges that pass consolidation threshold) | Dream checkpoint only (edge consolidation = promotion DB → file) | Cortical trace (edges) | Sessions → weeks |
| **C (ephemeral, edge)** | R (all of it), tags, candidate/sub-threshold edges, cached embeddings | SQLite DB (ephemeral tables) | Hot path (`route`, `signal_use`, `signal_outcome`) | Hippocampal fast trace | Session scope (~hours) |

### Placement Detail: Bidirectional Mirror

**Directed edges:** If engram A has learned an outbound edge to B (A → B, `composes` or `co_activation`), the edge is consolidated into A's `synapses:` block during Dream consolidation.

**Symmetric edges (co-activation reflection):** If A and B co-activated heavily, both edges (A → B and B → A) reflect the same strength by design. Dream consolidation writes both directions to maintain symmetry. Consistency is enforced by having Dream as the only writer.

**Dangling targets:** If an engram references a skill that hasn't yet registered (e.g., `needs: [unknown-skill]`), the edge is degraded to an inert placeholder until resolution. Same semantics as unresolvable declared dependencies (engram-format :168).

### Rebuildable Index: Precise Definition

[VERDICT] The rebuildable-index claim is preserved under a precise definition:

> **Rebuild ≡** registering/syncing engram files reconstructs all durable state (Tier A + B).
>
> Only fast-decaying ephemeral state (Tier C: R, tags, unconsolidated candidates, cached embeddings) is lost on rebuild. That loss is semantically equivalent to a period of disuse — the index recovers R and candidate edges through normal routing and tagging as sessions resume. **The loss is safe.**

This mirrors the corpus's own systems-consolidation analogy (hippocampal fast trace vs cortical checkpoint) and makes it engineering-true: **the DB is a rebuildable cache, not the source of truth for durable learned weights.**

### Impact: File Churn & Mitigation

[RISK] Dream consolidation may write to many engrams on every run (edge promotion + embedding refresh + status updates).

**Mitigation:** Apply consolidation thresholds + epsilon-hysteresis. Only promote edges that moved materially (e.g., \(\Delta S > \epsilon = 0.05\)). Only rewrite embeddings if drift detected. Write-gate on threshold-crossing, not on every update. Target: <5% of registry edges written per Dream run under steady-state conditions.

---

## Event Pipeline and Update Rules

### Phase 1: Hot Path (Cheap, Ephemeral)

```
Session starts
  → SessionStart hook: inject top-m "hot skills" (highest R) as priming

Agent applies skill_i
  → signal_use(skill_i) [or implicit via hook]
     └─ Tag skill_i with expiry ~τ_session
     └─ For each co-active skill_j in this session window:
        └─ Create candidate edge (i, j, type=co_activation) if not exists
        └─ Tag the edge

Task outcome verified (e.g., test passes, user accepts)
  → signal_outcome(valence ∈ [-1, 1], salience?, skill_ids?) [hook or explicit]
     └─ Find all tags still alive (age < τ)
     └─ Capture: mark for Dream consolidation
     └─ Log to session trace (audit trail)

Session ends
  → Episodic trace logged to Dream input
```

### Phase 2: Offline Consolidation (Dream Cycle)

```
Dream cycle triggered (cron / manual consolidate() / idle batch)
  1. Replay session traces
     └─ Identify successful paths (high valence + no failures)
     └─ Identify high-salience outcomes (user rated highly, test covered >80%)

  2. For each captured edge:
     └─ Compute Δw = η(1 - w/w_max) × avg_outcome × recency_weight
     └─ Apply spacing: massed repeats within Δt < τ_s get scaled by (1 - e^{-Δt/τ_s})
     └─ Commit to DB if Δw > threshold_consolidate

  3. Decay R and tags:
     └─ R(t) ← R_0 × e^{-λ_R × age}  [fast decay, days-scale]
     └─ S(t) ← S_0 × e^{-λ_S × age}  [slow decay, weeks-scale]
     └─ Delete expired tags (age > τ_session)

  4. Renormalize (homeostasis):
     └─ Total weight ← sum of all edge weights
     └─ If total > bound, scale all by bound/total
     └─ Preserve relative strength ratios

  5. Distill composite candidates:
     └─ Paths traversed ≥N times with consistent outcomes and no single engram covering them
     └─ Draft composite engram via local model (trace IR → rendered .egr.md)
     └─ Quality gate: reconstruction check + rubric assessment
     └─ If pass: insert with provenance=distilled, status=nascent

  6. Audit and flag:
     └─ Silent engrams: stored but never retrieved in last T sessions → flag_dead report
     └─ Black-hole hubs: nodes with usage PageRank > threshold → penalize in routing, flag for review
     └─ Coverage gaps: `needs` with no provider engram → nucleation candidates
     └─ Orphans: `yields` with no consumer → pruning candidates

  7. Checkpoint DB → file:
     └─ For each engram:
        └─ S_node, outcome counts, per-step stats, status → .egr.md frontmatter
        └─ Consolidated edges (S_edge > threshold) → .egr.md synapses: block
        └─ Refreshed embedding ref (if changed) → routing.embedding block
        └─ Append entry to Provenance journal
     └─ Set last_checkpoint timestamp
```

### Key Update Rules

1. **Two-phase capture:**
   ```
   tag.set(skill, session_id, expires=now + τ_session)
   ...
   if signal_outcome(valence) and outcome_high_confidence:
       for tagged_skill in alive_tags:
           Δw ← η(1 - w/w_max) × outcome × recency(skill)
           commit_to_db(Δw)  # deferred until Dream consolidation
   ```

2. **Behavioral tagging (retroactive credit):**
   ```
   if |valence| > θ_salience:  # high-salience outcome
       capture(all_tags_alive_in_window)  # all skills in last T minutes
   else:
       capture(explicitly_listed_skills)  # only if caller specified
   ```

3. **Metaplastic saturation:**
   ```
   η_effective = η × (1 - w / w_max)
   Δw = η_effective × outcome × co_activation_strength
   ```

4. **Spacing effect:**
   ```
   increment_scale = 1 - exp(-Δt / τ_spacing)
   Δw ← Δw × increment_scale
   # repeated updates within τ_spacing get increasingly dampened
   ```

---

## Forgetting and Decay as First-Class Policy

Two timescales operate independently:

### Fast Decay: Retrieval Strength (R)

**Timescale:** Days to weeks. **Mechanism:** Exponential: \(R(t) = R_0 e^{-\lambda_R \Delta t}\) with \(\lambda_R \approx 0.1/\text{day}\).

**Semantics:** Tracks recency. A skill applied yesterday is more likely to be relevant today; a skill last used 3 months ago is fading. R boosts retrieval but does not trigger durable weight changes.

**Application:** High R skills are injected into SessionStart (priming). In routing, score ∝ activation + R + excitability.

### Slow Decay: Storage Strength (S)

**Timescale:** Weeks to months. **Mechanism:** Exponential: \(S(t) = S_0 e^{-\lambda_S \Delta t}\) with \(\lambda_S \approx 0.01/\text{day}\).

**Semantics:** Tracks cumulative evidence. A skill with many confirmed successes maintains high S even if unused; a skill with many failures decays. S gates lifecycle transitions (S ≥ θ_consolidate → nascent to probation).

**Application:** Consolidation thresholds, promotion/archival decisions, distillation priority.

### Revival and Archival

- **Revival:** An archived skill can be re-promoted if new evidence accumulates (new session traces, manual feedback). Version history is preserved; no deletion.
- **Decay floor:** Skills reaching S < floor_archived are moved to archived status automatically by Dream. They remain in the registry (git history) but are excluded from routing.
- **Forgetting policy:** The filesystem is the archive. Dream never deletes `.egr.md` files; it moves them to `.spectra/archive/` with a timestamped suffix and updates the Provenance journal. Rollback is possible by restoring from the archive.

---

*Section authored by IDG, 2026-08-14, drawing on exploratory/consolidated-research-graph-mcp-proposal.md Part II.2–II.3, Part IV.4, and engram-format.md §2.*
