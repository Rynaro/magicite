# 02. Architecture: Engine Structure and Deployment Posture

**Status:** Draft-refined / v1  
**Provenance:** exploratory/consolidated-research-graph-mcp-proposal.md Part IV.1–IV.3, FINDING-006, FINDING-011, mcp20-server-dossier.md sections 1–2, 5, 10–11, GAP-004  
**Decisions implemented:** [DECISION D2] Local-first core + deployment-profile separation; five mcp20 disciplines adopted; concurrency/scoping model; R5 security boundary

---

## Architecture Verdict: Local-First Core as a Deployment Profile

[DECISION D2] The engine is **transport-agnostic** and **serves as a library behind a deployment boundary**. The v1 deployment profile is **local-first** (stdio MCP, embedded SQLite, local embeddings); serving is a **later profile, not a second architecture**.

### Rationale

The exploratory corpus presented two apparent architectures: local-first (stdio/SQLite/Ollama, no dependencies) vs production-served (TypeScript SDK/Streamable HTTP/OAuth/Postgres multi-tenant). These are not incompatible — they are **deployment profiles of the same engine**.

**Why local-first v1:**
1. Works whenever the project is served to a host (dominant case: a coding agent with local filesystem access).
2. Eliminates operational complexity (no database server, no auth infrastructure, no network I/O in the hot path).
3. Preserves git-committable registry semantics (the registry directory is a clean snapshot of consolidated knowledge).
4. Single-writer Dream model simplifies plasticity (all state mutations come from one batch worker; no race conditions on durable state).

**Why serving is "just" a profile:**
The engine is a library with a **tool-call contract** (tool name, input schema, output schema, side-effect class). Any transport (stdio MCP, HTTP Streamable, gRPC, local Python function) that wraps the same contract preserves engine semantics. A later served profile may add OAuth, PostgreSQL, multi-tenancy, queues, and K8s — but none of these change the engine's decision-making logic.

---

## Component Model: Hot Path / Write Path / Cold Path

### Hot Path (Per-Query Routing)

```
route(query, context?, k=5) → top-k L1 metadata + composition plan
  1. Embed query (Ollama, local, ~50ms)
  2. Top-m similarity seed via skill body embeddings
  3. Spreading activation over weighted edges (PPR, ~10–100ms)
  4. Score = α·activation + β·R + ε·excitability
  5. Aggregate by Leiden community (two-level rerank)
  6. Topological expansion on winner via composes-DAG
  7. Return [L1 metadata for top-k] + [ordered composition plan]
```

**Invariant:** The hot path is **read-only on durable state**. `route()` updates bookkeeping only (exposure counts, last_activated). Durable state (S, edge weights) is never written.

**Progressive disclosure:** Response carries only L1 metadata (~30–100 tokens × k). The harness loads L2/L3 on demand via its native bash read or tool call for the chosen skill—**Magicite never stuffs skill bodies into context** (Graph2Text avoided by construction).

---

### Write Path (Per-Event Learning)

```
signal_use(skill_ids) → set/promote candidate edges
  1. Tag activated skills with decaying session-scope tags (~1–3h)
  2. Co-activated skill pairs → candidate edges
  3. Store in ephemeral DB table (hot path overhead minimal)

signal_outcome(valence, salience?, skill_ids?) → tag capture → Δw
  1. Find all tags still alive in session window
  2. If valence high-confidence (e.g., from hook), capture tags
  3. For each captured tag: Δw ∝ η(1 − w/w_max) × outcome × recency
  4. Apply spacing/metaplastic saturation
  5. Log event to session trace (audit trail)
```

**Invariant:** Write path updates **R and tags only**. It never writes S (storage strength). Tags expire; they do not persist beyond consolidation.

**Signal types:**
- `inferred` — server-side observation of tool calls (always available)
- `self_reported` — tool calls from the agent following routing instructions (best-effort)
- `hook_verified` — host-side confirmation (Claude Code, rare on generic MCP hosts)

---

### Cold Path (Offline Consolidation Worker — Dream Cycle)

The **Dream worker** runs periodically (hourly cron, daily batch, or on-demand `consolidate()` call). It is the only writer to S and the registry files.

```
consolidate():
  1. Replay session traces → identify high-value paths
  2. Prune: edges w < threshold_prune for ≥3 sessions → archive
  3. Renormalize: global weight scaling (synaptic homeostasis)
  4. Distill: recurrent activation sequences → nucleate composite-skill candidates
  5. Rebuild embeddings: refresh skill descriptions if content drifted
  6. Audit: check registry for coverage gaps (unmet needs) and hubs (black-hole skills)
  7. Checkpoint: DB → file (plasticity.storage_strength + per-step stats)
```

**Invariant:** Only the Dream worker writes to `.egr.md` files and to S. This enforces the two-tier rule (doc 03) and makes the registry a clean, git-committable snapshot between Dream runs.

**Reversibility:** All Dream operations are idempotent and audited. A failed or incorrect consolidation can be rolled back by restoring a prior checkpoint or re-running with different parameters.

---

## Five Transport-Agnostic Disciplines (from mcp20 baseline)

[DECISION D2] These five disciplines are adopted NOW (cheap when implemented early, expensive to retrofit later):

1. **No correctness dependence on protocol sessions or connection persistence.**
   - Session state is keyed by explicit `session_id` parameters (or harness-supplied context).
   - A skill graph remains coherent across disconnects and reconnects.
   - Tools MUST NOT assume a "connection object" exists or persists.

