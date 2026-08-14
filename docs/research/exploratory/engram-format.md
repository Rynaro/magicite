# ENGRAM Format Specification (v0.1-draft)

**A learnable skill-file format for the SYNAPSE synaptic graph engine**
Supersedes SKILL.md as the native skill representation; SKILL.md becomes a *compile target*.
Ecosystem: Eidolons (sibling to crystalium, atomos, tonberry; harnesses: Claude Code, ariramba)

---

## 0. Motivation

SKILL.md is a *static* format: a 100-token description is the sole routing signal, the body is opaque prose, and nothing in the file records whether the skill works. The 2026 evidence is blunt — LLM-authored skills give no measurable benchmark gain without a structured refinement loop (SkillAxe), routing on metadata alone loses 31–44pp of accuracy (SkillRouter), and flat registries decay logarithmically (Scaling Laws of Skills). Meanwhile the skill-learning frontier (Voyager, EvoSkill, MIND-Skill, W2S/Skill-IR, CASCADE) converges on skills as *trace-induced, feedback-refined, quality-gated artifacts*.

ENGRAM is the file format that makes a skill a **learnable object**: routing data, plasticity state, per-step fault attribution, composition contracts, and provenance — one file, human-readable, git-diffable, machine-actionable.

Name rationale: an engram is the physical trace of a memory. A learned skill *is* a consolidated memory trace. Files are engrams; the SYNAPSE graph is the connectome.

---

## 1. File anatomy

One file per skill: `engrams/<name>.egr.md`. No mandatory directory. Executable code lives in fenced `exec` blocks *inside* the file, extracted to a sandbox at runtime (integrates with the verifier-sandbox skill). Assets are the only legitimate reason for a companion directory (`<name>.assets/`).

```
---
<YAML frontmatter: identity · routing · plasticity · composition · provenance>
---
## Procedure      ← numbered steps, each carrying live confidence stats
## Pitfalls       ← observed failure modes with counts (learned, not authored)
## Examples       ← positive and negative invocation examples
## Provenance     ← append-only reconsolidation journal
```

### 1.1 Frontmatter schema

```yaml
# ── IDENTITY ──────────────────────────────────────────────
spec: engram/0.1
name: proton-ge-proton-downgrade      # 1–64 chars, lowercase-hyphen, == filename
id: egr_9f2c7a1d                      # content-hash of identity+routing blocks
version: 3                            # bumped on every sharpening event
provenance: sharpened                 # authored | imported | distilled | sharpened
parents: [egr_4b8e01cc]               # lineage: engrams/traces this derives from

# ── ROUTING (replaces `description` as the routing signal) ─
intent:                               # structured, not prose-only
  does: Downgrade GE-Proton when a Steam game regresses after an update
  use_when: game crashes or performs worse immediately after GE-Proton update
  not_when: game never worked on any version   # negative intent → inhibitory edge
triggers:
  positive:                           # ≥3 required; embedded at register time
    - "game X broke after proton update"
    - "rollback ge-proton for steam"
    - "new proton version regression"
  negative:                           # ≥1 required; trains trigger precision
    - "proton not launching at all"   # → routes to proton-clean-install instead
context_affinity: [steam, lutris, nvidia, fedora-kde]   # links to context nodes
embedding:                            # cached; refreshed by Dream cycle on drift
  model: bge-m3
  ref: sha256:…                       # vector lives in the graph DB, not the file

# ── PLASTICITY (consolidated checkpoint; hot state lives in SYNAPSE DB) ──
plasticity:
  storage_strength: 0.71              # S — slow, cumulative; checkpointed by Dream
  exposure_count: 34
  outcome: {success: 29, fail: 5}     # lifetime; per-step stats live in Procedure
  excitability: 0.05                  # exploration bonus; decays with exposure
  last_checkpoint: 2026-08-09T03:00:00-03:00
  status: consolidated                # nascent → probation → consolidated → promoted
                                      #                          ↘ archived (never deleted)

# ── COMPOSITION (first-class DAG edges) ───────────────────
needs: [steam-prefix-access]          # inputs required → plan chaining
yields: [working-ge-proton-version]   # outputs produced → matched to others' needs
composes: []                          # sub-engrams, if this is a distilled composite
inhibits: [proton-clean-install]      # mutual exclusion (lateral inhibition)

# ── EXPORT ────────────────────────────────────────────────
exports:
  skill_md: true                      # Dream cycle renders vanilla SKILL.md shim
```

### 1.2 Body schema

```markdown
## Procedure
<!-- steps carry live fault-attribution stats; sharpening is surgical, not wholesale -->
1. [ok: 31/33] Identify the game's Steam appid and prefix.
2. [ok: 29/34] Download the target GE-Proton build into compatibilitytools.d.
3. [warn: 18/26] Pin the version per-game via launch options, not globally.
   ← last sharpened v3: added after 4 failures traced to global pinning
4. [ok: 12/12] Verify with `PROTON_LOG=1 %command%` and inspect the log tail.

## Pitfalls
- (×4) Global version pinning breaks sibling games — always pin per-appid.
- (×2) NTFS-mounted libraries fail silently; check filesystem first.

## Examples
+ "Hades II stutters since GE-Proton 10-9" → full procedure
− "Steam won't open" → NOT this engram (route to steam-runtime-repair)

## Provenance
- v1 2026-06-14 · authored by Rynaro
- v2 2026-07-02 · sharpened: step 3 rewritten (4 fails, fault=global-pinning)
- v3 2026-08-09 · sharpened: negative trigger added (confused with clean-install)
```

---

