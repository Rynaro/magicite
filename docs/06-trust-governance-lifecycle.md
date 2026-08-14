# 06. Trust, Governance, and Lifecycle

**Status:** Draft-refined / v1  
**Provenance:** GAP-001, GAP-002, GAP-006, exploratory/mcp20-server-dossier.md sections 5–11, engram-format.md §5, D5 augmentation ranks 2/3/9  
**Decisions implemented:** Provenance-tiered trust model; governance via mcp20 approval machinery; lifecycle-op risk classes; rollback semantics; interim sharing policy (rank 9)

---

## Overview

Magicite creates and modifies skill artifacts autonomously (sharpening, nucleation, consolidation). It also imports artifacts from untrusted sources (SKILL.md corpora, model-generated engrams, remote shares). This section specifies:

1. **Trust model:** artifacts are tiered by origin (authored, sharpened, distilled, imported) and verification status
2. **Injection surfaces:** which engram fields are agent-facing and untrusted
3. **Governance:** how lifecycle ops (nucleate, sharpen, promote, archive) map to mcp20 risk classes and approval gates
4. **Rollback:** version history and recovery
5. **Interim sharing:** single-writer registries + export/import with review

---

## Trust Model for Skill Artifacts

[D5 rank 2] **Provenance-tiered trust:**

### Origin Tiers (Ordered by Trust)

| Tier | Origin | Meaning | Verification | Governance |
|---|---|---|---|---|
| **1** | `authored` | Human-written skill, intended for production | Strong (human code review) | Promotion to consolidated requires low evidence bar |
| **2** | `sharpened` | Authored skill that has been refined by Dream consolidation or manual sharpening | Strong (prior authorship + audit trail) | Same as authored |
| **3** | `distilled` | Skill synthesized by Dream from observed traces (no human original) | Medium (reconstruction check + rubric gate required) | Promotion requires high evidence bar (more sessions) |
| **4** | `imported` | Skill ingested from external source (SKILL.md, remote registry, model-generated) | Weak (unverified, content unknown) | Must pass quarantine-on-import gate; promotion deferred until locally-hardened |

### Verification Status (Orthogonal to Origin)

| Status | Meaning | Action |
|---|---|---|
| `verified` | Passed all trust gates (quality check, lint, no injection risk) | Eligible for routing after lifecycle gate |
| `pending` | Ingested, not yet verified | Quarantine; exclude from routing until review |
| `quarantined` | Explicit user flag (suspicious content, injection risk detected, failed lint) | Must be manually reviewed and approved before moving to pending |

**Example:** An imported engram starts `origin=imported, status=pending` and must be explicitly approved by a human or automated verifier to move to `status=pending→verified`.

---

## Injection-Surface Analysis: What's Untrusted

Model-written and imported engrams can introduce prompt-injection vectors. Identify and isolate:

### High-Risk Fields (Treat as Untrusted Input)

1. **intent.does, intent.use_when, intent.not_when**
   - LLM-generated text describing skill purpose
   - **Risk:** Instruction injection if rendered in routing suggestions or system prompts
   - **Mitigation:** These fields are shown to users and agents; sanitize for HTML/markdown injection; never eval or execute as code
   - **Policy:** Review for semantic absurdity (does the intent match the actual procedure?)

2. **triggers (positive and negative)**
   - Embedding keys matched against user queries
   - **Risk:** Adversarial trigger engineering (e.g., trigger on common queries to hijack routing)
   - **Mitigation:** Flag triggers that are too generic or match common patterns; during review, check for suspiciously broad coverage
   - **Policy:** Negative triggers are more dangerous (exclusion); require stronger evidence for imported engrams

3. **pitfalls**
   - Descriptive failure modes; should be factual
   - **Risk:** Low (mostly observational); used by humans not LLMs
   - **Mitigation:** Sanity-check for obvious false claims (e.g., "use rm -rf on your home directory")

4. **exec blocks (if present)**
   - Fenced code to be extracted and executed by the host
   - **Risk:** Very high. Host must sandbox execution; malicious code can damage filesystem, leak data, etc.
   - **Mitigation:** [From doc 02] Magicite never executes; the host does. Quarantine on import is the gate. The host's execution sandbox (Claude Code audited bash, ariramba container, etc.) is the enforcement boundary, not Magicite.
   - **Policy:** Flag engrams with exec blocks in quarantine; require explicit human approval if importing from untrusted source

### Low-Risk Fields (Acceptable to Trust Minimally)

