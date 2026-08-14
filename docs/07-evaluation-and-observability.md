# 07. Evaluation and Observability: Fitness, Benchmarks, and Telemetry

**Status:** Draft-refined / v1  
**Provenance:** exploratory/consolidated-research-graph-mcp-proposal.md Part IV.8–IV.10, engram-format.md §3–§4, FINDING-004, D1 hypothesis register (doc 01)  
**Decisions implemented:** Fitness functions for lifecycle gates; system-level evaluation plan; per-tier signal-yield measurement; KPIs; ablation suite

---

## Overview

Magicite is a learning system: its core claim is that **skills improve through use**. This section specifies:

1. **Per-skill fitness functions** — how we measure "improved"
2. **System-level evaluation plan** — benchmarks, baselines, metrics, ablations
3. **Observability** — what signals to log and how to report on them
4. **Hypothesis testing** — how each 2026-source claim is validated or falsified

---

## Per-Skill Fitness Functions

[D5 rank 1] A skill is "improved" when it demonstrates evidence of learning: higher success rate, lower user friction, broader applicability.

### Lifecycle Gate: Nascent → Probation

**Gate requirement:** Reconstruction check (engram procedure reproduces held-out traces) + Rubric assessment (LLM scores against quality rubric).

**Fitness metrics:**
- **Reconstruction success:** Can the engram's procedure reproduce ≥80% of the traces used to induce it?
- **Rubric score:** Triggers (≥3 positive, ≥1 negative, distinct), Procedure (clear steps, no circular dependencies), Pitfalls (grounded in observed failures), Examples (positive + negative cases present).
- **Injection-risk check:** No obvious prompt-injection vectors; exec blocks (if present) are benign or marked for manual review.

**Gate passes if:** Reconstruction ≥80% AND Rubric score ≥ 8/12 (two-thirds pass).

---

### Lifecycle Gate: Probation → Consolidated

**Gate requirement:** Evidence of real-world application and success.

**Fitness metrics:**
- **Storage strength:** S_node ≥ 0.6 (cumulative success signal)
- **Pass rate:** outcome_count.success / (outcome_count.success + outcome_count.failure) ≥ 0.9 (90%)
- **Minimum sessions:** ≥ 3 independent sessions applied the skill with positive outcome
- **No recent high-salience failures:** Last 5 applications did not include catastrophic failures (valence < -0.7)

**Gate passes if:** S ≥ 0.6 AND pass_rate ≥ 0.9 AND sessions ≥ 3.

---

### Lifecycle Gate: Consolidated → Promoted

**Gate requirement:** Long-term stability and broad adoption.

**Fitness metrics:**
- **High storage strength:** S_node ≥ 0.85
- **Sustained pass rate:** outcome_count.success / (success + failure) ≥ 0.98 (98%)
- **Wide adoption:** ≥ 10 independent agents/sessions applied the skill
- **No evidence decay:** Last 30 days of signals are similar in quality to earlier signals (no sudden drop in valence)

**Gate passes if:** S ≥ 0.85 AND pass_rate ≥ 0.98 AND adoption ≥ 10 agents AND no recent decay.

---

### Sharpening Quality Gate

When Dream sharpens a skill (rewrites a step, adds triggers, updates pitfalls), the result is re-evaluated:

**Before sharpening:**
- Confidence on affected step(s): [ok: 25/30]

**After sharpening:**
- Re-test the sharpened procedure on held-out traces
- Confidence should improve: [ok: 28/30] or better
- If confidence degrades: revert and flag for manual review

**Fitness:**
- Sharpening succeeded if: post-confidence > pre-confidence OR confidence already ≥ 29/30 (ceiling)

---

## System-Level Evaluation Plan

[D5 rank 1] Measure whether the engine achieves its core claim: **skills route better and improve over time**.

### Benchmark Assembly

**Source 1: Internal registry**
- Assemble queries from your actual skill usage (anonymized if needed)
- Ground truth: which skills were actually invoked (manually labeled as correct)
- Minimum 50 queries for statistical significance; target 200+

