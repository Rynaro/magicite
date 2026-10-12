---
eidolon: ramza
version: 1.0.0
kind: spec
status: ready-for-implementation
created_at: 2026-09-29T13:50:00Z
target_repos: [Rynaro/magicite]
stories_count: 17
validation_gates_count: 17
---
# Magicite V1 — independently assignable delivery specifications

Start with [the slice index and dependency graph](magicite-v1/README.md). The machine-readable [dispatch plan](magicite-v1/plan.json) maps 17 slices to ownership, build/merge dependencies and 76 frozen criteria. Nothing in this packet implements runtime behavior or declares V1 release readiness.

## Scope

Intent: REQUEST, specs only. Digest the supplied V1 dossier, correct material research gaps, inspect pinned checkout `496e4eded09f7c0b8e0b776c3c709bccadc0321a`, define implementable slices, and publish a dedicated remote planning branch. The user explicitly rejects the dossier's calendar convention; bounded work packages and dependency gates replace day estimates and timeboxes. User authorization covers this packet's commit/push; no runtime implementation, deployment, release or external outreach is authorized by this task.

In: stable engram/1.0, migration, trust, conventional retrieval, constraints, abstention/explanations, typed composition, evidence/privacy, protocol/CLI, reliability, packaging, evaluation, documentation and release evidence. Out: host execution, service/marketplace/federation, mandatory learned ranking or large reranker. Optional: S10 learning promotion infrastructure; containment and evidence contracts remain mandatory.

Assumptions: existing local stdio architecture is retained; published-release source snapshots are available or must be recovered before claiming upgrade support; external operators and PyPI publisher setup are future release dependencies. Risk if wrong: a narrower supported matrix or a new scope amendment is necessary, not invented verification. Clarify/Discover skipped because goal, source material, specs-only boundary and publication destination are clear. Complexity gate: 9/12, extended reasoning; right-size: full, score 8.

## Approach

[DECISION] Use contract-first modular slices. Freeze shared vocabulary and ownership before agents work. Stable dense is the initial incumbent, hybrid must earn promotion, graph remains normative/explanatory for stable composition, and adaptation stays opt-in. All domains reuse existing authority and writer-safety mechanisms. This approach scored 89/100 through RAMZA Explore.

Pattern assessment: adapt existing system (60–84% planning fit category), not a greenfield rewrite. Existing restricted hot-path connections, durable approval mirrors, atomic engram writes and typed tool registry are strong matches; missing evidence/trust/context contracts prevent a template-only plan. Evidence: [scout report A07/A10/A14/A15](magicite-v1/scout-report.md). Memory lookup skipped because no Crystalium tools were exposed. Historical plans under `.spectra/changes/archive/` remain context, not active GA contracts.

Read in order:

1. [Shared contracts and decisions](magicite-v1/contracts.md).
2. [Source-backed scout](magicite-v1/scout-report.md) and [research corrections](magicite-v1/research/corrections.md).
3. [Evaluation contract](magicite-v1/evaluation.md) and [release gates](magicite-v1/release-gates.md).
4. Assigned slice and its dependencies; [handoff.yaml](magicite-v1/handoff.yaml).

## Stories

Each S00–S16 file is one bounded owner assignment containing a user story, source anchors, contracts, owned surfaces, action steps, atomic acceptance criteria and rollback/handoff requirements. S00, S01, S02 and S13 scaffolding can start immediately. Subsequent pure-module construction can use frozen contract fixtures; integration waits for `merge_after` and shared-file locks. No team size or elapsed schedule is assumed.

[The full slice index](magicite-v1/README.md) is the authoritative navigation page; [plan.json](magicite-v1/plan.json) is the dispatch representation. Executor hint: reasoning-capable agents following Vivi, with security/storage/schema changes receiving independent review. Small substeps can be delegated according to repository workflow, but P0 integration is not an unreviewed economy task.

## Acceptance Criteria