## 2. Where state lives: the two-tier rule

The format enforces the neuroscience storage/retrieval split physically:

| State | Home | Mutated by | Analogy |
|---|---|---|---|
| Retrieval strength R, tags, candidate edges | SYNAPSE graph DB (SQLite) | Hot path (`route`, `signal_use`, `signal_outcome`) | Hippocampal fast trace |
| Storage strength S, outcome counts, per-step stats, status | The `.egr.md` file | Dream cycle checkpoint only | Cortical consolidated trace |

The file is **never written by the hot path**. Only the offline Dream cycle checkpoints DB → file (and file → DB on `register`/`sync`). Consequences: zero write contention during sessions, the registry directory is always a clean git-committable snapshot of consolidated knowledge, and every mutation is attributable to a logged Dream run. `plasticity.last_checkpoint` guards against stale-file restores.

---

## 3. How SYNAPSE learns new skills: nucleation

New engrams are born in the Dream cycle, not authored in the hot path (W2S-style trace-grounded induction):

1. **Trigger.** Either (a) a recurrent activation path — the same sequence of ≥2 engrams traversed ≥N times with positive outcomes and no single engram covering it (composite candidate), or (b) a high-salience success on a task where `route` returned nothing above threshold (coverage-gap candidate).
2. **Induction.** A local model (ariramba/Ollama) receives the session traces and drafts an engram through an intermediate representation — trace → structured IR (goal, preconditions, steps, observed pitfalls) → rendered `.egr.md`. Never raw trace summarization.
3. **Birth state.** `provenance: distilled`, `status: nascent`, `storage_strength: 0.1`, `excitability: 0.5` (high exploration bonus — engram-allocation analog). Parents recorded; composite candidates get `composes:` edges to the path's engrams.
4. **Quality gate (MIND-Skill-style, closed loop).** Before entering probation: *reconstruction check* (can the engram's procedure reproduce a held-out trace from its induction set?) and *rubric assessment* (local LLM scores trigger precision, step coverage, pitfall grounding). Failure → back to draft, no graph insertion.

## 4. How SYNAPSE sharpens existing skills

Sharpening is the SkillAxe loop, driven by the format's own instrumentation — fully unsupervised, no ground-truth labels:

- **Surgical step repair.** Per-step `[ok: n/m]` stats localize faults. Steps below a confidence floor are rewritten by the local model with the failure traces as context; the Pitfalls entry that motivated the rewrite is cited inline. Version bumps; Provenance logs the event (the reconsolidation journal).
- **Trigger precision.** Routing false-positives (this engram returned, another applied) generate **negative trigger examples**; false-negatives (silent-engram report: stored, never retrieved, but manually invoked with success) rewrite `intent.use_when`. This fixes *retrieval strength* problems at the file level — the silent-engram distinction made operational.
- **Inhibition learning.** Repeated confusion between two engrams materializes a mutual `inhibits:` entry and a disambiguating negative example on both sides — lateral inhibition, persisted.
- **Coverage audit.** Dream cycle checks `needs:` against the registry: a need no engram yields is a nucleation target; a yield nothing needs is a pruning candidate.

## 5. Lifecycle state machine

```
nascent ──gate pass──▶ probation ──S≥θ ∧ pass-rate≥φ──▶ consolidated ──▶ promoted
   │                      │                                    │
   └──gate fail──▶ draft  └──decay/fail floor──▶ archived ◀────┘ (S,R < floor)
```

- **probation:** routes with excitability bonus; stats accumulate; sharpening active.
- **consolidated:** normal routing; eligible for `skill_md` export.
- **promoted:** stable; sharpening requires higher evidence bar (metaplasticity at the file level — old, strong engrams resist change, exactly like saturated synapses).
- **archived:** excluded from routing, retained for provenance and possible revival (engram silencing is an access state, not deletion — per the 2024–26 reversibility findings).

## 6. SKILL.md as compile target

`synapse export` (or the Dream cycle) renders, per consolidated+ engram, a vanilla `skills/<name>/SKILL.md`: `description` synthesized from `intent.does` + `intent.use_when` + top positive triggers; body from Procedure/Pitfalls/Examples with stats stripped. Engram is source of truth; SKILL.md is a build artifact — harnesses that only speak the common format (stock Claude Code) get the sharpened routing text for free, and the export diff shows exactly what sharpening changed.

## 7. MCP tool additions

| Tool | Purpose |
|---|---|
| `register(path)` / `sync()` | Parse, validate (JSON Schema), embed triggers+procedure, wire similarity/affinity edges |
| `nucleate(trace_ids?)` | Manual induction trigger (else Dream-cycle automatic) |
| `sharpen(name)` | Manual sharpening pass (else Dream-cycle automatic) |
| `promote(name)` / `archive(name)` | Lifecycle transitions |
| `export(out_dir)` | Render SKILL.md shims |
| `checkpoint()` | DB → file plasticity flush (idempotent, Dream-cycle calls it) |

Lint rules enforced at register: ≥3 positive / ≥1 negative triggers, `not_when` present, every `needs` resolvable or flagged, Procedure steps numbered, Provenance append-only (violations = hard error; the format's discipline *is* the learning signal quality).

## 8. Why a format, not just a database

Co-locating routing data, plasticity checkpoints, fault attribution, and provenance **in the artifact itself** makes each skill self-describing, self-diagnosing, and portable across machines and harnesses — the graph DB becomes a rebuildable index rather than the only copy of the truth. Git gives the registry versioned memory for free; the Provenance journal gives every weight a story. That is the difference between a skill library and a skill *organism*.


