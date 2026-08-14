# ATLAS — Autonomous Topological & Lexical Acquisition Strategy

> **A** ssess · **T** raverse · **L** ocate · **A** bstract · **S** ynthesize
>
> A tooling-independent methodology for Explorer/Scout agents that map and
> plan inside unfamiliar codebases **before** any mutation occurs.
>
> *ATLAS is read-only by construction. If you are writing code, you are not in ATLAS.*

> **Eidolon release:** v1.13.0 (see `CHANGELOG.md`) · **Methodology spec:** v1.0
> (breaking-change axis, tracked separately — see §7 Versioning).

---

## 0. Scope and non-goals

**In scope.** Long-horizon, read-dominant discovery of a software system:
topology, symbol relationships, entrypoints, test surfaces, integration seams,
and decision-ready plans. Output is a structured *scout report* consumable by
planning agents (e.g. SPECTRA) and implementation agents (e.g. APIVR-Δ).

**Out of scope.** File mutation, migrations, deployments, running arbitrary
writes against databases or external systems. ATLAS will refuse those and hand
off. It is a Plan-Mode methodology in the Claude Code / Cursor sense.

---

## 1. Architectural invariants

These are **mechanical** constraints enforced by the harness, not model-level
requests. They survive model swaps.

| # | Invariant | Enforcement point |
|---|-----------|-------------------|
| I-1 | Read-only tool surface | Harness exposes only `view_file`, `search_symbol`, `search_text`, `list_dir`, `graph_query`, `test_dry_run`, `memex.read`. No `edit`, `shell.write`, `git.commit`. |
| I-2 | Bounded ACI | `view_file` caps at ≤100 lines per call; `search_text` caps at ≤50 matches; `list_dir` caps at ≤200 entries. Overflow returns a pagination cursor. |
| I-3 | 90/10 deterministic/probabilistic | Symbol lookup, AST/Tree-sitter queries, and `grep` are tried **before** any LLM-authored search. LLM inference is the synthesis layer, not the retrieval layer. *Graph-first corollary:* relational sub-questions and the Scatter-Gather (§2.3.1) partition are derived from deterministic `graph_query` slices, not LLM-guessed clustering — pushing more Locate work onto structural probes raises search-efficiency η. |
| I-4 | Operator pattern | Subagents run in ephemeral contexts. They return one structured `finding` object. Their raw transcripts are never merged into the parent context. |
| I-5 | AgentFold at phase boundaries | Trajectory is `branch`/`return`-folded at every phase transition. Raw excerpts go to Memex; only an indexed summary remains in working memory. |
| I-6 | Telemetry-driven compaction | Harness tracks `context_used_pct`. At ≥60% it triggers an asynchronous fold. At ≥85% it halts and forces a checkpoint. |
| I-7 | Evidence-anchored claims | Every claim in the scout report carries `path:line_start-line_end` + confidence tier (`H`/`M`/`L`). Unanchored claims fail validation. |
| I-8 | Stop conditions are explicit | Decision-quality target is declared in the mission brief. ATLAS halts when target is reached; it does not keep exploring. |
| I-9 | ECL-conformant handoffs | Phase S MUST emit a v2.0 envelope sidecar (`scout-report.envelope.json`) adjacent to the scout-report. Envelope MUST satisfy the ECL v2.0 envelope schema (`schemas/ecl-envelope.v2.json`) and the per-Eidolon scout-report profile (`schemas/scout-report-profile.v1.json`). The envelope carries an `ise` block (`assertion_grade: "self-attested"` — ECL v2.0 §6.5) declaring the scout-report's provenance and the receiver's auto-route/auto-merge/auto-deploy authorization. Envelope is a terminal Phase-S artefact (same class as `scout-report.md`) — NOT a tool — preserving the I-1 read-only invariant. |

**Why mechanical?** Model-level instructions alone are insufficient under
long-horizon context rot. The SWE-agent, Claude Code Plan Mode, and AgentFold
literature converge on the same finding: reliability is a property of the
harness, not the model.

