# 04. Engram Format: v0.2 Specification Direction

**Status:** Draft-refined / v1  
**Provenance:** exploratory/engram-format.md entire, FINDING-007, FINDING-009, FINDING-010, GAP-005  
**Decisions implemented:** [DECISION D1] Three-tier rule applied to format; refined `synapses:` block for Tier-B edges; SKILL.md full round-trip (import + export); unified `register()` semantics

---

## Overview

The ENGRAM format (`.egr.md` files) is the portable skill-artifact spec. It carries:
- **Routing data** (intent, triggers, context affinity, embeddings)
- **Plasticity state** (storage strength, outcome counts, per-step stats, learned edges)
- **Provenance** (authorship, refinement history, versioning)
- **Composition contracts** (needs, yields, composes, inhibits)

This section specifies the v0.2 direction, incorporating fixes from D1 (three-tier state) and D4 (trust fields), and closing FINDING-010 (the registration-unit contradiction) via unified `register()`.

---

## File Anatomy

One file per skill: `engrams/<name>.egr.md`. No mandatory companion directory; assets live in `<name>.assets/` if needed.

```markdown
---
<YAML frontmatter: identity · routing · plasticity · composition · provenance · trust>
---
## Procedure       ← numbered steps, each carrying live confidence stats
## Pitfalls       ← observed failure modes with counts
## Examples       ← positive and negative invocation examples
## Provenance     ← append-only reconsolidation journal

[Optional exec blocks: Python, bash, SQL — host-side execution only]
```

---

## Frontmatter Schema (v0.2)

### Identity Block

```yaml
# ── IDENTITY ──────────────────────────────────────────────
spec: engram/0.2
name: proton-ge-proton-downgrade      # 1–64 chars, lowercase-hyphen, == filename
id: egr_9f2c7a1d                      # immutable hash of identity+routing blocks
version: 3                            # bumped on every sharpening event
provenance: sharpened                 # authored | imported | distilled | sharpened
parents: [egr_4b8e01cc]               # lineage: engrams this derives from
```

### Routing Block (Replaces SKILL.md Description)

```yaml
# ── ROUTING ────────────────────────────────────────────────
intent:
  does: "Downgrade GE-Proton when a Steam game regresses after an update"
  use_when: "game crashes or performs worse immediately after GE-Proton update"
  not_when: "game never worked on any version"  # separate contraindication routing view

triggers:
  positive:                           # ≥3 required; embedded at register time
    - "game X broke after proton update"
    - "rollback ge-proton for steam"
    - "new proton version regression"
  negative:                           # ≥1 required; separately embedded/penalized
    - "proton not launching at all"   # → inhibits proton-clean-install

context_affinity: [steam, lutris, nvidia, fedora-kde]   # links to context nodes

embedding:
  model: bge-m3
  ref: sha256:…                       # vector lives in graph DB, not file
  last_refreshed: 2026-08-09T03:00:00-03:00
```

### Plasticity Block (Tier A: Durable Node State)

```yaml
# ── PLASTICITY (durable node state, checkpointed by Dream) ──
plasticity:
  storage_strength: 0.71              # S_node; slow, cumulative
  exposure_count: 34
  outcome:
    success: 29
    failure: 5
  last_applied: 2026-08-09T12:30:00-03:00
  excitability: 0.05                  # exploration bonus; decays with exposure
  last_checkpoint: 2026-08-09T03:00:00-03:00
  status: consolidated                # nascent → probation → consolidated → promoted
                                      #              ↘ archived (never deleted)
```

### Synapses Block (Tier B: Durable Edge State) — NEW in v0.2

[DECISION D1] Learned edges that pass consolidation threshold are persisted here. This fixes FINDING-009.

