---
title: "Magicite v1 Implementation — Lifecycle Chronicle"
change_id: "magicite-v1-implementation"
archived_at: "2026-08-15"
archive_path: ".spectra/changes/archive/2026-08-15-magicite-v1-implementation"
status: "archived"
esl_version: "1.1"
---

# Lifecycle Chronicle: Magicite v1 Implementation

## Executive Summary

The Magicite v1 implementation specification was proposed on 2026-08-14, underwent eight planned development milestones (M0–M7), encountered a design gap discovered during post-verification audit that triggered an escalation from `verified` back to `in_progress`, underwent repair and re-verification, and is now archived on 2026-08-15 with a conformant outcome.

**Critical distinction:** The change is **conformant** — every frozen acceptance criterion (AC-001 through AC-033) passes, and the frozen anchor (`acceptance-criteria.md`, sha256 `7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`) never moved through five recorded amendments and remained byte-identical. The product hypothesis it was built to test — that authored edges (declared via YAML `needs`, `composes`, `inhibits`) could contribute to routing without being learned — was **not supported**. Risk R10 registered this falsification possibility in advance, and its mitigation fired as designed.

Conformance and validity are distinct attestations. This chronicle records both.

---

## Milestones and Implementation Arc (M0–M7)

Eight planned deliverables structured the work:

| Milestone | Delivered | Content |
|-----------|-----------|---------|
| **M0: Walking Skeleton** | ✓ | MCP stdio handshake, 16-tool surface scaffold, acceptance test harness; confirmed MCP 2.0 adoption (A1-REVISED) and signed off by Kupo; 84 tests pass |
| **M1: Storage Layer** | ✓ | SQLite WAL, Tier A/B/C durability split, schema DDL with signal tables, sync/rebuild procedure validated under data-loss scenarios |
| **M2: Router & Activation** | ✓ | Dense embedding via fastembed, kNN graph construction, PPR diffusion, hub-penalty PageRank, excitability and inhibition call sites; AC-023 exercised on authored states (AC-034 later) |
| **M3: Signal Ladder** | ✓ | Server-side Tier assignment (Tier-0/1/2), P0 authorizer guard, refractory window on R bump, per-engram rate limits; executed adversarial review (R1-RESTATED); 9 commitment levels defined |
| **M4: Dream Worker** | ✓ | Seven-phase consolidation pipeline, Hebbian potentiation, spacing-gated commitment, decay-at-read, retroactive-credit bounds, eph_tag-only S mutation, cross-process lease on registry write; R1's mitigation restated (temporal, not authenticational) |
| **M5: Data-Loss Fixes** | ✓ | Cross-process lease hardened, archival requires genuine prior peak ≥ floor_archived, dropped-row recovery; signal hygiene under concurrent consolidation |
| **M6: Session State & Grace Floor** | ✓ | Per-session co-activation window, decay vectors, `session_end_tag_grace_s` runaway protection, live-tag suppression floor |
| **M7: Container & Release** | ✓ | Hardened `--cap-drop ALL --security-opt no-new-privileges` image, AC-031 torch-free, offline stdio handshake validated; async Python 3.11, no network at startup |

---

## The Escalation and Re-Verification (Post-Verification Audit)

### What Triggered the Escalation

After Kupo's initial verification (33/33 frozen criteria passed, 9/9 validation gates green, 457 tests, 94.04% coverage, drift check passed), an internal audit discovered that author-declared edges (`needs`, `composes`, `inhibits` declared in `.egr.md` files) contributed **zero activation mass to routing** — permanently and by design.

**[DECISION]** This was escalated from `verified` → `in_progress` because it represented a **spec defect**, not an implementation bug. The specification had asked for `plan_confidence` to be computed from declared-edge strengths, but no code path could ever make a declared edge carry weight: storage inserted them at `storage_strength=0.0`, Dream could only potentiate `co_activation` edges, and activation weight was dropped for `w <= 0`. The gap was three design links, each verified in source.

### Scope of Amendments

Five errata records were written and chain-restamped into the envelope (`spec.envelope.json`):

1. **A1-REVISED (2026-08-14):** MCP framework path revised on execution evidence from `mcp.server.fastmcp.FastMCP` (official SDK 1.x) to `mcp.server.lowlevel.Server` (official SDK 2.x), bringing protocol coverage gains and simplification. Contained within declared scope; 13/14 verification conditions passed; 1 deferred to M7.

2. **R1-RESTATED (2026-08-15):** Risk R1's mitigation cited a per-session Tier-1 cap (3 dw/skill/session) as an anti-poisoning control. Executed adversarial review drove 253 tags against it because `session_id` is caller-supplied and unauthenticated. Restated to object-keyed and temporal bounds (per-engram refractory on R bump, decay-at-read, spacing-gated potentiation, retroactive-credit bounds). Cap demoted to runaway protection; residuals openly stated.

