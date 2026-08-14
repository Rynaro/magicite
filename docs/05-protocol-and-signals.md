# 05. Protocol and Signals: MCP Surface and Host Integration

**Status:** Draft-refined / v1  
**Provenance:** exploratory/consolidated-research-graph-mcp-proposal.md Part IV.5–IV.7, engram-format.md §7, FINDING-010, FINDING-012, GAP-003, mcp20-server-dossier.md sections 1–2  
**Decisions implemented:** [DECISION D3] Tiered signal-fidelity ladder; unified tool inventory; hooks demoted to optional adapter; [GAP-003] closed by design

---

## Overview

The Magicite MCP surface consists of **one unified tool inventory** that merges and clarifies the exploratory proposal's tools. Every tool carries:
- Input/output schemas (strict, unknown-field rejection)
- Risk class (R0–R5 from mcp20)
- Side-effect class (read-only, write ephemeral, write durable, trigger external)
- Signal tier (for learning tools: Tier 0/1/2)

The **signal fidelity model** (D3 verdict) is the core innovation: plasticity works on ANY host via a provenance-weighted ladder, never requiring host-specific hooks.

---

## Unified Tool Inventory

The exploratory proposal had 8 tools (proposal IV.6); engram-format had 8 additional tools (§7) with overlap. Unified here:

### Core Retrieval Tools

#### `route(query: str, context?: object, k?: int = 5, session_id?: str) -> object`

**Purpose:** Ranked skill candidates + composition plan for the query.

**Input schema:**
```json
{
  "query": "string (required, 1–500 chars)",
  "context": {
    "project_tag": "string (optional, e.g., 'steam-gaming', 'web-dev')",
    "recent_failures": ["string (optional, error class names)"],
    "user_prefs": ["string (optional, skill exclusions or preferences)"]
  },
  "k": "integer (optional, 1–20, default 5)",
  "session_id": "string (optional, UUID for continuity across calls)"
}
```

**Output schema:**
```json
{
  "candidates": [
    {
      "rank": 1,
      "id": "egr_9f2c7a1d",
      "name": "proton-ge-proton-downgrade",
      "intent_does": "Downgrade GE-Proton when...",
      "intent_use_when": "game crashes or performs worse...",
      "score": 0.87,
      "signal_tier_0": true,
      "exposure_count": 34,
      "status": "consolidated"
    }
  ],
  "composition_plan": [
    "steam-prefix-access",
    "proton-ge-proton-downgrade",
    "proton-verify"
  ],
  "plan_confidence": 0.72,
  "instructions": "After applying a skill, call signal_use(skill_id) with its id. When the task outcome is known (tests pass, user confirms), call signal_outcome(valence, skill_ids) to drive learning."
}
```

**Side effects:** Updates bookkeeping (exposure_count, last_activated) but NOT weights. Tier-0 signal (inferred).

**Risk class:** R0 (read-only on durable state).

**[DECISION D3] Signal tier 0 (Tier-0 passive inference):** Every `route()` call is observable server-side. The server logs: query, returned candidates, session_id, timestamp. This inference signal is always available, zero host requirements. Drives R and bookkeeping; never S.

---

#### `signal_use(skill_ids: list[str], session_id?: str) -> object`

**Purpose:** Mark skill application. Sets tags and candidate edges.

**Input schema:**
```json
{
  "skill_ids": ["egr_9f2c7a1d"],
  "session_id": "string (optional, UUID)"
}
```

**Output schema:**
```json
{
  "tagged": ["egr_9f2c7a1d"],
  "co_activation_candidates": ["egr_4b8e01cc", "egr_7d3f5a9e"],
  "note": "Tag set; expires in ~3 hours. Outcome signal will capture these into durable weight changes."
}
```

**Side effects:** Writes ephemeral tags (Tier C) and candidate edges. Updates last_applied timestamp.

**Risk class:** R1 (writes ephemeral, reversible state).

**[DECISION D3] Signal tier 1 (Tier-1 tool-mediated self-report):** An agent following the routing response instructions calls `signal_use()` after applying a skill. Host-independent (available on ANY MCP host that implements the tool). Probabilistic compliance—a dumb/forgetful model may skip it. Degrades gracefully.

---