```yaml
# ── SYNAPSES (durable learned edges, consolidated by Dream) ──
synapses:
  # One outbound edge per learned relationship
  # - target: engram name (must resolve in registry)
  # - type: co_activation | composes | depends_on | similar_to | inhibits
  # - storage_strength: association strength (S_edge)
  # - evidence_count: number of sessions that reinforced this edge
  # - provenance: declared | learned | distilled
  # - first_observed: timestamp
  # - last_updated: timestamp when Dream last consolidated this
  
  - target: steam-prefix-access
    type: depends_on
    storage_strength: 0.82
    evidence_count: 29
    provenance: declared
    first_observed: 2026-06-14T00:00:00Z
  
  - target: proton-clean-install
    type: inhibits
    storage_strength: 0.61
    evidence_count: 18
    provenance: learned
    first_observed: 2026-07-02T14:23:00Z
    last_updated: 2026-08-09T03:00:00Z

  # Dangling edge (target not yet registered): inert until resolved
  - target: unknown-graphics-driver-issue
    type: similar_to
    storage_strength: 0.30
    evidence_count: 3
    provenance: learned
    first_observed: 2026-08-01T00:00:00Z
    # ↑ This will be dropped from routing until unknown-graphics-driver-issue registers
```

### Composition Block (Declared Edges, Always Present)

```yaml
# ── COMPOSITION (declared first-class DAG edges) ───────────
needs: [steam-prefix-access]          # inputs required → plan chaining
yields: [working-ge-proton-version]   # portable metadata; not a graph edge in 0.3
composes: []                          # sub-engrams if this is a distilled composite
inhibits: [proton-clean-install]      # mutual exclusion
affinity: [steam, lutris]             # links to context nodes
```

### Provenance & Trust Block (Tier A: Durable Provenance)

```yaml
# ── PROVENANCE & TRUST ─────────────────────────────────────
provenance_journal:
  - version: 1
    timestamp: 2026-06-14T00:00:00Z
    author: rynaro@example.com
    event: authored
    note: "Initial draft from user experience"

  - version: 2
    timestamp: 2026-07-02T14:23:00Z
    author: rynaro@example.com
    event: sharpened
    note: "Step 3 rewritten after 4 failures (fault=global-pinning)"
    summary_of_change: "Added per-appid pinning guidance"

  - version: 3
    timestamp: 2026-08-09T03:00:00Z
    author: dream-worker
    event: consolidated
    note: "Moved to consolidated status; edge to proton-clean-install learned"
    signal_tier: self_reported
    base_version: 2

trust:
  origin: authored                    # authored | imported | distilled
  verification_status: verified       # pending | verified | quarantined
  signer: null                        # future: gpg key hash or cert
  import_source: null                 # if imported, where from
  injection_risk:
    triggers: "User-controllable; treat as untrusted input"
    pitfalls: "Descriptive; low-injection risk"
    intent: "LLM-written; verifiable against actual skill behavior"
```

### Export Block

```yaml
# ── EXPORT ────────────────────────────────────────────────
exports:
  skill_md: true                      # Dream cycle renders vanilla SKILL.md shim
```

---

## Body Schema

```markdown
## Procedure
<!-- Steps carry live fault-attribution stats updated by Dream -->
1. [ok: 31/33] Identify the game's Steam appid and prefix.
2. [ok: 29/34] Download the target GE-Proton build into compatibilitytools.d.
3. [warn: 18/26] Pin the version per-game via launch options, not globally.
   - Last sharpened v3: added after 4 failures traced to global pinning
   - Failure mode identified: GLOBAL_PINNING_BREAKS_SIBLINGS
4. [ok: 12/12] Verify with `PROTON_LOG=1 %command%` and inspect the log tail.

## Pitfalls
- (×4) Global version pinning breaks sibling games — always pin per-appid.
- (×2) NTFS-mounted libraries fail silently; check filesystem first.

## Examples
+ "Hades II stutters since GE-Proton 10-9" → full procedure
- "Steam won't open" → NOT this engram (route to steam-runtime-repair)

## Provenance
- v1 2026-06-14 · authored by Rynaro
- v2 2026-07-02 · sharpened: step 3 rewritten (4 fails, fault=global-pinning)
- v3 2026-08-09 · consolidated: Dream checkpoint; edge to proton-clean-install learned

[Optional: exec blocks for host-side code extraction]
```