3. **DECLARED-EDGES-AMENDED (2026-08-15):** Formal spec amendment. New normative section 3.3.1 defines `S_eff = max(storage_strength, w_authored)`, where `w_authored = declared_edge_strength` (new Config knob, default 1.0) for declared provenance, 0.0 for learned. Computed at read, never stored; separates authored channel from Hebbian channel without breaking "weight is earned" principle. Amended route steps 4, 5, 9, 10, community weighting, introspect, baseline (c) in bench. Two routing defaults moved: `ppr_restart 0.15 → 0.85` (measured), `w_retrieval 0.15 → 0.05` + `w_similarity 0.30 → 0.40` (precautionary). Nine new criteria (AC-034 … AC-042) in `acceptance-criteria-addendum.md` (NOT frozen, NOT merged into frozen set). AC-023 NOT edited — coverage defect, not rewrite.

4. **R12-FIRED (2026-08-15):** Release obligation to re-run cold 210-query benchmark discharged. `ppr_restart=0.85` confirmed on new graph shape (recovery of baseline (c) 0.4333 → 0.5286, baseline (d) 0.4905 → 0.5476). `declared_edge_strength=1.0` confirmed against −0.0286 Hit@1 delta (six queries in 210, p-ceiling 0.031) because baseline (c)'s diffusion arm carries full declared mass with NO inhibition and is IDENTICAL across both runs (0.5286). The channel being debugged (diffusion) measured inert; the delta arose in inhibition or community re-clustering, never isolated (MO-3 not discharged). Four pre-registered reversal conditions (RC-1 … RC-4) recorded.

5. **INHIB-GAIN-RECALIBRATED (2026-08-15):** RC-1 fired and discharged both halves. `declared_edge_strength=1.0` confirmed true (RC-1b: baseline (c) responds to the knob at 5.0, so its 0.0 vs 1.0 invariance is a real corpus property). Inhibition identified as the channel (RC-1a: 84% of the gap, derived as `theta_synapse × 0.7`). `inhib_gain 0.7 → 0.245`, NOT selected from sweep (prevents overfitting on single-author corpus) but derived from design constants and corroborated by Pareto frontier. Sweep published; MO-3 discharged; R12 narrowed to single-corpus generalization.

### Why Re-Verification Succeeded

- **Frozen anchor untouched.** Acceptance criteria file remains byte-identical (`7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448`). No criterion was edited or re-frozen. AC-023 was never changed.
- **Code inside declared scope.** A1-REVISED touched only adapter files and tests already within scope. R1-RESTATED touched only implementation (`src/`). DECLARED-EDGES-AMENDED touched implementation and bench, no structural change to DDL or core APIs.
- **Validation gates re-run.** All nine VG commands were re-executed after DECLARED-EDGES-AMENDED. All nine passed. Test suite grew from 457 to 476 (AC-034 … AC-042 added).
- **Coverage of new claims.** AC-034 … AC-042 provide the cover that AC-023 lacked (ingestion path named, effective_strength field pinned, cycle-break determinism, etc.).

---

## The Conformance Outcome

### Frozen Criteria: All 33 Pass

Every criterion in `acceptance-criteria.md` (AC-001 through AC-033) was re-attested after all amendments. All 33 frozen criteria pass:

- **Event-driven criteria (AC-001, 003, 006, 008, 009, 011, 012, 014, 015, 017, 018, 019, 020, 021, 023, 025, 026, 028, 029, 030, 032, 033):** 22 pass.
- **State-driven criteria (AC-010, 022, 027):** 3 pass.
- **Ubiquitous criteria (AC-002, 004, 005, 007, 024, 031):** 8 pass.
- **Unwanted-behavior criteria (AC-013, 016):** 2 pass.

### Addendum Criteria: All 9 Pass

The nine new criteria in `acceptance-criteria-addendum.md` (AC-034 through AC-042) are NOT frozen and NOT part of the frozen set, but all 9 pass:

- AC-034: Inhibition reachable from `register()` path (event-driven).
- AC-035: Declared edge enters activation graph with correct weight (event-driven).
- AC-036: Authored weight never persisted to `edge.storage_strength` (state-driven).
- AC-037: `plan_confidence` equals 1.0 when all declared needs resolve (event-driven).
- AC-038: `plan_confidence` reports unresolved share (event-driven).
- AC-039: Zero declared strength exactly reverts routing (unwanted-behavior).
- AC-040: Edge weight derived only via `effective_strength` helper (ubiquitous).
- AC-041: Introspect carries `effective_strength` field (event-driven).
- AC-042: Cycle-break deterministic over identical registry (event-driven).

### The Validity Outcome (Product Hypothesis)