#### `signal_outcome(valence: float, salience?: float, skill_ids?: list[str], session_id?: str) -> object`

**Purpose:** Verified outcome signal. Captures tags into durable weight changes (deferred to Dream).

**Input schema:**
```json
{
  "valence": "float (required, -1.0 to +1.0; -1=failure, 0=neutral, +1=success)",
  "salience": "float (optional, 0–1; confidence in the valence)",
  "skill_ids": ["string (optional, explicit skills to credit; else all tagged in window)"],
  "session_id": "string (optional, UUID)"
}
```

**Output schema:**
```json
{
  "captured": 2,
  "skills_credited": ["egr_9f2c7a1d", "egr_4b8e01cc"],
  "consolidation_scheduled": true,
  "note": "Signals logged. Dream cycle will consolidate at next checkpoint."
}
```

**Side effects:** Logs outcome events (audit trail). Marks captured tags for Dream consolidation. Never updates weights immediately.

**Risk class:** R1 (writes ephemeral, logged state).

**[DECISION D3] Signal tier 1 (same as signal_use):** Outcome valence can be derived from hook observations (test exit codes, user confirmations, implicit success detection) but is ultimately claimed by the agent. Provenance tier is `self_reported`. Capped per-session (anti-poisoning) and weighted lower than Tier-2.

---

### Learning & Consolidation Tools

#### `consolidate(manual_trigger?: bool = false) -> object`

**Purpose:** Trigger offline consolidation worker. Idempotent.

**Input schema:**
```json
{
  "manual_trigger": "boolean (optional, true = prioritize in queue)"
}
```

**Output schema:**
```json
{
  "consolidation_id": "c_20260809_a3b2c1",
  "enqueued": true,
  "status": "scheduled",
  "estimated_start": "2026-08-09T03:15:00Z",
  "note": "Consolidation queued. Check status with introspect(consolidation_id) for progress."
}
```

**Side effects:** Enqueues Dream consolidation worker. Worker writes to DB (Tier C expiry + renormalization) and to registry files (Tier A + B checkpoint).

**Risk class:** R3 (triggers backend batch processing).

**Note:** This is the entry point to the Dream cycle. The tool itself is fast (enqueues work); the actual consolidation is async.

---

#### `checkpoint() -> object`

**Purpose:** Idempotent DB → file flush. Subcomponent of `consolidate()`.

**Input schema:**
```json
{}
```

**Output schema:**
```json
{
  "checkpointed": 42,
  "modified_engrams": ["proton-ge-proton-downgrade", "steam-prefix-access"],
  "timestamp": "2026-08-09T03:00:00Z"
}
```

**Side effects:** Writes to registry files. Idempotent.

**Risk class:** R2 (writes filesystem, local only).

**Note:** Dream cycle calls this; exposed for manual recovery/audit.

---

### Management & Inspection Tools

#### `register(path: str, format?: "auto" | "egr" | "skill") -> object`

**Purpose:** Ingest skills into registry from `.egr.md` (native) or SKILL.md (import).

**Input schema:**
```json
{
  "path": "string (required, file or directory path relative to project root)",
  "format": "string (optional, auto-detect if omitted)"
}
```

**Output schema:**
```json
{
  "ingested": 3,
  "registered": [
    {"id": "egr_9f2c7a1d", "name": "proton-ge-proton-downgrade", "provenance": "authored"},
    {"id": "egr_4b8e01cc", "name": "steam-prefix-access", "provenance": "imported"}
  ],
  "validation_errors": [],
  "consolidation_scheduled": true
}
```

**Side effects:** Writes engrams to registry. Enqueues embedding and edge-wiring. Idempotent per filename.

**Risk class:** R2 (filesystem read + registry write).

---

#### `sync() -> object`

**Purpose:** Rebuild engine state from registry files. Refreshes embeddings, re-validates, wire-graphs.

**Input schema:**
```json
{}
```

**Output schema:**
```json
{
  "synced": 42,
  "removed": 0,
  "validation_errors": [],
  "consolidation_scheduled": true
}
```

**Side effects:** Full rebuild of in-memory state and DB from `.egr.md` files. Used for recovery / initialization.

**Risk class:** R2 (reads registry files, writes DB).

---