- `name`, `id`, `spec`, `version`, `provenance`, `parents` — structural/metadata
- `plasticity.*` — checkpointed state, not modifiable without going through Dream
- `synapses.*` — learned edges, same
- `provenance_journal` — append-only audit trail
- `needs`, `yields`, `composes`, `inhibits`, `affinity` — declared composition DAG (verifiable against registry)

---

## Governance: Lifecycle Operations and Risk Classes

[D5 rank 3] Magicite's lifecycle operations (nucleate, sharpen, promote, archive) modify the registry and can have system-wide effects. Map each to mcp20 risk classes and gate with approval machinery.

### Risk Class Mapping

From mcp20-server-dossier.md §5 (:248–277):

| Risk | Meaning | Magicite Ops | Approval Gate |
|---|---|---|---|
| **R0** | No side effect | `route`, `introspect`, `flag_dead` | None (read-only) |
| **R1** | Write ephemeral state | `signal_use`, `signal_outcome` | None (reversible, not durable) |
| **R2** | Write local filesystem or ephemeral DB | `register`, `sync`, `export`, `checkpoint` | None if path is within project; else Warn+Ask |
| **R3** | Write durable registry state or modify lifecycle | `nucleate`, `sharpen`, `promote`, `archive`, `consolidate` | Review-gate by default; opt-in autonomous mode |
| **R4–R5** | Trigger external actions, raw shell/SQL | Not implemented in v1 | N/A |

### Approval State Machine (from mcp20 §10)

[Reuse from mcp20-server-dossier.md :316–376]

```
Proposed
   ↓
[Human review / automated verification]
   ↓
Approved ─→ Executed ─→ Succeeded (logged)
   ↓
Rejected (logged)
   ↓ (optionally)
Appealed ─→ Re-review
```

**Magicite instantiation:**

| Op | Default | Approval | Flow |
|---|---|---|---|
| `nucleate` (R3) | Requires review | Manual approval or "autonomous mode" opt-in | Proposed → review → approved → executed (Dream schedules induction) |
| `sharpen` (R3) | Requires review | Manual approval | Proposed → review → approved → executed (Dream schedules rewrite) |
| `promote` (R3) | Evidence-gated | Automatic if evidence bar met; else requires manual approval | Check evidence (S ≥ θ, pass-rate ≥ φ) → auto-promote if pass; else propose + await review |
| `archive` (R3) | Requires review | Manual approval | Proposed → review → approved → executed (moved to .spectra/archive/) |

### Autonomous Mode (Opt-In)

Users may enable `--autonomous` to skip approval gates:

```bash
magicite consolidate --autonomous
```

**Effect:** R3 ops execute directly without review (convenience for trusted registries). Default is `--review` (safe, approval-gated).

**Audit trail:** All ops logged with actor (human, dream-worker, autonomous-mode) and approval status. Logs are immutable; rollback is possible via version history.

---

## Lifecycle State Machine (Approval-Gated)

```
             [gate pass]
                  ↓
nascent ───────→ probation ───→ consolidated ───→ promoted
   ↓                 ↓              ↓
 draft ← [gate fail] └── archived ◀─┘
```

### Transitions and Approval

**Nascent → Probation:**
- Requires: reconstruction check passed (engram can reproduce held-out traces)
- Requires: rubric assessment passed (LLM scores triggers, step coverage, pitfalls ≥ threshold)
- Approval: Automatic if tests pass; manual review if marginal
- Who triggers: Dream (auto on nucleate) or `promote()` manual

**Probation → Consolidated:**
- Requires: S ≥ θ_consolidate (e.g., 0.6) AND pass_rate ≥ φ (e.g., 90%)
- Approval: Automatic if evidence met; else manual review
- Who triggers: Dream (auto on consolidation cycle) or `promote()` manual

**Consolidated → Promoted:**
- Requires: Very high evidence (S ≥ 0.85, pass_rate ≥ 98%) OR explicit manual promotion
- Approval: Automatic if evidence met; else requires explicit manual approval (high bar)
- Who triggers: Dream (auto if evidence met) or `promote()` manual

**Any → Archived:**
- Triggers: S < floor_archived (e.g., 0.2) OR explicit `archive()` call
- Approval: Automatic if decay-floor reached; manual review required for explicit archive
- Who triggers: Dream (auto on decay) or `archive()` manual

---

## Rollback and Version History

[D5 rank 3, operational piece]

### Version Control