The specification was built to test a hypothesis: that authored edges, declared at design time in skill YAML, could contribute routing mass without being learned by Dream (Hypothesis H-BODY-b: "authored mutual exclusion and composition contribute to discovery ranking").

**Measurement result:** Hypothesis not supported.

- **Baseline (c)** carries declared `composes`/`depends_on` into the diffusion graph at full authored mass with no inhibition and no community re-clustering: 0.5286 Hit@1 at both `declared_edge_strength=0.0` and `declared_edge_strength=1.0` — the channel being tested measured exactly inert (0/210 top-1 queries moved).
- **Baseline (d)** (full routing with inhibition and community re-clustering) shows −0.0286 Hit@1 (six queries in 210, p-ceiling 0.031) going from 0.0 to 1.0 declared strength, but the delta arises in a channel the run never isolated (inhibition magnitude uncalibrated for `S=1.0`; community re-clustering unseparated from inhibition effect).
- **Negative product finding recorded:** H-BODY-b did not improve routing. Even with inhibition off and declared strength at its peak, baseline (d) still trails baseline (b) (dense embedding alone) by one query (0.5429 vs 0.5476).

This is not a failure — Risk R10 recorded the falsification possibility in advance and its mitigation (invest in authored edges now, continue if measurement supports it; otherwise archive the bet) fired as designed.

---

## Verification Summary

| Artifact | Status |
|----------|--------|
| **Frozen acceptance criteria** | 33/33 pass; byte-identical anchor held; not re-frozen |
| **New addendum criteria** | 9/9 pass; not frozen; tamper anchor is addendum's own sha256 |
| **All 9 validation gates** | Re-run, all 9 pass (VG-1 through VG-9) |
| **Test suite** | 476 tests, 94.23% coverage; 457 → 476 test growth from new criteria |
| **Drift check** | Passed; specifications and implementations reconciled |
| **Maker ≠ Checker** | Vivi (maker) ≠ Kupo (checker); C4 gate holds |

---

## Carry-Forward Items (Outliving This Change)

These architectural issues were discovered but remain open because they cross into v2 territory or require broader corpus validation:

1. **Archive-Rebuild Gap (CF-A):** The synapses checkpoint issue — learned edge `storage_strength` values being lost when `sync()` was called after deleting `skill-graph.db` — was identified as spec drift during the post-verification audit, escalated from `verified` back to `in_progress`, and **fixed at commit fbb90c1** (`wire_synapse_edges()`). The fix was hand-verified: a learned edge authored into `.egr.md`, full database deletion, and rebuild from files alone recovers the edge at full precision. The round-trip is now complete and durable.

   The genuine remaining gap is different: `sync()` does not rescan `.spectra/archive/`, so on a full delete-and-rebuild, *archived* engrams' rows are lost. Normal `sync()` (with archive folder present) preserves them. For v2: consider archive inclusion in the durability contract, or a separate archive-restore procedure.