#### `introspect(skill_id?: str, consolidation_id?: str) -> object`

**Purpose:** Full audit: neighborhood, weights, history, signal tiers.

**Input schema:**
```json
{
  "skill_id": "string (optional, egr_id or name)",
  "consolidation_id": "string (optional, check Dream consolidation status)"
}
```

**Output schema (skill_id):**
```json
{
  "skill": {
    "id": "egr_9f2c7a1d",
    "name": "proton-ge-proton-downgrade",
    "status": "consolidated",
    "storage_strength": 0.71,
    "exposure_count": 34,
    "outcome": {"success": 29, "failure": 5}
  },
  "outbound_edges": [
    {
      "target": "steam-prefix-access",
      "type": "depends_on",
      "storage_strength": 0.82,
      "provenance": "declared"
    }
  ],
  "inbound_edges": [...],
  "history": [
    {"event": "authored", "timestamp": "2026-06-14T00:00:00Z", "author": "rynaro@example.com"},
    {"event": "sharpened", "timestamp": "2026-07-02T14:23:00Z", ...}
  ],
  "silent_engram_flag": false
}
```

**Side effects:** None (read-only).

**Risk class:** R0 (read-only).

---

#### `flag_dead() -> object`

**Purpose:** Find silent engrams: stored but never routed in the last T sessions.

**Input schema:**
```json
{}
```

**Output schema:**
```json
{
  "candidates": [
    {
      "id": "egr_2a3f5b6c",
      "name": "obsolete-workflow",
      "last_routed": "2026-05-01T00:00:00Z",
      "retrieval_strength": 0.02,
      "reason": "retrieved 0 times in last 30 days; poor cues or description drift"
    }
  ],
  "recommendation": "review triggers and intent.use_when; re-describe or archive"
}
```

**Side effects:** None (read-only observation).

**Risk class:** R0 (read-only).

---

#### `sharpen(name: str, proposed_changes?: object) -> object`

**Purpose:** Manual sharpening pass (else Dream-cycle automatic).

**Input schema:**
```json
{
  "name": "string (required, engram name)",
  "proposed_changes": {
    "procedures": ["suggested step rewrite if step had low confidence"],
    "triggers": ["new positive trigger example"],
    "pitfalls": ["observed failure mode"]
  }
}
```

**Output schema:**
```json
{
  "sharpened": true,
  "version_bumped": "2 → 3",
  "changes": [...],
  "consolidation_scheduled": true
}
```

**Side effects:** Modifies engram, bumps version, queues consolidation.

**Risk class:** R3 (modifies registry engram).

---

#### `promote(name: str) -> object`

**Purpose:** Lifecycle transition: nascent/probation → consolidated, or consolidated → promoted.

**Input schema:**
```json
{
  "name": "string (required, engram name)"
}
```

**Output schema:**
```json
{
  "promoted": true,
  "name": "proton-ge-proton-downgrade",
  "new_status": "consolidated",
  "evidence": {"successes": 29, "failures": 5, "confidence": "high"}
}
```

**Side effects:** Updates status field. Requires evidence gate.

**Risk class:** R3 (modifies registry status).

---

#### `archive(name: str, reason?: str) -> object`

**Purpose:** Lifecycle transition: any → archived. Removes from routing but retains for provenance.

**Input schema:**
```json
{
  "name": "string (required, engram name)",
  "reason": "string (optional, explanation)"
}
```

**Output schema:**
```json
{
  "archived": true,
  "name": "proton-ge-proton-downgrade",
  "provenance_entry": "archived at 2026-08-09T03:00:00Z by user; reason: no longer used"
}
```

**Side effects:** Moves engram to archived status; remains in `.spectra/archive/` for history.

**Risk class:** R3 (modifies registry status).

---

#### `export(out_dir: str) -> object`

**Purpose:** Render SKILL.md shims from consolidated+ engrams.

**Input schema:**
```json
{
  "out_dir": "string (required, target directory path)"
}
```

**Output schema:**
```json
{
  "exported": 38,
  "target_dir": "skills/",
  "format": "SKILL.md",
  "note": "Generated from engrams with status consolidated or higher"
}
```

**Side effects:** Writes filesystem.

**Risk class:** R2 (filesystem write).

---

## Signal Fidelity Model: The Three-Tier Ladder