2. **Strict input/output schemas; unknown-field rejection.**
   - Every tool input carries a schema; unknown fields are rejected, never silently ignored.
   - Enables forward compatibility and typo detection.
   - Output schema validated before return.

3. **Tool metadata: side-effect class and risk class R0–R5.**
   - Every tool is labeled with its side effects (read-only, writes non-durable state, writes durable state, triggers external action).
   - Risk class: R0 (no side effect) → R5 (raw shell / arbitrary SQL / filesystem / URL access).
   - Risk class informs governance and approval thresholds (doc 06).

4. **Idempotency for writes.**
   - Write operations are idempotent: calling `signal_outcome(...)` twice with the same arguments in the same session is safe.
   - Use `version_token` or timestamp guards if needed.

5. **R5 prohibition: no raw shell, SQL, filesystem, or URL tools.**
   - Magicite is an MCP server, not an execution sandbox.
   - Code in engram `exec` blocks is host-side content, executed by the agent under the host's own permission system.

---

## Security Boundary: The Server Never Executes Skills

[DECISION D2] A critical boundary statement for hosts:

> **Magicite stores, routes, and audits skill artifacts. It NEVER executes engram code.**
>
> Engram `exec` blocks are Markdown fenced code (Python, bash, SQL) embedded in the `.egr.md` file as a reference, not an executable. The *harness* (agent + host) extracts and executes these blocks under the host's own permission and sandbox systems (e.g., Claude Code's audited bash execution, ariramba's local-sandboxed Python).
>
> The server observes outcomes (success/failure) via hooks or explicit signals; it does not inspect, validate, or execute the code itself. This keeps Magicite out of the execution-permission stack and avoids the R5 risk class.

**Implication for hosts without native exec sandboxes:** Hosts can safely import engrams with `exec` blocks; the blocks are inert until a harness with execution capability loads and runs them. Import quarantine (doc 06) is the safety gate, not code inspection.

---

## Concurrency & Scoping: Per-Project, Single-Writer

[GAP-004] The model is **per-project registry + single-writer Dream**.

### Registry Scope

```
~project_root/
  .magicite/
    engrams/
      skill-a.egr.md
      skill-b.egr.md
      skill-graph.db            ← SQLite
```

- One registry per project (one `.magicite/engrams/` directory).
- Skills are project-local (not global).
- Multiple projects → multiple independent registries (no shared state).
- Concurrent agents may query the same registry; only Dream writes to it.

### Write Contention: Single-Writer + WAL

The exploratory corpus claimed "zero write contention" but was imprecise. **Refined:**

| State | Writer(s) | Contention |
|---|---|---|
| R, tags, candidate edges (ephemeral) | Hot path (multiple agents, parallel sessions) | YES — SQLite WAL (write-ahead log) + PRAGMA journal_mode=WAL handles this. Sessions write to a shared log; no blocking. |
| S, per-step stats, status (durable) | Dream worker only | NO — Dream is single-writer. Hot-path reads may overlap; no durable-state writes race. |
| `.egr.md` files | Dream worker only | NO — Dream is single-writer. File mutations are atomic per Dream run. |

**Implication:** Multiple concurrent sessions are safe. The SQLite WAL allows readers and the hot-path writer to coexist; the Dream worker coordinates durable-state updates offline.

---

## Deployment Profiles (v1 and Future)

### v1: Local-First (In-Repository)

| Component | Implementation |
|---|---|
| Transport | stdio MCP (MLK, FastMCP, or equivalent) |
| Storage | SQLite, single file in project `.magicite/engrams/` |
| Embeddings | Ollama (local model, no API calls) |
| Scope | Per-project registry |
| Query model | Command-line / Python function / MCP client |
| Governance | File-based approval (human-veto gate via `.spectra/changes/` in Tonberry) |
| Deployment | Library linked into agent codebase, or stdio MCP server spawned by agent |

**Applicability:** Local coding agents (Claude Code, Cursor, Rynaro ecosystem), Python/Bash harnesses, any agent with filesystem access.

---

### v2+: Served (Deferred)

Later profiles MAY include:

| Component | Variant |
|---|---|
| Transport | HTTP Streamable, gRPC, custom network protocols |
| Storage | PostgreSQL, distributed graph DB, cloud object storage |
| Embeddings | Hosted embedding service (OpenAI, Anthropic, internal), or local on-disk cache |
| Scope | Multi-tenant (org-level or instance-level registries) |
| Query model | REST API, websocket subscriptions, gRPC streaming |
| Governance | OAuth/OIDC, centralized approval workflow (Tonberry as a separate service) |
| Deployment | Kubernetes, serverless functions, VPC-embedded microservice |

**When:** Only if real adopters are predominantly hosted/cloud agents with no local filesystem. The engine design is prepared for this (tool contracts are profile-agnostic); implementation is deferred to SPECTRA and later iterations.

---

## Impact on Other Docs

- **Doc 03 (Learning Model):** The three-tier state model (hot-path R/tags, durable S/stats, cold-path consolidation) depends on this write-path separation.
- **Doc 04 (Engram Format):** The two-tier rule (R in DB, S in file) is enforced by the Dream-only-writes constraint.
- **Doc 05 (Protocol):** Tool definitions inherit risk classes from this discipline list.
- **Doc 06 (Trust & Governance):** Approval machinery is scoped to the single-writer model (Dream runner is the approval-gate executor).

---

*Section authored by IDG, 2026-08-14, drawing on exploratory/consolidated-research-graph-mcp-proposal.md Part IV and mcp20-server-dossier.md production guidance.*