**Source 2: Public SkillsBench derivative (Comparability)**
- SkillRouter paper uses a SkillsBench-derived benchmark; use the same or compatible set
- Enables cross-comparison with published baselines
- Minimum 100 queries

**Source 3: Compositional queries (CompSkillBench-style)**
- Multi-step tasks requiring skill sequences
- Ground truth: the correct skill order + composition plan
- Minimum 20 queries

**Total benchmark size:** 150–320 queries, stratified by skill category and complexity.

---

### Four Baselines (A–D)

Test the cumulative effect of each design decision:

**Baseline (a): Native SKILL.md matching (status quo)**
- Harness in-context judgment: LLM matches query against all skill descriptions (SKILL.md descriptions only)
- No learned routing, no graph structure
- **Hypothesis:** This is the weakest baseline (H-BODY predicts −31–44pp vs body-aware routing)

**Baseline (b): Dense embedding retrieval (no learning)**
- Embed query + embed skill body; retrieve by cosine similarity
- No graph, no plasticity, no learned weights
- Rerank via BM25 if desired
- **Hypothesis:** This is better than (a) due to body content, but still static

**Baseline (c): Embedding + graph (no plasticity)**
- Same as (b), plus static-weighted graph edges (declared only: composes, inhibits, similarity from embeddings)
- No learned weights, no outcome signals
- Use spreading activation with fixed edge weights
- **Hypothesis:** Hierarchy (Leiden communities) helps but without learning gains are limited

**Baseline (d): Full Magicite (embedding + graph + plasticity)**
- Embedding + spreading activation + learned edge weights (S_edge)
- Outcome-gated updates + Dream consolidation
- Hook signals (Tier-2) if available; fall back to Tier-1 / Tier-0
- **Hypothesis:** This is the best (validates H-LEARN)

---

### Metrics

#### Ranking Metrics
- **Hit@1:** Is the correct skill in the top 1? (% of queries)
- **Hit@3:** Is the correct skill in the top 3? (% of queries)
- **Hit@5:** Is the correct skill in the top 5? (% of queries)
- **MRR (Mean Reciprocal Rank):** Average of 1/rank for each query
- **NDCG (Normalized Discounted Cumulative Gain):** Rank-weighted correctness

#### Composition Metrics (for multi-step tasks)
- **Plan F1:** Precision & recall of the skill sequence (correct skills in correct order)
- **Plan Success Rate:** Did the composed plan achieve the task goal?

#### Efficiency Metrics
- **Latency:** Query embedding + spreading activation + scoring time (target: <100ms)
- **Token cost:** L1 metadata returned per query (~100 tokens × k)
- **Storage:** SQLite DB size, registry .egr.md file size

---

### Experimental Protocol

**Phase 1: Offline Benchmark (Baselines A–D)**

```
For each baseline (a, b, c, d):
  1. Initialize skill graph with exploratory corpus (or smaller test set)
  2. Split benchmark into train (60%) + test (40%)
  3. For baseline (a)–(c): routes are fixed (no learning)
  4. For baseline (d): 
     a. Phase 1a (no learning): run test set → measure Hit@k
     b. Phase 1b (with learning): run train set (with simulated/real outcome signals) 
        → consolidate → re-run test set → measure Hit@k again
  5. Report: Hit@1/3/5, MRR, NDCG, latency, F1 for compositions

Expected result (from FINDING-005):
- (a) is lowest (status quo)
- (b) is +15–20pp over (a)
- (c) is +5–10pp over (b)
- (d) is +10–20pp over (c), validating H-LEARN
```

**Phase 2: Online Learning (Ablations)**

Test specific design choices:

| Ablation | Change | Hypothesis |
|---|---|---|
| No decay | Set λ_R = λ_S = 0 | Without forgetting, old signals dominate; accuracy plateaus or degrades |
| No tag-capture | Learn on hot path (every signal) | Noisy single-session signals → instability, overfitting |
| No communities | Flat routing (no Leiden) | Accuracy vs registry size degrades logarithmically (H-SCALE predicts this) |
| No inhibition | Remove learned inhibits edges | False positives (two similar skills both returned) increase |
| No behavioral tagging | Credit only direct, explicit skills | Composite-skill detection fails; lower plan F1 |
| No metadata body | Embed triggers only, not full SKILL.md | Validates H-BODY (predicts −31–44pp loss) |

**Run each ablation:**
1. Train on full train set with ablation applied
2. Test on test set
3. Compare Hit@k, MRR, F1 vs baseline (d)
4. Measure Δaccuracy (degradation if ablation is important)

---

## Per-Tier Signal-Yield Measurement

[D3 [ASSUMPTION]] Tier-1 (tool-mediated) signals are hypothesized to outperform Tier-0 (passive inference) by material margin (assumption: agents follow instructions often enough).

### Measurement Protocol

**Instrument signals to track provenance:**

```
Every Δw update logged as:
{
  timestamp: <now>,
  signal_tier: inferred | self_reported | hook_verified,
  skill_id: <egr_id>,
  valence: <-1.0 to 1.0>,
  salience: <0 to 1>,
  session_id: <uuid>,
  outcome: success | failure | neutral
}
```

**Aggregate by tier over a test period (e.g., 1 week of agent usage):**

```
Tier-0 (inferred):
  - Count: N_0 signals
  - Avg valence: μ_0
  - Avg |Δw| contributed: ρ_0
  - Precision (outcome later confirmed): P_0

Tier-1 (self_reported):
  - Count: N_1 signals
  - Avg valence: μ_1
  - Avg |Δw| contributed: ρ_1
  - Precision: P_1

Tier-2 (hook_verified):
  - Count: N_2 signals
  - Avg valence: μ_2
  - Avg |Δw| contributed: ρ_2
  - Precision: P_2 (≈ 1.0 by design)
```

**Hypothesis test:**
- Tier-1 yield ρ_1 > 0.8 × ρ_2 (Tier-1 is ≥80% as valuable as Tier-2)
- Tier-0 yield ρ_0 > 0.3 × ρ_2 (Tier-0 is ≥30% as valuable as Tier-2)

**Outcome:** If both pass, the fidelity ladder is validated. If Tier-1 fails, agents are not following instructions; fall back to Tier-0 + passive inference. If Tier-0 fails, adjust inference thresholds (currently conservative).

---

## KPIs: Standing Observables

Run these continuously in production:

### Hit@k vs Registry Size (The Scaling Law Test)

**Metric:** For all queries routed in the last N days, compute Hit@k binned by registry size at query time.

```
Registry size: 10–50 skills   → avg Hit@3 = 85%
Registry size: 50–100 skills  → avg Hit@3 = 78%
Registry size: 100–200 skills → avg Hit@3 = 70%
Registry size: 200+ skills    → avg Hit@3 = ?
```

**Expected behavior (from H-SCALE):**
- Flat routing: log-decay curve (Hit@k = c − k log(N))
- Hierarchical routing: sub-logarithmic or flat curve

**Passing criteria:** Slope with hierarchy is <50% of slope without hierarchy.

---

### Silent Engram Report (Operational Diagnostic)

**Query:** `flag_dead()`

**Output:** Skills stored but never retrieved in last 30 days.

**Action:** Review triggers and descriptions; re-sharpen or archive.

**KPI:** Percentage of registry flagged silent. Target: <10% (if >20%, indicates routing cues are systematically poor).

---

### Skill Fitness Distribution (Health Check)

**Metric:** Histogram of S_node values across all engrams.

```
S < 0.3:  nascent/failed (target: <10% of registry)
0.3–0.6:  probation (target: 10–20%)
0.6–0.85: consolidated (target: 60–70%)
>0.85:    promoted (target: 5–10%)
```

**Passing criteria:** Majority in consolidated+promoted range (>65%).

---

### Black-Hole Hub Detection (Pathology Monitor)

**Metric:** Nodes with usage-graph PageRank > 95th percentile.