[DECISION D3] All four signal mechanisms from the exploratory corpus are folded into a provenance-weighted ladder:

| Tier | Mechanism | Availability | Provenance | Confidence | Weight Cap |
|---|---|---|---|---|---|
| **0** | Passive server-side inference from tool calls (route, exposure, co-retrieval, implicit-negative, idle gaps) | GUARANTEED on any host | `inferred` | Low (50–70%) | Learning on R only; never S |
| **1** | Tool-mediated self-report: `signal_use` + `signal_outcome` called by agent following instructions | Any host; probabilistic compliance (60–80%) | `self_reported` | Medium (70–85%) | Capped per-session; can update S via Dream |
| **2** | Host hook adapters: Claude Code constructs (SessionStart, PreToolUse, PostToolUse), local filesystem ops | Host-specific, optional acceleration | `hook_verified` | High (85–95%) | Full Δw weight; highest priority in Dream |

### How Plasticity Scales Across Tiers

Every signal carries its **provenance tier** (assigned server-side, not claimed by caller). During Dream consolidation:

1. **Tier-0 signals** (inferred): Adjust R (retrieval strength) and bookkeeping (exposure_count, co-occurrence edges). Never touch S (storage strength). Prevents the index from reinforcing its own retrieval bias.

2. **Tier-1 signals** (self_reported): Can update both R and S via Δw, but:
   - Capped per session: max 3 Δw events per skill per session (anti-poisoning)
   - Weighted lower than Tier-2: η_eff ← η × 0.6 for Tier-1 vs η × 1.0 for Tier-2
   - Metaplastic saturation still applies

3. **Tier-2 signals** (hook_verified): Full weight Δw. No cap (verified externally).

### Tier-2: Host Adapter Matrix

Tier-2 requires host-specific adapters. Currently documented: Claude Code.

#### Claude Code (Tier-2 Hook Adapter)

**Available hooks:**
- `SessionStart`: Inject top-m "hot skills" (highest R) as priming block
- `PreToolUse`: Call `signal_use(skill_id)` when selected skill is about to apply
- `PostToolUse` + `Stop`: Call `signal_outcome(valence, ...)` when task completes (infer valence from exit codes / test results / user feedback)

**Valence inference:**
- Positive: test exit code 0, user says "success", or task state indicates completion
- Negative: non-zero exit code, error output detected, user says "failure"
- Neutral: timeout, user says "unclear" or skips confirmation

**Caveat:** Hooks are probabilistic. A hook may fire late or not at all (agent forgets, hook not triggered). Fall back to Tier-1 (`signal_outcome()` explicit calls).

**Future:** Other hosts (Cursor, AgentKit, custom harnesses) can implement Tier-2 adapters with their own hook machinery.

#### Tier-1 Fallback for Hookless Hosts

On hosts without hooks, plasticity operates solely on Tier 1:
- Harness embeds the routing instruction: "After applying a skill, call `signal_use(skill_id)` and later `signal_outcome(...)` when the outcome is known."
- Some agents (Claude, GPT-4, smart harnesses) follow instructions reliably → Tier-1 signals flow.
- Dumb/forgetful agents skip it → only Tier-0 (passive inference) works.

**Result:** Plasticity is never dead; it degrades to Tier-0 on zero Tier-1 compliance. Learning continues via exposure and co-retrieval patterns (slow, but safe and unsupervised).

---

[GAP-003 closed by design]

> **Question (FINDING-012):** On hookless MCP hosts, is plasticity dead? Answer: No. Tier-0 passive inference + Tier-1 tool-mediated fallback ensure learning is always available, never requiring host-specific hooks. Degraded (Tier-0 is weaker) but alive.

---

## Progressive Disclosure Preserved

The routing response carries only **L1 metadata** (~100 tokens × k). The harness loads L2/L3 on demand via its native bash read (or Magicite's `load_skill_body()` tool for hosts without filesystem access). **Magicite never stuffs skill bodies into context** — avoiding the Graph2Text bottleneck noted in the exploratory corpus.

---

*Section authored by IDG, 2026-08-14, drawing on exploratory/consolidated-research-graph-mcp-proposal.md Part IV.5–IV.7 and implementing [DECISION D3].*
