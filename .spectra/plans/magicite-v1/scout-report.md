---
kind: scout-report
eidolon: atlas
ecl_version: '2.0'
decision_target: Define independently assignable V1 implementation slices from pinned repository evidence.
scope:
  entrypoints: [src/magicite/__main__.py, src/magicite/mcp/app.py]
  modules: [engram, core, storage, mcp, eval, obs, embeddings]
  excluded: [runtime execution, implementation]
findings_count: 23
gaps_count: 1
---
# ATLAS scout report — Magicite V1

MISSION-ID: magicite-v1. Baseline: `496e4eded09f7c0b8e0b776c3c709bccadc0321a` (same as dossier). Decision: which reusable mechanisms and missing contracts determine independent V1 slices? Static inspection only; no runtime test, benchmark, installation or signature verification performed. Confidence H means direct source/artifact evidence; M means bounded-search inference, not proof of absence. Paths/line anchors refer to the pinned baseline, not future implementation.

## Findings and handoffs

| ID | Finding | Evidence and confidence | → RAMZA slice |
|---|---|---|---|
| A01 | Production scoring blends graph activation, dense similarity and learned state; communities enabled by default | `src/magicite/core/router.py:399-459`, `:488-493`; `src/magicite/config.py:136-139`, `:247` — H | S00/S07 |
| A02 | Dense candidates scan routable vectors; current routing text includes numbered steps but omits raw prose, name, pitfalls and examples | `src/magicite/core/router.py:357-384`; `src/magicite/core/registry.py:89-104` — H | S05 |
| A03 | Model name namespaces embeddings; body hash does not cover the full frontmatter-plus-body projection | `src/magicite/core/registry.py:220-245`; `src/magicite/embeddings/base.py:25-36` — H | S05/C11 |
| A04 | Context boosts/preferences are not hard compatibility checks | `src/magicite/core/router.py:245-303`, `:467-497` — H | S06 |
| A05 | Resolved dependencies enter closure without eligibility recheck | `src/magicite/core/composition.py:44-61`, `:100-114` — H | S06/S08 |
| A06 | Cycles are broken to continue and router exposes order/confidence without warning status | `src/magicite/core/composition.py:161-208`, `:247-263`; `src/magicite/core/router.py:525-538` — H | S08/S11 |
| A07 | Only engram/0.2 is modeled; stable IDs and atomic writer are reusable | `src/magicite/engram/model.py:134-175`; `src/magicite/engram/ids.py:1-7`, `:49-56`; `src/magicite/engram/writer.py:334-356` — H | S02/S03 |
| A08 | Raw prose and imported SKILL.md extras survive parsing/import, but body loading renders numbered steps only | `src/magicite/engram/parser.py:87-105`; `src/magicite/engram/skillmd.py:270-307`; `src/magicite/mcp/bind_retrieval.py:104-121` — H | S02/S05/S11 |
| A09 | Verification is derived by server, but native intake supplies file-declared origin to authored auto-verification | `src/magicite/core/registry.py:296-309`, `:457-469`; `src/magicite/core/lifecycle.py:136-142` — H flow, M exploitability; no exploit run | S04 |
| A10 | Public imported/distilled review path is explicitly missing; approvals already have durable mirrors/audit | `src/magicite/core/lifecycle.py:103-142`; `src/magicite/core/approvals.py:10-47` — H | S04 |
| A11 | Hook token establishes source tier, not task-verifier correctness; eph_event and session counts expire | `src/magicite/core/signals.py:44-54`; `src/magicite/storage/migrations/001_init.sql:165-169`; `src/magicite/core/lifecycle.py:153-174` — H | S09 |
| A12 | Raw route query reaches event storage despite generic digest handling | `src/magicite/core/router.py:542-549`; `src/magicite/obs/events.py:42-64` — H | S00/S09 |
| A13 | Dream directly updates incumbent node and edge strength | `src/magicite/core/dream.py:246-294`, `:349-368` — H | S00/S10 |
| A14 | Hot-path restricted connection, authorizer, lease fencing and transactional migrations already exist | `src/magicite/mcp/app.py:27-39`, `:86-100`; `src/magicite/storage/authorizer.py:45-79`; `src/magicite/storage/lease.py:150-161`; `src/magicite/storage/db.py:51-84` — H | S03/S09/S12 reuse |
| A15 | Tool registry and strict validation/idempotency are existing integration seams; async handler dispatches synchronously | `src/magicite/mcp/registry.py:60-83`; `src/magicite/mcp/app.py:327-440`, `:536-550` — H | S11 |
| A16 | Main benchmark computes both gold/predicted plan via production expansion; independent 24-case checker is separate | `src/magicite/eval/bench.py:328-337`; `scripts/check_evaluation_results.py:20-61`; `docs/evaluation/v0.3-results.json:24-38` — H | S01 |
| A17 | Ranking results are carried forward: dense Hit@1 .5476 versus full .5333 on 70 skills/210 queries | `docs/evaluation/v0.3-results.json:40-54` — H artifact status, not fresh runtime verification | S01/S14 |
| A18 | Current 10k latency evidence retains failures; synthetic/repeated-query timing is not a production scale guarantee | `docs/evaluation/v0.3-benchmarks.json:70-100`; `scripts/run_benchmark_matrix.py:266-290`; `tests/integration/test_route_latency.py:1-27` — H | S14 |
| A19 | Doctor's purported read-only registry check opens a connection whose default runs migrations | `src/magicite/obs/doctor.py:132-145`; `src/magicite/storage/db.py:87-98` — H | S12 |
| A20 | PyPI step is conditional; wheel check is dry-run/no-deps, not clean installed runtime evidence | `.github/workflows/release.yml:274-287`, `:304-315`; `docs/operations.md:328-359` — H | S13 |
| A21 | CI tests Python 3.11/3.12 while package declares >=3.11; security scan ignores unfixed findings | `pyproject.toml:7`; `.github/workflows/ci.yml:38-46`, `:210-217` — H | S13/S16 |
| A22 | Docs still mix draft-v1 language with 0.3 authority; generated docs checker uses fixed tool inventory | `docs/07-evaluation-and-observability.md:3`; `docs/AUTHORITY.md:1-21`; `scripts/check_generated_docs.py:16-40` — H | S15 |
| A23 | Existing docs/evaluation/provenance/supply-chain checker names were not found in scoped CI/test search | scoped `rg` over `.github/` and `tests/`, anchored checker implementation `scripts/check_generated_docs.py:16-40` — M; verify wiring when implementing | S13/S15 |