**Action:** Flag for review; these skills may be overly general (capturing too much routing).

**KPI:** Percentage of traffic routed through top-5 skills. Target: <30% (if >50%, indicates hub concentration problem).

---

## Honest Limits (from Exploratory Corpus, Carried Forward)

[From exploratory/consolidated-research-graph-mcp-proposal.md:303–308]

1. **Feedback loops.** The engine is vulnerable to adversarial confusion (a model mistakenly calls `signal_outcome(+1)` for a failed skill). Mitigated by two-phase commit, metaplastic saturation, per-tier signal caps, and the need for accumulated evidence before durable updates. **Not eliminated, only bounded.** Evaluation must include robustness tests (e.g., adversarial signal noise injection).

2. **Outcome signal quality.** Hooks approximate "success" via exit codes, user confirmation, or test output. Noisy valence degrades plasticity. Start conservative (capture only on explicit/high-confidence signals). Measure signal precision in doc 07 evaluation.

3. **Cold start.** New skills route via similarity edges + excitability bonus until usage data accrues. Expect ~3–5 sessions to stabilize, not zero-shot parity. Reflected in the nascent→probation→consolidated progression.

4. **Scale ceiling of the analogy.** At 10³ nodes, all computation is trivial (PPR, Leiden are linear-time). The neuroscience analogies buy *dynamics* (plasticity, two-tier learning), not capacity. **If the registry stays under ~50 skills, native SKILL.md matching is fine and Magicite is overhead.** The engine pays off exactly where the scaling law says native routing breaks (H-SCALE).

---

## Test-Driven Development Roadmap

**Confidence gates for each design element:**

| Element | Confidence | Test | Owner |
|---|---|---|---|
| H-BODY (body content improves routing) | 85% | Baseline (b) vs (a); SkillsBench-compatible benchmark | Eval phase 1 |
| H-SCALE (hierarchy flattens log-decay) | 88% | Baseline (d) Hit@k vs registry size; compare to ablation "no communities" | Eval phase 1 + 2 |
| H-COMPOSE (composition planning works) | 82% | Plan F1 metric on CompSkillBench-style queries | Eval phase 1 |
| H-LEARN (plasticity improves routing) | 86% | Baseline (d) learning curve; ablation "no tag-capture" | Eval phase 1 + 2 |
| D1 (Three-tier state model) | 85% | Verify no weight loss on rebuild; test Tier B edge persistence | Integration test |
| D2 (Local-first core, deployment profile) | 88% | Stdio/SQLite implementation; verify tool contracts are transport-agnostic | Implementation test |
| D3 (Tier-0/1/2 signal fidelity) | 82% | Per-tier signal-yield measurement; Tier-0 works on hookless hosts | Eval + integration |
| D4 (Analogy table verdicts) | 86% | Each ablation maps to a row; validate keep/kill/reframe decisions | Ablation suite |
| Principle 0 (Never learn from hot path alone) | 90% | Ablation "no tag-capture"; verify two-phase commit stability | Ablation |
| Principle 1 (Retrieval is a write to ledger) | 87% | Verify R never changes on route(), only bookkeeping updated; S never changes on signal_use() | Unit test |

---

## Open Questions for Future Work

1. **Multi-agent signal merging:** How do we confidently combine signals from different agents/models with different reliability profiles? Current: treat all self_reported equally. Future: agent-specific calibration or Bayesian priors.

2. **Concept drift in embeddings:** As embedding models evolve (new versions, new training data), how do we detect and handle drift in cached embeddings? Current: refresh on Dream cycle. Future: continuous drift detection + proactive refresh.

3. **Compositionality depth:** How deep can composition DAGs go (how many skills in sequence)? Current: no limit in spec, but evaluation untested at depth >5.

4. **Forgetting policy for promotion:** Should promoted skills have a slower decay rate? Or should they never decay? Current: same decay for all. Future: separate decay curves by status.

---

*Section authored by IDG, 2026-08-14, implementing D5 rank 1 and carrying forward ablation/measurement framework from exploratory corpus.*