---

## The Three-Tier Rule Applied to Format

[DECISION D1] Frontmatter enforces the three-tier state model:

| Tier | State | Home | Mutated by | Ephemeral? |
|---|---|---|---|---|
| **A (durable, node)** | plasticity block (S_node, outcome counts, per-step stats, status) | `.egr.md` frontmatter | Dream checkpoint only | No |
| **B (durable, edge)** | synapses block (S_edge, consolidated learned edges, provenance) | `.egr.md` frontmatter | Dream checkpoint only | No |
| **C (ephemeral, edge)** | R, tags, candidate edges, cached embeddings | SQLite DB | Hot path | Yes; expires per session or decay |

**Consequence:** The file is **never written by the hot path**. Only the offline Dream cycle checkpoints DB → file. Registry is always git-committable and clean between Dream runs.

---

## Lifecycle State Machine

```
                               ┌─────────────┐
                               │    draft    │
                               └─────────────┘
                                     ↑
                                     │
                              gate fail
                                     │
┌──────────┐  gate pass  ┌──────────────────┐                    ┌──────────┐
│ nascent  ├─────────────→│   probation      ├─S≥θ ∧ pass-rate≥φ→│consolidated├─→ promoted
└──────────┘             └──────────────────┘                    └──────────┘
                               │
                      decay/fail floor
                               │
                               ↓
                         ┌────────────┐
                         │  archived  │ (never deleted)
                         └────────────┘
```

**Nascent** (status field = `nascent`): Born from distillation or manual nucleation. Routes with excitability bonus. Stats accumulate. Sharpening active.

**Probation** (status field = `probation`): Waiting for enough evidence. Requires S ≥ θ_probation and pass_rate ≥ φ_probation (e.g., 3+ successful invocations, no failures). Sharpening active.

**Consolidated** (status field = `consolidated`): Normal routing. Eligible for `skill_md` export. Sharpening active but requires higher evidence bar.

**Promoted** (status field = `promoted`): Stable, widely-used. Older, strong engrams resist change (metaplasticity at file level). Sharpening requires very-high evidence bar or manual override.

**Archived** (status field = `archived`): Excluded from routing, retained for provenance. Reached when S < floor_archived. Can be revived if new evidence accumulates.

---

## Unified `register()` Semantics (Closes FINDING-010)

[DECISION D4] The exploratory corpus had two `register()` definitions (proposal :272 vs engram-format :161). Unified here:

```python
register(path: str, format: "auto" | "egr" | "skill") -> List[Engram]:
    """
    Ingest skills into the registry from either `.egr.md` (native) or 
    SKILL.md (import) format. Returns list of registered engrams with 
    their IDs and ingestion status.
    
    path: file or directory to scan
    format: auto-detect or explicit
    
    Behavior:
    1. If format=egr or auto & .egr.md detected:
       - Parse YAML frontmatter + body
       - Validate schema (JSON Schema)
       - Embed triggers + procedure via local model
       - Wire similarity/affinity edges
       - Insert into registry with provenance=authored/sharpened (depending on version field)
       - Status determined by plasticity.status (if present) or default nascent
    
    2. If format=skill or auto & SKILL.md detected:
       - Parse SKILL.md YAML + description + body
       - Convert to .egr.md format:
         * intent.does ← description
         * intent.use_when ← (inferred from body or manual) or default "general purpose"
         * triggers.positive ← auto-generate from description + keywords
         * Procedure / Pitfalls / Examples ← extracted from SKILL.md body
         * provenance ← "imported"
         * status ← "nascent" (requires quality gate before probation)
         * trust.origin ← "imported"
         * trust.verification_status ← "pending" (quarantine)
       - Embed, wire edges
       - Insert into registry with a migration note in provenance_journal
    
    3. Common to both:
       - Lint enforcement: ≥3 positive triggers, ≥1 negative, `not_when` present, 
         Procedure steps numbered, Provenance append-only
       - Any lint violation → hard error; format discipline is the learning signal
       - Set last_checkpoint ← now
       - Validation against JSON Schema for v0.2 frontmatter
    """
```