Canonical [acceptance.md](magicite-v1/acceptance.md) contains AC-S00-01 through AC-S16-04 plus six seam criteria and A02's two S09 export-deletion criteria (76 criteria); each is duplicated verbatim in its slice for standalone assignment. It is frozen with `ramza-freeze`; change requires an amendment and regeneration of both representations. Recorded amendments live under [magicite-v1/amendments](magicite-v1/amendments/) (A01 scopes AC-S02-01 preserved-bytes identity to the C1 payload surfaces; A02 names C6 export deletion local-management scope and adds AC-S09-05/AC-S09-06). VERIFY entries describe future evidence to produce, not tests run during planning.

This packet is complete only when references/JSON/dependency DAG/criterion coverage are checked, RAMZA structure/EARS/emission gates pass, independent critique is recorded and the remote branch contains the specs-only commit. Runtime CI/benchmarks/GA evidence remain unevaluated until implementation. [Verification record](magicite-v1/verification.md) distinguishes planning checks from future runtime checks.

## Confidence

Confidence is computed at Assemble through `ramza-score`; exact dimensions/verdict are in [state](magicite-v1.state.json) and [verification](magicite-v1/verification.md). It measures specification readiness, not probability of meeting empirical quality or release gates. Source-backed findings have individual H/M confidence; no empirical result is upgraded by a planning rubric.

## Rejected Alternatives

- **Freeze v0.3 and polish docs only** — 74.5/100. Low change risk, but leaves implicit adaptation, circular evaluation labels, missing applicability semantics and trust/recovery gaps. Reversal: consider a deliberately narrower 0.x maintenance release if V1 scope cannot be funded; do not call it fulfillment of this V1 contract.
- **Learned-router-first rewrite** — 73/100. Potential quality gain and innovation, but higher footprint, unsupported learning assumptions and weaker near-term reproducibility. Reversal: an independently verified candidate with better paired outcomes under equal constraints could justify a new optional policy spec.
- **Selected: governed contract-first slices** — 89/100. Preserves reusable mechanisms and parallel module work while making release evidence explicit. Reversal: measured interfaces fail to isolate authority or cause unacceptable operational cost; amend contracts with evidence before dependent integration.

## Risks

| Risk | Priority | Mitigation and reversal |
|---|---|---|
| Broad scope hides serial integration | P0 | Build/merge DAG and shared-file locks; independent modules before central binding work |
| Compatibility unknowns sharply reduce coverage | P0 | Explicit context_required, migration eligibility preview and measured coverage gate; never silently fail open |
| Evidence durability weakens hot-path safety | P0 | Explicit checkpoint boundary, separate authority, acknowledged-write crash matrix |
| Signature interpreted as approval | P0 | Separate trust dimensions, digest-bound local review and revalidation |
| New hybrid/learning fails quality gates | P0 | Keep strongest nonadaptive incumbent; optional learning cannot block GA |
| Final labels reused for tuning | P0 | Immutable preregistration/holdouts; failed final run needs new data before promotion |
| Retention conflicts with audit and rollback | P0 | Tombstones, backup expiry and restore-time privacy/revocation overlay |
| External evidence/publisher credentials absent | P0 release | Keep gate unevaluated until independently supplied; parallel code work continues |
| Scope grows through optional models/dashboards | P1 | Explicit optional/deferred status; any new contract needs amendment |

## Assemble and dispatch

```yaml
schema: magicite/handoff/1
plan: magicite-v1/plan.json
contracts: magicite-v1/contracts.md
criteria: magicite-v1/acceptance.md
executor: vivi
first_assignments: [S00, S01, S02, S13]
auto_merge: false
auto_deploy: false
```

A local domain-specific slice plan is emitted; it does not falsely claim to validate against Junction's unrelated transport-plan schema. The ECL sidecar uses `PROPOSE` to hand off the verified spec packet. All authored outputs stay under `.spectra/`; the parent publishes Git state under the user's explicit branch request, outside the read-only scouting role.