---

## 2. Phase specification

Each phase has: inputs, outputs (artifact), hard constraints, exit criteria.
Artifacts are **fill-in-the-blank templates** with enumerable IDs. Narrative
instructions produce inconsistent outputs; structural templates produce
machine-checkable ones.

### 2.1 Phase A — Assess

**Purpose.** Convert an ambient user goal into a bounded mission with a
declared decision-quality target and a token budget.

**Inputs.**
- User prompt
- Repository root + read-only credentials
- Parent-agent handoff context (if any)

**Outputs.** `mission.md` (see `templates/mission-brief.md`).
Required fields:

- `MISSION-ID`
- `GOAL` — one-sentence imperative
- `DECISION_TARGET` — what downstream question must be answerable (e.g.
  *"List all FlowObjects that write to `cast_vote_records` and their
  authorization paths"*)
- `SCOPE_INCLUDE` / `SCOPE_EXCLUDE` — path globs
- `BUDGET` — `max_tool_calls`, `max_tokens_input`, `max_wall_clock_s`
- `STOP_CONDITIONS` — enumerated
- `ESCALATION_TRIGGERS` — enumerated patterns that halt-and-ask

**Hard constraints.**

- Refuse missions with no `DECISION_TARGET`. Ambiguous goals produce unbounded
  exploration; that is the single most common failure mode in Explorer agents.
- Refuse write-scoped verbs (`implement`, `fix`, `refactor`, `migrate`). Hand
  off to the appropriate agent.

**Exit criteria.** `mission.md` passes schema validation and user (or parent
agent) acknowledges it.

---

### 2.2 Phase T — Traverse

**Purpose.** Build a **structural** map of the repository before any semantic
interpretation. Deterministic, high-signal, cheap.

**Inputs.** `mission.md`, repository tree.

**Outputs.** `map.md` (see `templates/traversal-map.md`). Contains:

- `MAP-ROOTS` — entrypoints relevant to mission (routes, workers, CLIs, public APIs)
- `MAP-MODULES` — top-N modules by structural centrality within scope
- `MAP-GRAPH` — adjacency list of `{caller → callee}` edges within scope,
  derived from AST not LLM inference
- `MAP-HEATMAP` — churn + ownership overlay (from git log if available)
- `MAP-GAPS` — regions the structural index could not parse (confidence drop zones)

**Hard constraints.**

- **No LLM calls during Traverse.** This phase is pure deterministic tooling:
  Tree-sitter (or Prism for Ruby, `ast` for Python, etc.), `git log`, `rg`.
- If a symbol index or code-graph MCP server is available, it is the primary
  source. Fallback order: symbol index → Tree-sitter → `rg` → directory walk.
- Skip vendored/generated directories by default (`node_modules`, `vendor/bundle`,
  `tmp/`, `dist/`, `public/assets`).

**Exit criteria.** `MAP-ROOTS` is non-empty and every entry references a
concrete file. If `MAP-ROOTS` is empty, mission scope was wrong — return to
Assess.

---

### 2.3 Phase L — Locate

**Purpose.** Pursue specific mission-driven questions across the map using
bounded probes. This is where most tokens are spent, so it is where the
Operator pattern matters most.

**Inputs.** `mission.md`, `map.md`.

**Outputs.** `findings.md` — a list of `FINDING-XXX` records. Each:

```
FINDING-XXX
  claim: <one-sentence factual claim>
  evidence:
    - path: app/flows/vote_casting/record_vote.rb
      lines: 42-78
      excerpt_ref: memex://excerpt/ab3f21  # raw text lives in Memex, not here
  confidence: H | M | L
  supports_decision: [DECISION-TARGET or sub-question ID]
```

**Probe patterns.**

1. **Symbol probe** (deterministic, preferred):
   `search_symbol("CastVoteRecord")` → definition, references, subclasses.
2. **Structural probe:** `graph_query("callers_of:X")`,
   `graph_query("implementers_of:Interface")`.
3. **Lexical probe:** `search_text("pattern", scope=<glob>)` capped at 50.
4. **Windowed read:** `view_file(path, start, start+100)`; iterate via cursor.
5. **Scatter subagent** (when ≥2 independent questions exist):
   spawn ephemeral subagents, each with its own 10% token budget, returning
   exactly one `FINDING` record. Parent never sees subagent transcripts.

**Hard constraints.**

- Every probe records what it *ruled out*, not just what it found. "No
  callers outside module X" is a first-class finding.
- A probe that returns >50 matches is **not** refined with a bigger limit; it
  is replaced with a narrower symbol-level probe. Repeated overflow is a signal
  to revise the map, not to brute-force.
- Dead-end fixation guard: if three consecutive probes on the same
  sub-question return `confidence: L`, halt the sub-question and record it in
  `GAPS`.

**Exit criteria.** Every `DECISION_TARGET` sub-question has at least one
finding with `confidence ≥ M` *or* is documented in `GAPS` with rationale.

---

### 2.3.1 Phase L sub-mode — Scatter-Gather Locate (TRANCE-gated)

**Purpose.** Operationalize the Operator pattern (I-4) as a *first-class named
mode* for large surfaces. Scatter-Gather is the G1 TRANCE form: parallel
read-only fan-out of Locate probes across topologically-disjoint module
clusters, each in a clean-context subagent, merged back into `findings.md`.
Full mechanical contract: `skills/scatter.md`.

**Default-or-gated.** GATED, never default. Standard-tier Locate stays serial.
Scatter adds *parallelism only* — never a write tool, never a fresh budget,
never an escape from a refusal gate (I-1 stands; read/explore parallelism is
safe-by-construction because ATLAS never writes the codebase).

**Activation (both-flags rule).** Escalate to Scatter-Gather only when **both**
hold; either alone stays standard tier:

- **Surface size:** `map.md` scope exceeds **> 5 modules OR > 25 files**.
- **Disjoint sub-questions:** **≥ 2 topologically-disjoint** `DECISION_TARGET`
  sub-questions (different modules / concerns).

**Fan-out.** Partition is **deterministic** — derived from a single parent-side
`graph_query` call-graph slice (or `MAP-MODULES` centrality clusters), not
LLM-guessed. One sub-mission per disjoint cluster, **hard cap 5 branches**,
per-branch budget = `parent_remaining / N` (branch budgets sum to ≤ parent
remaining). Branches are scope-diverse, not N-identical.

**Sub-mission.** Each branch is an ephemeral clean-context subagent seeded with
ONLY its `map.md` scope-slice + one sub-question + its budget. It runs the
Tier 1–5 ladder and returns exactly one structured object
(`findings`, `gaps`, `ruled_out`, `telemetry`) — **no transcript** (I-4).

**Merge + dedup.** The parent renumbers branch-local IDs to a global namespace,
then dedups: findings matching on **(path, overlapping line-range)** merge into
one, keeping the **highest** confidence tier and **unioning** anchors +
`ruled_out` notes. Cross-branch **contradictions are NOT silently merged** —
they emit a `[DISPUTED]` finding at confidence `L` pending a follow-up probe.
The merged `findings.md` then flows into the **existing** Phase A clean-context
fold (§2.4) unchanged — no new aggregator.

**Stop condition.** Every sub-question has ≥1 merged finding at confidence ≥ M
OR a `GAP`. Scatter does not re-spawn (max recursion = 1, §6).

---

### 2.4 Phase A — Abstract

**Purpose.** Compress the trajectory into a dense, high-fidelity summary
without losing evidentiary grounding. This is the AgentFold step.

**Inputs.** `findings.md`, the raw Locate trajectory.

**Outputs.**

- **Working memory:** a ≤2000-token condensed index: the claims, their
  confidence tiers, and Memex pointers. No raw excerpts.
- **Stable memory (Memex):** every raw excerpt, keyed by hash, retrievable
  byte-exact via `memex.read(ref)`.

**Fold contract.** The fold summary MUST preserve:

1. Every `FINDING-XXX` id.
2. Every `path:line` anchor.
3. Every unresolved sub-question and its status.
4. Every `ESCALATION_TRIGGER` that fired.

It MAY drop: intermediate tool outputs, failed probes that produced no
finding, narrative reasoning. Those stay recoverable via Memex.

**Hard constraints.**

- If the fold summary cannot preserve items 1–4 within 2000 tokens, the
  mission was under-scoped. Halt and ask, do not truncate.
- The fold is written by a *different* prompt than the Locate phase, in a
  clean context seeded only with `findings.md` and `mission.md`. This prevents
  trajectory contamination.

**Exit criteria.** Fold summary validates against the contract above, with a
mechanical check (regex for IDs, regex for anchors).

---

### 2.5 Phase S — Synthesize

**Purpose.** Emit the scout report — the single artifact downstream agents
consume.

**Inputs.** Folded summary + `mission.md`.

**Outputs.**

- `scout-report.md` (see `templates/scout-report.md`) — primary structured report.
- `scout-report.envelope.json` (see `schemas/scout-report.envelope.json`) — ECL v2.0 envelope sidecar, emitted adjacent to the scout report. Schema: `schemas/ecl-envelope.v2.json` + `schemas/scout-report-profile.v1.json`. Carries an `ise` block (`assertion_grade: "self-attested"`; ECL v2.0 §6.5).

The envelope sidecar is a **terminal Phase-S artefact** in the same class as `scout-report.md` itself. It is NOT a tool call. Emitting it does not violate the I-1 read-only invariant.

`scout-report.md` sections:

1. **Mission recap** (copied verbatim from `mission.md`).
2. **Topology summary** (≤10 bullets derived from `map.md`).
3. **Answer to DECISION_TARGET** — the substantive deliverable. Every
   sentence carries a `FINDING-XXX` reference.
4. **Recommended next actions** — ranked list, each tagged with the downstream
   agent best suited (`→ SPECTRA`, `→ APIVR-Δ`, `→ human`).
5. **Risks & gaps** — enumerated, with confidence tier.
6. **Telemetry** — tokens consumed per phase, tool-call counts, fold ratio.

**Hard constraints.**

- Every factual statement maps back to a `FINDING-XXX`. Synthesis does not
  introduce new claims.
- No section exceeds 500 tokens. If it does, the mission should have been split.
- The envelope sidecar is a terminal artefact emission, not a tool. It is
  produced as a Phase-S output in the same way `scout-report.md` is produced.
  This classification is explicit so the I-1 read-only invariant is not
  misread as prohibiting envelope emission.

**Exit criteria.** Schema-validated scout report + schema-validated envelope sidecar + all `DECISION_TARGET`
sub-questions answered or explicitly marked unanswerable.

---

### 2.6 Delta re-scout (incremental mode)

**Purpose.** A READ-ONLY, evidence-anchored *re-run* that reuses a prior
scout-report + its Memex store + a git-diff range to re-probe **only** the
changed surface, carrying unchanged findings forward verbatim. Full mechanical
contract: `skills/rescout.md`.

**Honest scope.** The delta re-scout **narrows** the staleness penalty of the
always-on-live-index gap — it does **not close** it. ATLAS-as-a-separate-step
is intrinsic to its read-only-by-construction design; a true always-on index is
an atlas-aci runtime / nexus integration concern, not a methodology property
(see §0 non-goals). Do not represent the delta mode as a live index.

**Procedure.** (1) `CHANGED-SURFACE = git diff <prior_commit>..HEAD` files
intersected with the prior `map.md` `MAP-MODULES`; (2) re-run Phase T
deterministically over the changed surface ONLY (no LLM calls — Phase T rule);
(3) mark a prior `FINDING-XXX` **STALE** iff its anchored `path:line` range
intersects a changed hunk; (4) re-probe ONLY stale findings via the Locate
ladder; (5) carry forward UNCHANGED findings **verbatim from Memex**,
preserving provenance.

**Output.** `delta-scout-report.md` — same class as `scout-report.md` (reuses
`templates/scout-report.md` + `schemas/scout-report.v1.json`); its section-3
labels each finding **FRESH / UNCHANGED / RE-VERIFIED / NEWLY-STALE** with the
originating commit. Changed-surface files outside the prior scope are noted in
`MAP-GAPS` (they may warrant a full scout), never silently dropped.

**Read-only.** The diff is read via the deterministic Traverse ladder
(`git diff` / `git log` / `rg`), never a write tool (I-1).

---

## 3. Failure modes and mitigations

| Failure | Mechanism | Mitigation |
|---------|-----------|------------|
| Unbounded exploration | Missing `DECISION_TARGET`, loose stop conditions | Phase A refuses; hard budget in harness |
| Context rot | Linear transcript growth | Phase-boundary folds + 60% telemetry compaction |
| Hallucinated paths | Model inferring file structure | I-7: every claim needs `path:line`; validator rejects unanchored claims |
| Dead-end fixation | Repeated probes on same sub-question | Three-strike rule in Locate |
| Scatter-subagent context leak | Subagent returns raw transcript | Operator pattern: only structured `FINDING` records cross the boundary |
| Fold drops critical constraint | Summarizer compresses away a security rule | Fold contract enforces preservation of IDs/anchors/triggers; mechanical check |
| Tool definition bloat | Too many MCP tools loaded | Progressive disclosure: only Traverse-phase tools in Traverse context |

---

## 4. Portable abstractions

ATLAS is defined entirely in terms of five abstractions. Any concrete
implementation is valid if it provides these:

1. **ACI** — bounded read/search primitives (`view_file`, `search_symbol`,
   `search_text`, `list_dir`, `test_dry_run`).
2. **Code graph** — queryable structural index. Tree-sitter is the default,
   Prism/LSP/language-specific AST parsers are acceptable substitutes.
3. **Memex** — content-addressable store for raw excerpts. Implementable as
   sqlite-vec, a flat directory of hashed files, or any KV store.
4. **Harness telemetry** — token counters, phase timers, tool-call logs.
5. **Subagent spawner** — launches an ephemeral context with its own budget
   and a schema-typed return value.

None of these are vendor-specific. MCP is the recommended transport but not
required.

---

## 5. Evaluation

ATLAS implementations are evaluated on three axes:

- **Mission completion rate** — fraction of canary missions whose
  `DECISION_TARGET` was correctly answered (ground-truth comparison).
- **Search efficiency η** — tokens in relevant findings ÷ total tokens
  consumed. Target: η ≥ 0.25. Below 0.1 indicates Inference Trap.
- **Fold fidelity** — for each folded phase, what fraction of downstream
  answers still resolves correctly using only the fold summary? Target: ≥ 0.95.

Two mode-specific axes apply when the corresponding sub-mode runs:

- **Scatter fan-out efficiency** — `dedup_count / total_branch_findings` should
  be low on a *correctly disjoint* partition; a high value means the clusters
  overlapped (the trigger or the graph slice was too coarse).
- **Delta-recall** — for a delta re-scout (§2.6), the fraction of prior findings
  correctly labelled FRESH / UNCHANGED / RE-VERIFIED / NEWLY-STALE against
  ground truth, and the `reprobe_tool_calls vs full-scout estimate` saving (the
  quantified staleness-narrowing, not closure).

Canary dataset lives in `evals/canary-missions.md`. Benchmark hooks into
SWE-bench (repository-navigation subset) and `AgencyBench` when available.

---

## 6. Relationship to other agents

- **SPECTRA** (planning/spec) consumes ATLAS scout reports as input for spec
  generation. ATLAS is upstream.
- **APIVR-Δ** (implementation) consumes the `Recommended next actions`
  section. ATLAS never invokes APIVR-Δ directly; handoff is explicit.
- ATLAS is allowed to recurse once: Synthesize may spawn a follow-up ATLAS
  mission if it identifies a sub-question whose decision-quality target is
  cleanly separable. Max recursion depth: 1.
- **ESL discover hop (opt-in).** In an ESL-enabled consumer project
  (`.spectra/` present) with a change-worthy finding, Phase S loads
  `skills/esl-hop.md` and frames the emitted scout-report + envelope as a
  proposal to open an ESL change at `proposed`. ATLAS never calls a tonberry
  write verb itself; SPECTRA's own `esl-hop` owns `propose`/`specify` on
  receipt over the unchanged ATLAS→SPECTRA edge.

---

## 7. Versioning

This document is the ATLAS v1.0 specification. Breaking changes to the
phase contract, artifact schemas, or invariants require a minor-version bump
and a migration note.

Downstream implementations SHOULD declare ATLAS version compatibility in
their `agent.md` frontmatter.

ATLAS targets ECL v2.0 (declared in `ECL_VERSION`). ECL adoption is opt-in; live consumers MAY ignore the envelope sidecar without losing scout-report functionality.

## 9. Memory protocol (CRYSTALIUM)

ATLAS integrates with CRYSTALIUM to build persistent, cross-session codebase
intelligence. The pipeline is:

| Hook | Phase | Call |
|------|-------|------|
| **Recall** | A (Assess / mission intake) | `mcp__crystalium__recall(scope, query, k=5, layers=[semantic,episodic,procedural])` — surface prior maps, conventions, known traps before any structural work. |
| **Ingest** | S (Synthesize) | `mcp__crystalium__ingest(envelope, payload)` — persist the scout-report + ECL envelope at T1. Primary persist path; `from.eidolon=atlas` drives tier derivation. |
| **Commit** | Mid-cycle (optional) | `mcp__crystalium__commit(layer=episodic, payload, provenance={author_agent:"atlas",...})` — direct episodic write for notable observations not worth a handoff. `author_agent` MUST be `"atlas"`. |
| **Session end** | S (after ingest) | `mcp__crystalium__session_end()` — Dream trigger; call once per mission completion. |

**Trust tier:** T1 for all ATLAS calls (set process-wide by `CRYSTALIUM_CALLER_TIER=T1`
in the shared `.mcp.json`).

**Read-only invariant:** the I-1 constraint applies to the *codebase* (no write
tools against the repository). Calling `mcp__crystalium__*` memory tools is
explicitly permitted and does not violate I-1.

**Graceful skip:** if `mcp__crystalium__*` tools are unavailable (CRYSTALIUM not
installed), all four hooks are silent no-ops. ATLAS is EIIS-standalone-conformant
and works without CRYSTALIUM.

For the full tool surface (8 tools), layer×tier matrix, Dream consolidation
knobs, and `skill_invoke` / `plan_checkpoint` details, see the cortex deep table:
`methodology/cortex/memory-protocol.md` (in the Eidolons nexus).

---

## 8. atlas-aci MCP server — container mode (v1.1.0)

ATLAS v1.1.0 adds `--container` support to `eidolons atlas aci`. The
container mode builds the `atlas-aci` image locally (docker or podman) from
the pinned `ATLAS_ACI_REF` git ref and wires MCP host configs to use
`<runtime> run --rm -i --read-only` per session.

Smoke test for the container path:

```sh
eidolons atlas aci wire docker --host claude-code
# Expected: BUILD atlas-aci:<version> → image digest captured → .mcp.json written.
# Second run: no-op (digest unchanged).
eidolons atlas aci wire docker --host claude-code
# Expected: "already up-to-date" — no writes.
```

Exit codes specific to container mode: 7 (runtime not found), 8 (build
failed), 9 (non-interactive without --runtime).