## Reuse and supersession

Reuse writer/authorizer/approval machinery (A07/A10/A14), the central MCP registry (A15), metric primitives (`src/magicite/eval/metrics.py:28-57`, H), and existing process-kill/lease tests (`tests/integration/test_dream_crash_recovery.py:73-111`, `tests/integration/test_lease_multiprocess.py:141-226`, H). Extend them rather than create parallel safety mechanisms.

Some existing tests intentionally pin legacy graph scoring and cycle breaking (`tests/unit/core/test_router.py:88-131`, `tests/unit/core/test_composition.py:83-145`, H). Implementation must explicitly supersede these expectations for stable V1 while preserving the experimental/legacy diagnostic paths where supported.

## Phase folds and limits

A→T: mission fixed, dossier revision equals checkout, lane boundaries chosen by source modules. T→L: retrieval/schema/composition, governance/storage/protocol, and evaluation/distribution/operations scouted independently. L→A: source anchors and reusable seams consolidated above; raw traversal transcripts excluded. A→S: findings handed to RAMZA contracts and 17 slices. Crystalium/graph query tooling was unavailable; deterministic file/symbol probes used. No follow-up scout was needed after the three lanes converged.

[GAP] Runtime correctness, exploitability, SDK cancellation behavior, published package state, actual host conformance and external pilot evidence remain unverified. These are explicit future evidence gates, not reasons to invent success. → implementation owners S01/S04/S11–S16.
