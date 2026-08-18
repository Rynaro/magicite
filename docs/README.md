# Magicite: Organic Skill Learning & Refinement — Documentation Index

**Status:** Current design index for 0.3
**Scope:** Refined design layer (new, 2026-08-14)  
**Provenance:** docs/research/exploratory/ (frozen original layer)  
**Decisions implemented:** FINDING-013, FINDING-014, D1 (evidence policy)

---

Current normative interpretation starts with [AUTHORITY.md](AUTHORITY.md).
Archived construction records and `research/exploratory/` remain evidence and
context, but do not override the 0.3 acceptance criteria or runtime schemas.

## Overview

Magicite is the first **Organic Skill** obtention, evolution, and refinement tool, delivered as an MCP server usable by ANY coding agent. The vision: skills evolve through traced usage, not through static SKILL.md listings. This corpus documents the design, hypotheses, and learning model that make that vision feasible.

The project consists of:
- A **skill-artifact format** (.egr.md files, portable across hosts, carrying learning state)
- A **plasticity engine** (graph-structured, Hebbian-inspired, outcome-gated)
- An **offline consolidation worker** (Dream cycle, prunes, distills, renormalizes)
- An **MCP surface** (tools for routing, signaling outcomes, introspection)
- A **trust & governance model** (provenance-tiered, injection-surface-aware, rollback-capable)

Eidolons is the **first host** but not the only one. The architecture is deployment-profile-agnostic: a local-first stdio/SQLite v1 can later become a served HTTP/Postgres v2 without the engine changing. Sibling MCPs (crystalium for facts, atomos for context lifecycle, tonberry for change governance) exist in Rynaro's ecosystem; they are NOT dependencies — they are adapters for hosts that provide them.

---

## Reading Order

Start with the **Vision & Hypotheses** (01) to understand the problem, then:

- **Architecture** (02): engine structure and the deployment posture
- **Learning Model** (03): how skills evolve
- **Engram Format** (04): the portable skill-artifact spec
- **Protocol & Signals** (05): MCP surface and host integration
- **Trust & Governance** (06): lifecycle ops and artifact provenance
- **Evaluation** (07): fitness functions and benchmarks

Each doc can be read independently; cross-references are explicit.

---

## Terminology & Renames

| Old Term | New Term | Rationale |
|---|---|---|
| SYNAPSE (engine) | **Magicite** | Project name; SYNAPSE was a working title. Sibling MCPs are named for their function (crystalium = memory lattice, tonberry = change log); Magicite is both the product and the engine. |
| "Graphed Skills project" | **Magicite** | Same. |
| astroengrams | **context nodes** | Mechanism retained (skill-context bipartite graph); terminology aligned with standard feature-node design (GNN language). Biology becomes a footnote, not the naming source. |
| SYNAPSE graph / connectome | **skill graph** | Informal; "connectome" used only as flavor. |
| SYNAPSE→crystalium relationship | **unchanged** | crystalium = facts & observations (four-layer lattice). Magicite = capabilities & skills (weighted directed graph). The coupling is at the application layer (Dream writes distilled composites into crystalium; hosts choose integration). |

**Evidence policy (D1 directive):** Three 2026 sources cited in the exploratory corpus — SkillRouter (arXiv 2603.22455), "Scaling Laws of Skills", CompSkillBench — appear ONLY as named hypotheses with explicit unverified status. No quantitative claim from them may be stated as fact; each becomes a falsifiable hypothesis with its confirmation test in doc 07.

The six systems named at exploratory/engram-format.md:11 (SkillAxe, Voyager, EvoSkill, MIND-Skill, W2S/Skill-IR, CASCADE) are inspiration sources, unverified, uncited. Mechanisms they describe are adopted and re-derived from engineering principles.

---

## Relationship to Exploratory Layer

The five documents at `docs/research/exploratory/` remain byte-identical, frozen. They are the ground-truth source material:

- `consolidated-research-graph-mcp-proposal.md` — engine proposal (primary synthesis)
- `engram-format.md` — format v0.1 draft (extended specification)
- `neuroscience-knowledge-acquisition.md` — neuroscience digest (input material)
- `graphs-from-euler-to-today.md` — graph theory digest (input material)
- `mcp20-server-dossier.md` — production deployment reference (generic MCP guidance)

This refined layer (`docs/01-07.md` + index) is built FROM those sources, correcting contradictions (FINDING-009, FINDING-010, FINDING-011) and implementing design verdicts (D1–D6). The exploratory layer is never edited; refinements live here.

---

## Known Gaps & Research Agenda (deferred to v2+)

| Gap | Status | Notes |
|---|---|---|
| Multi-agent/multi-machine merge semantics | Deferred research | GAP-001: single-writer registries + export/import with review gate is the interim policy. CRDT-style merge for edge weights is explicitly rejected for v1 (semantics are unsolved at this scale). Documented in doc 06. |
| GNN representation layer | Deferred | Proposed at exploratory/consolidated-research-graph-mcp-proposal.md:59 but unnecessary at 10²–10⁴ nodes. Revisit only if node features (skill bodies, error contexts) demand learned embeddings beyond local embedding models. |
| Multi-tenant served profile | Deferred | v1 is local-first (stdio, SQLite). A served HTTP/OAuth/Postgres profile is a deployment profile, not an engine change. Implementation deferred to SPECTRA. |
| Fine-grained telemetry | Deferred | v1 ships `introspect` and a standing KPI (Hit@k vs registry size). Per-signal-tier yield measurement is an observability experiment (doc 07), not a first-release requirement. |

---

## Document Status Ledger

| Doc | Title | Status | Implements |
|---|---|---|---|
| 01 | Vision & Hypotheses | draft-refined | Problem statement; hypothesis register (H-BODY, H-SCALE, H-COMPOSE, H-LEARN) |
| 02 | Architecture | draft-refined | D2 verdict (local-first core, serving as deployment profile); five mcp20 disciplines |
| 03 | Learning Model | draft-refined | D4 verdict (neuroscience analogy table, corrected); D1 three-tier plasticity locus; P0 principle |
| 04 | Engram Format | draft-refined | Refined format spec v0.2 (synapses: frontmatter block, SKILL.md round-trip, three-tier rule) |
| 05 | Protocol & Signals | draft-refined | D3 verdict (Tier 0/1/2 signal ladder, provenance-weighted capture, hooks optional) |
| 06 | Trust & Governance | draft-refined | Trust model, lifecycle ops, approval machinery, interim sharing policy (ranks 2, 3, 9 from D5) |
| 07 | Evaluation | draft-refined | Fitness functions, benchmarks, per-tier signal-yield measurement, ablation plan |

All docs: human-veto-open on naming (SYNAPSE→Magicite adoption, ENGRAM retention, context-node terminology). No blocking items.

---

## How to Use This Corpus

**For implementation (SPECTRA):** Treat 02–07 as the spec foundations. Details are normative; examples are illustrative. Cross-references to exploratory originals (file:line) are for historical context, not binding.

**For evaluation:** doc 07 is your benchmark and ablation suite. Every 2026 quantitative claim from the hypothesis register gets a falsification test.

**For adoption by other hosts:** Read 02 (architecture boundary), 05 (tool surface), and 06 (governance). The engine design is host-agnostic. Integration examples (Claude Code hooks, crystalium interop) are adapters, not requirements.

---

*Composed by IDG (MAKER for ESL change `refine-exploratory-research-corpus`), 2026-08-14. Checker: Kupo (owns `verified` transition).*