2. **Deferred Signal Bounds (CF-B):** Per-call fan-out on co-activation edges uncapped (observed: 380 edges from one 20-id call). Per-call retroactive-credit breadth capped at 10 but cap-burning attack possible (honest caller names another's session, burns their quota). Per-engram window cap unmeasured; temporal decay sufficient for recovery path. For v2: consider per-call edge budget, cross-session trust model, or identity-based quotas.

3. **Reversal Conditions — A1-REVISED Framework Path (from A1-REVISED decision record):**
   - **A1-RC-3:** By M7, `mcp` 2.0.x has received zero patch releases AND an open defect on stdio + tools/list + tools/call path affects Magicite. Decision: re-evaluate the **pinning strategy** (exact-pin, vendor a patch, carry a local fix), not the framework choice. 1.x is not a refuge: equally frozen with a stricter bar.
   - **A1-RC-4:** A stable `fastmcp` 4.x on `mcp` 2.x base ships AND Magicite later needs the served HTTP/OAuth profile (v1 `out_of_scope`). Decision: reopen standalone-`fastmcp` option **for the served adapter only**, never for stdio.
   - **A1-RC-5:** Upstream publishes a 1.x EOL date, or un-freezes 1.x and backports `2026-07-28`. Decision: **no change either way.** 2.x already chosen.

4. **Reversal Conditions — Routing Thesis Validation (from R12-FIRED and INHIB-GAIN-RECALIBRATED decision records):**
   - **RC-1 — DISCHARGED (2026-08-15):** Inhibition isolation (MO-3 re-measurement at commit 9963b89). **Result:** Inhibition accounted for **84% of the 0.0286 Hit@1 gap** between `declared_edge_strength=0.0` and `1.0` (from 0.5429 with inhibition off to 0.5190 at shipped `inhib_gain=0.7`). Baseline (c)'s invariance between 0.0 and 1.0 declared strength proved real (responds at extreme 5.0: 0.5286 → 0.5333), confirming the −6 query loss was not a harness artifact but a real corpus finding. Discharged both halves of the caveat: inhibition is the identified channel; baseline (c) responds to the knob.
   - **RC-2:** A second, independently-authored corpus (relations written by a different agent than the queries) plus a paired McNemar test. This removes the single-author confound and replaces "6 in 210" with a p-value. Independence must be in the **authorship of the relations**, not just the queries.
   - **RC-3 — PRIORITY RAISED:** A compositional query set (CF-7, raised from nice-to-have to live condition). H-COMPOSE has never been tested; single-target Hit@1 is structurally near-adversarial to composition edges, making a null on Hit@1 uninformative about composition. A null in RC-3 would be decisive. If RC-3 also returns null, the diffusion channel ships off by default regardless of RC-1's outcome.
   - **RC-4:** A registry with known-good declared relations (constructed so `needs`/`composes`/`inhibits` are correct by fiat). Distinguishes "the design does not help" from "these particular authored relations are noise."
   - **RC-5:** Hit@1 is the **metric of record**. The sweep at RC-1 showed Hit@3 peaking at `inhib_gain=0.3` (0.7571) while Hit@1 peaked at 0.0 (0.5429). If a second corpus reproduces the Hit@3 peak with Hit@1 flat or degraded, it becomes a product question for human judgment, not a measurement fact.

   **A second independently-authored skill corpus (beyond the 70-engram benchmark set)** is required to settle RC-2 and RC-3, and to validate whether the routing defaults (`ppr_restart=0.85`, `inhib_gain=0.245`, `w_retrieval=0.05`) generalize beyond the single-author benchmark.

5. **VC-11 — CLOSED (M7, 2026-08-15):** Verification condition on `truststore` and `opentelemetry.trace` imports inside hardened image was verified at M7 release. Executed: `docker run --cap-drop ALL --security-opt no-new-privileges --user 10001 --entrypoint python magicite:verify -c "import truststore, opentelemetry.trace"` → exit 0. Now a permanent CI step in `ci.yml`.

---

## Amendment Chain (Tamper Evidence)

The specification moved through five amendments, each recorded and integrity-tagged:

```
sha256 chain (spec.md):
  92372ad6... (original, emitted 2026-08-14)
  → 9fca1c08... (A1-REVISED, 2026-08-14)
  → 57148a2d... (R1-RESTATED, 2026-08-15)
  → e9efab60... (DECLARED-EDGES-AMENDED, 2026-08-15)
  → 757313fc... (R12-FIRED, 2026-08-15)
  → 6d3bff8e... (INHIB-GAIN-RECALIBRATED, 2026-08-15)
```

Each amendment is recorded in `decisions/<id>.md` (append-only), with its authorizing verdict, evidence, implementation commits, and unchanged artifacts explicitly listed. The ECL envelope (`spec.envelope.json`) carries the full chain in `x_ramza_amendment[]` and is re-stamped once per amendment so receiver verification does not report false tamper alarms on authorized changes.

---

## Provenance & Metadata

- **Change ID:** magicite-v1-implementation
- **Archive Path:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation`
- **Created At:** 2026-08-14
- **Archived At:** 2026-08-15
- **Status:** archived
- **Tier:** full
- **Maker:** vivi
- **Checker:** kupo
- **Frozen Criteria Anchor:** `7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448` (unchanged)
- **Spec Ref:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/spec.md`
- **Acceptance Criteria Ref:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/acceptance-criteria.md`
- **Addendum Criteria Ref:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/acceptance-criteria-addendum.md`
- **Decision Records:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/decisions/` (A1-REVISED.md, R1-RESTATED.md, DECLARED-EDGES-AMENDED.md, R12-FIRED.md, INHIB-GAIN-RECALIBRATED.md)
- **Plan State:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/plan-state.json` (phases M0–M7 complete, errata[] records all five)
- **Envelope:** `.spectra/changes/archive/2026-08-15-magicite-v1-implementation/spec.envelope.json` (ECL v2.0, integrity chain recorded)

---

## Governance Notes

This record is written in the archive folder and is immutable (part of the archived artifact set). It documents what was delivered, what was amended, why, and what remains open. The frozen acceptance-criteria anchor serves as the tamper-evidence checkpoint; the amendment chain and decision records provide the rationale.

The promotion envelope (`promotion.envelope.json`) routes this change to semantic memory (CRYSTALIUM) for later recall. Future Magicite v2 work may reference this archive as prior art, particularly for the signal bounds, the authored-edge design tension, and the measurement obligations (MO-3, RC-5) that remain live.

---

*Chronicle recorded by IDG on 2026-08-15 during the archived hop of ESL lifecycle magicite-v1-implementation.*
