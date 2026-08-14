# VIGIL — Methodology Specification

> **Status:** v1.0 — authoritative.
> **Methodology-type:** Debugger / Diagnostician (capability class).
> **Scope:** Code failures — failing tests, regressions, heisenbugs, compound failures, APIVR-Δ escalations.
> **Out of scope:** Feature implementation (APIVR-Δ), planning (SPECTRA), documentation (IDG), healthy-codebase exploration (ATLAS), open-ended reasoning (FORGE).

VIGIL is the Eidolons team's **forensic specialist**. It is called when a failure resists normal repair. Its mission is not to fix, plan, or describe — it is to **attribute root causes under evidence discipline** and emit verified findings that downstream members can act on.

---

## 1. Architectural Invariants

Every VIGIL implementation enforces these, mechanically where possible:

| # | Invariant | Enforcement |
|---|-----------|-------------|
| I-1 | **Reproduction gates attribution** | No `[ROOT-CAUSE]` emitted without ≥2 consistent deterministic runs (or statistical confidence band ≥ 0.85) |
| I-2 | **Dependency-first, not temporal** | Candidate ranking uses the Information Dependency Graph; temporal-order heuristics are rejected |
| I-3 | **Hypothesis plurality** | ≥3 active `[HYPOTHESIS-N]` before any `[INTERVENTION-N]`; harness refuses single-hypothesis convergence |
| I-4 | **Counterfactual gates blame** | `[ROOT-CAUSE]` requires a recorded intervention that flips failure → success |
| I-5 | **Bounded intervention budget** | Hard cap: 5 counterfactuals per mission. After exhaustion: escalate, do not continue |
| I-6 | **Flag-gated authority** | `read-only` / `sandbox` / `write` set at mission start; write never inferred |
| I-7 | **Evidence-anchored findings** | Every claim carries `path:line_start-line_end` + confidence tier (`H|M|L`) + counterfactual result |
| I-8 | **Non-determinism declared, not masked** | Deterministic-first; two failures → statistical mode with confidence bands; `[FLAKE]` marker mandatory |
| I-9 | **Sandbox adapter interface** | VIGIL does not implement sandboxing; it uses whatever adapter the harness provides (Docker, language-native, bubblewrap, etc.) |
| I-10 | **Telemetry-driven compaction** | ≥60% context → async fold; ≥85% → halt and checkpoint |
| I-11 | **ECL envelope emission gates downstream hand-off** | Every artefact handed to APIVR-Δ, SPECTRA, IDG, or FORGE MUST be accompanied by an `*.envelope.json` sidecar conforming to the ECL envelope schema (see [Rynaro/eidolons-ecl](https://github.com/Rynaro/eidolons-ecl) — `envelope.v2.json`; `envelope.v1.json` for a v1.x sidecar accepted during the §7.3 compatibility window). Inbound envelopes from APIVR-Δ MUST be verified before the payload is processed (Phase V escalation entry). |

**Why mechanical.** Model-level instructions alone drift under long-horizon pressure. The research literature converges on this across GraphTracer (arXiv:2510.10581), AgenTracer (arXiv:2509.03312), and the Lifecycle of Failures study (arXiv:2509.23735): attribution accuracy lifts substantially (46.3% → 65.8% in the lifecycle paper) when replay and dependency-graph construction are harness-enforced rather than prompt-suggested.

---

## 2. The VIGIL Cycle

```
V ──▶ I ──▶ G ──▶ I ──▶ L
│                │
│                └── up to 5 counterfactual iterations
│
└── Each phase: inputs → artifact → exit criteria
```

Each phase is a contract. Inputs are explicit; outputs are schema-validated; exit criteria are binary.

### 2.1 Phase V — Verify

**Purpose.** Establish a reproducible failure. No attribution can begin without one.

**Inputs.**
- Failure description OR upstream handoff artifact (`repair-failed-report.md`, trace file, bug report, CI log)
- Repository root + test command + environment spec
- Mission authority flag: `read-only` / `sandbox` / `write`

**ECL inbound verification (escalation entry mode).** When VIGIL is invoked on the escalation entry mode (APIVR-Δ → VIGIL), Phase V MUST verify the inbound envelope BEFORE processing the payload:

1. Read `<repair-failed-report>.envelope.json` if present alongside the upstream artifact.
2. Validate it against the ECL envelope schema (see [Rynaro/eidolons-ecl](https://github.com/Rynaro/eidolons-ecl) — `envelope.v2.json`; accept `envelope.v1.json` shape for a v1.x sidecar received during the §7.3 compatibility window).
3. Confirm `from.eidolon == "apivr"` and `to.eidolon == "vigil"` and `performative ∈ {ESCALATE, REQUEST, ACKNOWLEDGE}` per the APIVR→VIGIL hand-off contract (see [Rynaro/eidolons-ecl](https://github.com/Rynaro/eidolons-ecl) — `contracts/apivr-to-vigil.yaml`).
4. Recompute `sha256` of the payload bytes and confirm it matches `envelope.integrity.value`.
5. Append `verify_pass` or `verify_fail` trace event to `.eidolons/.trace/<thread_id>.jsonl` (relative to consumer project root per ECL §5.1.1).
6. On `verify_fail`: halt mission, emit a `[GAP]` finding referencing the failure code (`INTEGRITY_MISMATCH` / `SCHEMA_INVALID` / `UNDECLARED_EDGE` / `PERFORMATIVE_NOT_ALLOWED` per ECL §5.3).

If no envelope sidecar is present (non-ECL APIVR-Δ), skip verification and proceed normally.

**Outputs.** `reproduction.md` — schema-validated, includes:
- `MISSION-ID`
- `FAILURE_SIGNATURE` — normalized representation (test name, error class, key stack frames)
- `REPRODUCTION_MODE` — `deterministic` or `statistical`
- `REPRODUCTION_EVIDENCE` — run log entries showing consistent failure
- `DETERMINISM_VERDICT` — `stable` | `flaky` | `intermittent`
- `AUTHORITY_ACKNOWLEDGED` — the flag under which this mission runs

**Hard constraints.**
- Deterministic-first: attempt exact replay with fixed seed, recorded env, pinned deps
- If first attempt produces inconsistent outcome → **one more deterministic attempt**
- If two deterministic attempts diverge → switch to **statistical mode**: ≥5 runs, compute failure rate + 95% confidence interval. Minimum 3-of-5 failures required to proceed.
- If statistical mode also fails to establish a stable signal → halt, emit `[GAP]`, escalate.

**Exit criteria.** Schema-valid `reproduction.md` + `DETERMINISM_VERDICT ≠ intermittent`.

---

### 2.2 Phase I — Isolate

**Purpose.** Narrow the fault surface. Produce a bounded set of candidate nodes worth graphing.

**Inputs.** `reproduction.md`, repository structure, trace artifacts (if any).

**Outputs.** `fault-surface.md` — schema-validated, includes:
- `CANDIDATE_NODES` — ranked list of `{path:lines, kind, suspicion_rank}`
- `REDUCTION_TRAIL` — what was ruled out and why (negative results are first-class)
- `SCOPE_BOUNDS` — path globs inside which the fault is almost certainly contained

**Method.** Delta-debugging-style reduction (Zeller, classical):
1. Start with the **suspect set** — files in the failing test's dependency closure, recent commits touching those files, and modules referenced in the stack trace.
2. Apply **bisection** where possible — git bisect for regressions; dependency graph walk for compound failures; input minimization for data-driven failures.
3. Apply **ruled-out markers** generously — "I checked X and found no causal path" is a valuable finding.
4. Cap candidate count at **≤8**. If more survive, the scope is too wide; narrow the suspect set.

**Hard constraints.**
- Suspect files must be cited by path:line, not inferred.
- Reduction must preserve the failure — any step that accidentally hides the failure is reverted.
- Candidates are ranked by **structural proximity to the failure**, not plausibility.

**Exit criteria.** ≤8 candidates with evidence anchors + `REDUCTION_TRAIL` ≥3 entries.

---

### 2.3 Phase G — Graph

**Purpose.** Build the Information Dependency Graph (IDG). Distinguish propagated symptoms from candidate root causes.

**Inputs.** `fault-surface.md`, reproduction trace (structured log, OTEL spans if available, test output).

**Outputs.** `idg.md` — schema-validated, includes:
- `NODES` — each candidate as a graph node with `{id, path:lines, observed_state, expected_state}`
- `EDGES` — information-flow dependencies (`A → B` means B's state derives from A's)
- `SYMPTOM_NODES` — nodes where failure is *observed* but state derives from upstream — marked `[SYMPTOM]`
- `ROOT_CANDIDATES` — nodes whose state does NOT derive from another candidate in the graph — primary hypothesis sources
- `SENSITIVITY_RANK` — for each root candidate, an estimate of how many symptom nodes depend on it

**Method.** Adapted from GraphTracer (arXiv:2510.10581):
1. For each candidate, trace **what its state depends on** — inputs, called functions, shared state, tool outputs.
2. Build directed edges: `A → B` if B reads A's output or state.
3. Identify nodes with zero incoming edges within the candidate set — these are `ROOT_CANDIDATES`.
4. Rank root candidates by descendant count (how many symptoms they could explain).

**Hard constraints.**
- The IDG is built from **trace evidence**, not from model inference about what "probably" calls what. If the evidence doesn't show the edge, the edge is not asserted.
- Temporal order is recorded but **does not set rank**. The earliest-observed symptom is not automatically the root cause.
- Cycles in the graph → halt, emit `[DISPUTED]`, escalate to human review.

**Exit criteria.** At least one `ROOT_CANDIDATE` identified, or explicit `[GAP]` with escalation.

---

### 2.4 Phase I — Intervene

**Purpose.** Falsify hypotheses via counterfactual replay. The root cause is the candidate whose minimal correction flips failure → success.

**Inputs.** `idg.md`, reproduction harness, authority flag.

**Outputs.** `intervention-log.md` — schema-validated, includes:
- `HYPOTHESES` — ≥3 competing hypotheses derived from `ROOT_CANDIDATES`
- `INTERVENTIONS` — each with `{hypothesis_id, intervention_type, diff_or_oracle, run_result, flipped: bool}`
- `SURVIVOR` — the hypothesis whose intervention flipped the failure; or `null` if none survived

**Method.** Counterfactual replay (AgenTracer, CHIEF):
1. For each hypothesis, design the **smallest possible intervention** that would falsify it:
   - Code change hypothesis → minimal diff applied in sandbox
   - Input/data hypothesis → corrected input supplied
   - State/config hypothesis → corrected state injected
   - Timing/concurrency hypothesis → fixed order or delay injected
2. Each intervention runs in the sandbox via the pluggable adapter.
3. Result categories:
   - **Flipped** (failure → success) → hypothesis survives falsification → candidate `[ROOT-CAUSE]`
   - **No change** → hypothesis falsified
   - **New failure** → hypothesis falsified but reveals a compound issue — log for `Learn` phase
4. If multiple hypotheses flip: the one with the **smallest intervention** wins. Ties → `[DISPUTED]` and escalate.
5. If zero hypotheses flip after budget exhausted (5 interventions) → **escalate to FORGE** with full evidence bundle.

**Hard constraints.**
- Intervention budget is **5 hard max**. The 5th intervention either flips or the mission escalates.
- Interventions run only in the declared authority scope:
  - `read-only` → interventions are **simulated only** (diff described but not executed); downstream must validate
  - `sandbox` → real execution in isolated environment; working tree untouched
  - `write` → sandbox first; if flip confirmed, may apply to working branch
- Non-deterministic baseline → each intervention re-runs 5× in statistical mode; flip requires ≥4 of 5 successes.

**Exit criteria.** Either one `[ROOT-CAUSE]` identified with counterfactual evidence, or escalation artifact emitted.

---

### 2.5 Phase L — Learn

**Purpose.** Emit the verified finding. Preserve failure signature and intervention pattern for future missions.

**Inputs.** `intervention-log.md` (with `SURVIVOR`), full mission artifacts.

**Outputs.**
1. **`root-cause-report.md`** (primary deliverable) — per `templates/root-cause-report.md`
2. **`root-cause-report-<mission-id>.envelope.json`** (or `…envelope.<recipient>.json` for fan-out) — ECL v2.0 envelope sidecar; envelope structure defined by the ECL envelope schema (see [Rynaro/eidolons-ecl](https://github.com/Rynaro/eidolons-ecl) — `envelope.v2.json`)
3. **`verified-patch.diff`** — if authority ≥ `sandbox` and survivor was a code change
4. **`failure-signature.yaml`** — entry for the semantic memory ledger
5. **Handoff directive** — pointer to downstream recipient (APIVR-Δ / SPECTRA / IDG / human)

**ECL envelope emission (Phase L).** After the payload is written, emit the envelope sidecar(s) per I-11:

1. Compute `sha256` of the payload bytes.
2. Generate a UUIDv7 `message_id`. Reuse `thread_id` from the inbound envelope on escalation entry; generate a new one on consultant/post-hoc first emit.
3. For fan-out (e.g. SPEC_DEFECT → SPECTRA + IDG): write the payload once, then write **one envelope per recipient** with distinct `message_id` values and shared `thread_id` and `parent_id`. File suffix: `<basename>.envelope.<recipient>.json` (e.g. `…envelope.spectra.json`, `…envelope.idg.json`). The `vigil-to-idg` envelope MUST set `constraints.trust_level: "standard"`; `vigil-to-apivr` and `vigil-to-spectra` envelopes MUST set `constraints.trust_level: "high"`.
4. Set `ise.assertion_grade` per `skills/intervene.md` § ISE Grade on the Root-Cause Report (conditional on authority) and `ise.receiver_authorization: {auto_route: true, auto_merge: false, auto_deploy: false}` on every envelope (ECL v2.0 §6.5).
5. Append one `emit` trace event per envelope to `.eidolons/.trace/<thread_id>.jsonl` (relative to consumer project root per ECL §5.1.1; create the directory if absent).

**Method.**
1. Walk the root cause back to its originating decision — commit, prompt, schema, config change. Cite the chain explicitly.
2. Emit `[FINDING-NNN]` records per ATLAS schema (team-wide convention).
3. Write the `FAILURE_SIGNATURE` in a form matchable against future failures (normalized error class + key dependency-path fragment + categorical tags).
4. Set `confidence`:
   - `H` — deterministic reproduction + counterfactual flip + clean graph
   - `M` — statistical reproduction + counterfactual flip with 4/5 confidence; or deterministic with one unexplained edge
   - `L` — escalation case; partial attribution recorded for downstream reasoning

**Hard constraints.**
- `root-cause-report.md` must be schema-valid. Missing counterfactual evidence → cannot emit `[ROOT-CAUSE]`, must emit `[HYPOTHESIS-N]` at best and escalate.
- `FAILURE_SIGNATURE` must be de-duplicated against existing memory entries before adding. Increment frequency count on match.
- No recommendations beyond the specific verified fix. Broader improvements route to SPECTRA, not VIGIL.

**Exit criteria.** Schema-validated `root-cause-report.md` + appropriate handoff artifact + memory entry written.

---

## 3. Failure Taxonomy

VIGIL classifies every attribution into one of these categories. This is not an optional tag — it is used by `FAILURE_SIGNATURE` de-duplication and by downstream Eidolons to route the fix.

| Category | Typical Signature | Minimal Intervention | Downstream Recipient |
|----------|-------------------|----------------------|----------------------|
| **LOGIC_ERROR** | Test asserts X, code produces Y; no exception | Code-change intervention | APIVR-Δ |
| **REGRESSION** | Test passed at commit N, fails at commit M | Git-bisect + code diff | APIVR-Δ |
| **BUILD_ERROR** | Compile/type/import failure | Schema/dep/config correction | APIVR-Δ |
| **TYPE_ERROR** | Static type checker violation | Type annotation or signature fix | APIVR-Δ |
| **LINT_VIOLATION** | Lint rule fired on new/modified code | Minimal rewrite | APIVR-Δ |
| **RUNTIME_ERROR** | Exception with traceback at runtime | Targeted guard/coercion | APIVR-Δ |
| **INTEGRATION_ERROR** | Failure at component boundary; schema/contract mismatch | Contract alignment | APIVR-Δ or SPECTRA if structural |
| **ENVIRONMENT_ERROR** | Failure traces to env/deps/system, not code | Env spec correction | human or APIVR-Δ (env scripts) |
| **HEISENBUG** | Non-deterministic; statistical replay required | Concurrency/ordering/state-isolation fix | SPECTRA (structural redesign) or APIVR-Δ |
| **COMPOUND** | Root cause interacts across 2+ independent nodes | Multi-node sequential interventions | SPECTRA (replanning) |
| **SPEC_DEFECT** | Test correctly fails because spec was wrong | No code fix; route upstream | SPECTRA (spec revision) |

The taxonomy aligns with APIVR-Δ's 9-category Reflect taxonomy and extends it with three domains APIVR-Δ escalates on: `HEISENBUG`, `COMPOUND`, `SPEC_DEFECT`.

---

## 4. Evidence Ladder

For each `[FINDING-NNN]` emission, the following evidence is required at the stated tier:

| Tier | Required for confidence | Evidence |
|------|------------------------|----------|
| **H** | High — emitted as `[ROOT-CAUSE]` | Deterministic reproduction (≥2 consistent runs) + counterfactual intervention flips (100% in 1 run, or ≥4/5 in statistical mode) + clean IDG with single root candidate + path:lines anchor verified |
| **M** | Medium — emitted as `[ROOT-CAUSE]` with flag, or as `[HYPOTHESIS-N]` | One of: statistical reproduction (≥0.85 CI) + flip; OR deterministic reproduction + flip but with one unexplained IDG edge; OR compound root cause where each component has H evidence individually |
| **L** | Low — never emitted as `[ROOT-CAUSE]`; only as `[HYPOTHESIS-N]` in escalation brief | Plausible but no counterfactual flip, or counterfactual flip only after broader change than the hypothesis described. Always escalated |

**Unanchored claims fail validation.** The schema rejects findings without `path:line_start-line_end` and confidence tier.

---

## 5. Artifact Pipeline

```
[Upstream: APIVR-Δ failure / user / CI / bug report]
                            │
                            ▼
                    ┌───────────────┐
                    │  Phase V      │  → reproduction.md
                    │  Verify       │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │  Phase I      │  → fault-surface.md
                    │  Isolate      │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │  Phase G      │  → idg.md
                    │  Graph        │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │  Phase I      │  → intervention-log.md
                    │  Intervene    │  (≤5 iterations)
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │  Phase L      │  → root-cause-report.md
                    │  Learn        │     + verified-patch.diff (if sandbox/write)
                    │               │     + failure-signature.yaml
                    └───────┬───────┘
                            ▼
                 [Downstream: APIVR-Δ / SPECTRA / IDG / FORGE / human]
```

---

## 6. Portable Abstractions

VIGIL is defined in terms of six abstractions. Any concrete implementation is valid if it provides these:

1. **Reproduction Harness** — runs a specific failing test/command with controlled env (deterministic mode: fixed seed, pinned deps, isolated fs; statistical mode: repeat N times, aggregate outcomes).
2. **Sandbox Adapter** — pluggable interface that executes counterfactual interventions in isolation. Implementations may use Docker/Podman, language-native test harnesses (pytest, jest), host-level sandboxes (Firejail, bubblewrap, nsjail), or language-specific tooling. VIGIL requires the interface; the harness chooses the mechanism.
3. **Trace Source** — structured failure trace. Preferred: OpenTelemetry GenAI-compatible spans. Acceptable: structured logs, test runner XML, LSP diagnostics. Unstructured text logs are parsed on best-effort basis.
4. **Dependency Analyzer** — minimum: AST-level call/data-flow graph for the mission scope. Tree-sitter is default; language-native analyzers (Prism, rust-analyzer, clangd) are acceptable substitutes.
5. **Diff Engine** — applies minimal-scope patches in sandbox; reverts cleanly on rollback.
6. **Memory Store** — persistent failure-signature ledger with de-duplication and frequency counts.

None are vendor-specific.

---

## 7. Failure Modes and Mitigations

| Failure | Mechanism | Mitigation |
|---------|-----------|------------|
| Unbounded investigation | Missing reproduction or weak stop condition | Phase V refuses; harness enforces 5-intervention cap |
| Temporal attribution | Assuming earliest-observed symptom = root cause | Phase G mandates IDG; sensitivity rank over descendant count |
| Premature convergence | Committing to first plausible hypothesis | I-3 requires ≥3 hypotheses; harness blocks intervention otherwise |
| Log-only blame | Attributing without replay | I-1 + schema validator: no counterfactual, no `[ROOT-CAUSE]` |
| Flakiness hidden | Noise treated as deterministic | Phase V auto-escalates to statistical on 2 failed deterministic attempts; `[FLAKE]` marker mandatory |
| Over-broad fix | Intervention exceeds hypothesis scope | Diff engine rejects interventions spanning >3 files without explicit justification |
| Confirmation bias | Designing interventions to confirm, not falsify | Phase I protocol: each hypothesis gets a falsification-optimal intervention, not a confirmation-optimal one |
| Compound-failure missed | Two root causes masked as one | Phase G records all root candidates; if multiple flip, categorize as `COMPOUND` |
| Authority drift | Writing outside declared scope | Flag-gated; `read-only` emits simulated diffs only; harness-enforced |
| Context rot | Long reproduction logs bloat context | ≥60% telemetry → async fold; raw traces go to content-addressable store |
| Sandbox divergence | Sandbox environment differs from production | Adapter spec mandates env parity fields; mismatch triggers `[GAP]` |
| Oracle wrong | Counterfactual assumes correct output that isn't | Multi-oracle when available; `[DISPUTED]` when oracle confidence low |

---

## 8. Evaluation

VIGIL implementations are evaluated on four axes:

- **Attribution accuracy** — fraction of canary missions where emitted `[ROOT-CAUSE]` matches ground truth.
- **Intervention efficiency** — mean interventions per mission (target ≤3 of the 5-cap).
- **Escalation discipline** — fraction of missions escalated when budget exhausted without a flip (target: 100% of such cases; zero false confidence).
- **Non-determinism handling** — accuracy on the flaky-failure subset of canary missions.

Canary dataset: `evals/canary/` — includes deterministic regressions, heisenbugs, compound failures, spec defects, and APIVR-Δ escalation fixtures.

**Target pass rate:** ≥80% on deterministic cases; ≥65% on non-deterministic cases (the latter bound set by current research state on statistical attribution).

---

## 9. Memory Protocol (CRYSTALIUM)

VIGIL integrates with CRYSTALIUM for persistent memory across missions. Full
layer × tier matrix: `eidolons/methodology/cortex/memory-protocol.md`.

**Recall pre-flight (Phase V / Isolate entry):** before any reproduction work,
call `mcp__crystalium__recall` with the failure signature as query,
`layers=["semantic","episodic","procedural"]`, `k=5`, and
`agent_class_visibility:"vigil"`. Prior procedural patterns (how a similar
failure class was isolated) and semantic root-cause knowledge (what causes this
signature) surface here and fold into hypothesis generation. See `agent.md`
§"Memory pre-flight" and `skills/verify.md` for the precise call shape.

**Ingest spine (Phase L):** after the ECL envelope is emitted, call
`mcp__crystalium__ingest(envelope, payload=<root-cause-report>)` to persist
the handoff at T1. `from.eidolon=vigil` drives tier derivation. See
`skills/learn.md §"CRYSTALIUM Memory"`.

**Learned-pattern commit (VIGIL extension):** also in Phase L, for each
corroborated debugging pattern surfaced during the mission, call
`mcp__crystalium__commit(layer=procedural OR semantic, payload=<pattern>,
provenance={author_agent:"vigil"})`. Procedural commits capture reusable
isolation / intervention techniques; semantic commits capture categorical
root-cause knowledge. Both feed the Dream→semantic promotion gate. `author_agent`
MUST be `"vigil"` on every direct commit.

**Session end:** call `mcp__crystalium__session_end()` once per mission
completion to trigger Dream consolidation asynchronously.

**Graceful skip:** if `mcp__crystalium__*` tools are unavailable, VIGIL
proceeds without memory — never hard-fails. EIIS standalone conformance is
preserved.

---

## 10. Versioning Policy

`SPEC.md` is the authoritative spec. Breaking changes to phase contracts or JSON schemas require a minor-version bump (v1.1, v1.2…). Major bumps reserved for invariant changes. Implementations declare `methodology: VIGIL` and `methodology_version: 1.0` in their `agent.md` frontmatter.

## 11. ECL Compatibility

VIGIL v1.8.0 emits ECL v2.0 envelopes by default on all inter-Eidolon hand-offs. The `ECL_VERSION` file in the repository root declares the targeted spec version (`2.0`). The nexus reads this during `eidolons sync` and warns on mismatches exceeding one minor (per ECL §7.2).

Integrity method: `sha256` for all v1.8.0 edges. `hmac-sha256` (RECOMMENDED for `trust_level: high` edges per ECL §6.3) is deferred to a future release pending `ECL_HMAC_KEY` distribution support in the nexus (D1). The choice is forwards-compatible: VIGIL can promote to `hmac-sha256` without a SemVer break in any peer.

VIGIL's own source repo vendors `schemas/ecl/envelope.v2.json` (current) alongside the retained `schemas/ecl/envelope.v1.json` (§7.3 compatibility window, through 2027-05-13) for self-contained `jq`-validation; neither is copied into the consumer install target (EIIS v1.4 §1.7 whitelist). ECL schemas and contracts remain maintained upstream at [Rynaro/eidolons-ecl](https://github.com/Rynaro/eidolons-ecl) as the source of truth — implementors requiring offline validation without cloning VIGIL should fetch `envelope.v2.json` and `contracts/apivr-to-vigil.yaml` directly from that repository.

**ISE trust hierarchy (ECL v2.0 §6.5).** VIGIL's emitted envelopes MAY carry the optional `ise` block. `root-cause-report`'s `ise.assertion_grade` is conditional on authority — see `skills/intervene.md` § ISE Grade on the Root-Cause Report for the full rule. `escalation-brief` is always `self-attested`. All emitted envelopes set `ise.receiver_authorization = {auto_route: true, auto_merge: false, auto_deploy: false}` — VIGIL never authorizes a receiver to merge or deploy on its behalf.

## 12. ESL Lifecycle Hop (Failure Path)

In an ESL-enabled project (`mcp__tonberry__*` tools available), VIGIL owns the
**failure path** of the Eidolons Spec Lifecycle — a checker distinct from the
maker (maker ≠ checker, ESL C4 gate). Full lifecycle, stage definitions, and
role bindings: `eidolons/methodology/cortex/esl-protocol.md`.

**On `verify_fail`:** call
`mcp__tonberry__transition --change_id <id> --to_status in_progress` to route the
change back to the maker, then emit an `ESCALATE` envelope carrying your
`[ROOT-CAUSE]` attribution (from the V→I→G→I→L cycle). The maker re-implements
against your finding, not the symptom.

**Drift gate (impl outran spec):** before archive, run
`mcp__tonberry__drift_check --change_id <id> --checker vigil`. A mismatch between
the implementation and the Semantic spec is a `verify_fail` → transition back +
`ESCALATE`. Your verify/drift envelopes carry `from.eidolon = vigil`.

**Graceful skip:** if `mcp__tonberry__*` tools are unavailable, run the normal
V→I→G→I→L cycle and emit findings/escalations the standard way — never
hard-fail. ESL is opt-in; EIIS standalone conformance is preserved. Precise
call shapes: `skills/esl-hop.md`.

---

*VIGIL — Methodology specification*