**Consequence:**
- Native `.egr.md` files are ingested as authored/sharpened (no conversion).
- SKILL.md files are converted to `.egr.md` with provenance=imported, status=nascent.
- Both flows merge into a single endpoint and follow the same lint rules.
- Migration is transparent; old SKILL.md corpora can be adopted wholesale.

---

## SKILL.md as Compile Target (Export)

The Dream cycle (or manual `export(out_dir)`) renders, per consolidated+ engram, a vanilla `skills/<name>/SKILL.md`:

```yaml
---
name: proton-ge-proton-downgrade
description: |
  Downgrade GE-Proton when a Steam game regresses after an update.
  Use when: game crashes or performs worse immediately after GE-Proton update.
  Not when: game never worked on any version.
---
```

Body: Procedure/Pitfalls/Examples with stats stripped; human-readable.

**Purpose:** Harnesses that only speak the common SKILL.md format (stock Claude Code) get the sharpened routing text for free. The export diff shows exactly what sharpening changed. ENGRAM is source of truth; SKILL.md is a build artifact.

---

## MCP Tool Additions for Format Operations

| Tool | Class | Purpose | Risk |
|---|---|---|---|
| `register(path, format?)` | write | Ingest .egr.md (native) or SKILL.md (import) | R2 (filesystem read on provided path) |
| `sync()` | write | Refresh DB from registry files (full rebuild) | R2 (scans local registry) |
| `nucleate(trace_ids?)` | write | Manual induction trigger (else Dream-cycle auto) | R3 (writes to registry) |
| `sharpen(name, proposed_changes?)` | write | Manual sharpening pass; guided by proposed_changes | R3 (modifies engram) |
| `promote(name)` / `archive(name)` | write | Lifecycle transitions; requires sufficient evidence | R3 (modifies status field) |
| `export(out_dir)` | write | Render SKILL.md shims from consolidated+ engrams | R2 (filesystem write on provided path) |
| `checkpoint()` | write | Idempotent DB → file flush (Dream-cycle calls this) | R2 (writes to registry) |

---

## Why a Portable Format, Not Just a Database

[From exploratory engram-format §8, refined]

Co-locating routing data, plasticity checkpoints, fault attribution, and provenance **in the artifact itself** enables:

1. **Portability:** A skill moves across machines, harnesses, and projects without losing learned state (the synapses block travels with it).
2. **Git versioning:** The registry directory is a clean snapshot; version control gives every weight a history and rollback path.
3. **Auditability:** Provenance journal is human-readable; every change is attributed and timestamped.
4. **Self-describing:** Each engram declares its own state; no separate metadata database needed.
5. **Rebuilding:** Durable state (Tier A + B) survives DB corruption or reset via `register`/`sync`; only ephemeral state (Tier C) is lost.

**The result:** A skill *organism*, not a skill *library*.

---

## Migration from Existing SKILL.md Corpora

[GAP-005 resolution] Existing SKILL.md corpora are converted to `.egr.md` via `register()` with format=skill:

1. **Bulk import:** `register(path="skills/", format=skill)` scans the directory and converts all SKILL.md files.
2. **Provenance:** All imported engrams carry provenance=imported, status=nascent, verification_status=pending.
3. **Quality gate required:** Imported engrams must pass reconstruction check + rubric gate before moving to probation (auto-sharpening may help, but manual review recommended for first-class corpora).
4. **Learning enabled:** Once in probation+, learned state accrues normally.

---

*Section authored by IDG, 2026-08-14, drawing on exploratory/engram-format.md and incorporating FINDING-009/010 fixes.*