- **Registry is git-repository-friendly:** All `.egr.md` files and `skill-graph.db` reside in `.spectra/` and can be committed.
- **Provenance journal:** Every `.egr.md` carries an append-only journal in the file body; every modification is logged with timestamp, actor, and reason.
- **Archive directory:** `.spectra/archive/` contains all archived engrams, timestamped (never deleted).

### Rollback Paths

1. **Git rollback (entire registry):**
   ```bash
   git checkout HEAD~1 -- .spectra/
   magicite sync  # rebuild DB from files
   ```

2. **Single-engram rollback:**
   ```bash
   git checkout HEAD~N -- .spectra/engrams/<name>.egr.md
   magicite sync
   ```

3. **Restore from archive:**
   ```bash
   cp .spectra/archive/2026-08-09-<name>.egr.md .spectra/engrams/<name>.egr.md
   magicite sync
   ```

4. **Dream-run undo:** Dream consolidation writes are idempotent and audited. A consolidation can be undone by restoring the previous checkpoint timestamp:
   ```bash
   # Hypothetical future tool
   magicite rollback-consolidation --to <consolidation_id>
   ```

---

## Interim Sharing Policy (Rank 9 from D5 — Deferred Research Component)

[GAP-001 partial resolution] v1 ships a **single-writer, export/import with review-gate** model.

### Current Policy (v1)

1. **Single-writer per registry:** Each project registry has one Dream worker (single writer to durable state). No concurrent writes.

2. **Export engrams for sharing:**
   ```bash
   magicite export out_dir=./export/
   ```
   Renders consolidated+ engrams as `.egr.md` files with full provenance.

3. **Import with review-gate:**
   ```bash
   magicite register path=./import/ --format=egr
   ```
   Ingests shared engrams as `origin=imported, status=pending, verification_status=pending` (quarantine). Requires explicit human review + approval before entering routing.

4. **Review workflow:**
   ```
   Shared engram (imported, pending)
     ↓
   Human inspection (check intent, triggers, injection risk)
     ↓
   Approve or reject
     ↓
   If approve: promote to probation; start learning
   If reject: archive with explanation
   ```

### Why Not CRDT-Style Merge (v1)?

Merging learned edge weights from two independent registries is unsolved:
- How do you combine S_edge values from two unrelated training histories?
- How do you resolve conflicts (edge A→B learned in registry-1 with S=0.8, but inhibited in registry-2)?
- The Hebbians analogy breaks: weight merge has no biological equivalent.

**v2+ research:** Graph-merge protocols may exist (e.g., vector-clock timestamps, convergent semantics), but it's premature. v1 keeps it simple: single writer + explicit export/import.

### Deferred: Remote Sync & Consensus

A future iteration might add:
- Central registry server with consensus protocol
- Multi-host conflict resolution (CRDTs or quorum-based)
- Version negotiation (engram A at v3 on host-1, v4 on host-2)

Currently out of scope. Single-writer per project is sufficient for the first-release use cases (local agent, team-project with designated curator).

---

## Trust Checklist for Imported Engrams

When importing an engram (e.g., from a shared repository or third-party source):

- [ ] **Origin**: Is the original author known and trusted? (Check provenance_journal)
- [ ] **Injection risk**: Do intent, triggers, or pitfalls contain suspicious instructions or LLM-generated text that looks like prompt injection?
- [ ] **Exec blocks**: Are there any? If yes, **mandatory manual review** before import. Source code inspection required.
- [ ] **Composition DAG**: Do `needs`, `yields`, `composes` refer to engrams that exist in your registry? Dangling references are inert but should be resolved or documented.
- [ ] **Evidence**: How many sessions / successes does the engram have? Imported engrams with zero evidence start in `nascent` status and must accumulate local evidence before promotion.
- [ ] **Version**: Is the imported engram's version > 1? Sharpened engrams are lower-risk than first-draft engrams.

---

## Security Boundary Summary

- **Magicite stores and routes artifacts.** It does not execute code, verify signatures, or sandbox untrusted content.
- **Hosts execute engram code.** The host's sandbox (Claude Code bash, ariramba container, Python runtime) is the execution boundary. Hosts decide whether to trust an engram's exec blocks.
- **Approval gates are the trust enforcement.** Imported engrams are quarantined until explicitly reviewed and approved. Lifecycle promotions require evidence (pass-rate, outcome signals) or manual override.

---

*Section authored by IDG, 2026-08-14, implementing D5 ranks 2, 3, and 9.*
