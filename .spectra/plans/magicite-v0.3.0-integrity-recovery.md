---
eidolon: ramza
kind: spec
version: 1.0.0
created_at: 2026-08-18T09:45:30Z
plan: magicite-v0.3.0-integrity-recovery
release: 0.3.0
intent_class: CHANGE
---

# Magicite 0.3.0 integrity recovery

## Scope

Deliver one governed 0.3.0 release candidate that closes the confirmed audit drifts without changing Magicite's local-first product boundary. The change includes durable-state recovery, routing/evaluation parity, truthful interfaces, performance index reuse, documentation authority, release hygiene, and future agent provenance.

In scope: `src/magicite`, migrations, tests, evaluation fixtures, public schemas, package/release metadata, current documentation, append-only governance corrections, CI/release definitions, and a provenance record for this change.

Out of scope: a native C extension, a new embedding model, rewriting historical archive bytes, claiming falsified hypotheses now pass, merging the pull request, or publishing the final tag before independent review.

## Approach

Use the audit's staged integrity-first strategy. Preserve existing public behavior where safe, make fail-open paths explicit and recoverable, share production routing primitives with evaluation, add focused compatibility shims only when they do not hide state, and optimize by caching/index generation before considering native code.

Execution order:

1. Contain governance risk before implementation: immutable CI inputs, branch/tag protection evidence, and a PR-only release train.
2. Establish test anchors, v0.2 upgrade fixtures, and a recovery migration.
3. Fix Dream, leases, registry reconciliation, migrations, and idempotency.
4. Fix routing semantics, baseline parity, introspection, pagination, sessions, and secret redaction.
5. Repair evaluation and publish superseding results without rewriting prior evidence.
6. Add safely invalidated route-index reuse and production-shaped performance tests.
7. Reconcile current docs, generated inventories, changelog errata, archive redirects, release metadata, and provenance.
8. Run drift, package, test, type, lint, benchmark, container, and governance gates.

Integration checkpoints under one 0.3 release train are: governance containment; durable-state recovery; routing/interface parity; evaluation repair; performance index; documentation/release RC. Each checkpoint must be independently green before the next is integrated. The branch version is `0.3.0rc1` until all gates pass; final `0.3.0` tagging and publication remain human-governed.

## Stories

### S1 — Recoverable durable state (8d)

- Add explicit transaction helpers and atomic migration versioning.
- Make Dream phases resumable/idempotent with durable checkpoints and ownership fencing.
- Acquire leases atomically, heartbeat during long work, and reject writes after ownership loss.
- Fail closed when a discovered authoritative file is invalid.
- Reconcile each source-owned edge set by replacement, including deletions.
- Make idempotency TTL effective at lookup and persist completion through a durable protocol.

### S2 — Routing and evaluation truth (8d)

- Version the canonical routing text view.
- Represent contraindication text independently and apply a diagnosable penalty.
- Populate one canonical recent-failure context path through ingestion.
- Extract shared seed/PPR/inhibition primitives for production and baselines.
- Replace self-referential composition labels with an independently authored corpus.
- Add score-stage diagnostics and reciprocal-inhibition regression coverage.

### S3 — Interface and lifecycle truth (5d)

- Project live effective strength/reliability/tag/pending-delta values in introspection.
- Make consolidation lookup functional.
- Add cursor input to `load_skill_body`.
- Enforce terminal session close and explicit new-session behavior.
- Redact or key-digest adapter secrets before durable canonicalization.
- Separate lifecycle, verification, and execution status in docs and public projections.

### S4 — Performance without native-code risk (5d)

- Introduce a generation-keyed routing index for graph normalization, structural rank, and candidate matrices.
- Invalidate it on registry/edge/community mutations.
- Batch cosine and use partial top-k where result ordering remains deterministic.
- Add stage timers and cold/miss/hit benchmarks at 1k and 10k registries.
- Record a native-C reconsideration gate; do not ship a second semantic implementation in 0.3.0.

### S5 — Authority, release, and provenance (5d)

- Publish a current v0.3 authority manifest and reconcile docs 02–07.
- Generate/check the 16-tool inventory and runtime defaults.
- Add append-only errata for counts, changelog claims, invalid ablation, and stale state names.
- Add archive path redirects without modifying anchored archive records.
- Default embedding operation to offline and document explicit model acquisition.
- Remove self-referential image digest from package metadata.
- Pin workflow actions and container bases to immutable references where verification permits.
- Record model/host/role/commit/checker provenance for this change without guessing prior Fable work.
- Set package/documentation version to 0.3.0 and prepare draft release notes.

## Drift traceability

| Audit drift | Story | Concrete disposition | Acceptance criteria |
|---|---|---|---|
| D01 | S5 | Offline default plus explicit model acquisition | AC-017 |
| D02 | S5 | One current authority manifest | AC-021, AC-045 |
| D03 | S5 | Backport CR-1–CR-8 outcomes | AC-045 |
| D04 | S5 | Replace stale Ollama-default claims | AC-045 |
| D05 | S5 | Scope Dream to processing host-authored proposals | AC-045 |
| D06 | S3/S5 | One import severity/state contract | AC-031, AC-045 |
| D07 | S5 | Immutable ID plus distinct drift hash | AC-045 |
| D08 | S5 | Generated 16-tool inventory | AC-020 |
| D09 | S2/S5 | One retrieval formula and default | AC-026 |
| D10 | S2 | Separate contraindication embedding/penalty | AC-008 |
| D11 | S2/S5 | Governed `yields` semantics | AC-027 |
| D12 | S2 | Canonical ingested failure context | AC-011 |
| D13 | S2 | Correct reciprocal-inhibition semantics/reporting | AC-028 |
| D14 | S2 | Versioned routing-view field contract | AC-029 |
| D15 | S2 | Independent composition labels | AC-012, AC-038 |
| D16 | S3/S5 | Explicit decide/resume transitions | AC-030 |
| D17 | S3/S5 | Independent state dimensions | AC-031 |
| D18 | S5 | Database declared rebuildable/local | AC-032 |
| D19 | S5 | Append-only changelog erratum | AC-033 |
| D20 | S5 | Append-only acceptance-count erratum | AC-033 |
| D21 | S5 | Archive redirect manifest | AC-021 |
| D22 | S4/S5 | Supersede invalid ablation conclusion | AC-033, AC-044 |
| D23 | S3 | Complete live introspection projection | AC-013, AC-036 |
| D24 | S3 | Cursor round trip | AC-014 |
| D25 | S1 | Durable idempotency and crash recovery | AC-007, AC-043 |
| D26 | S1 | Atomic migration plus v0.2 upgrade | AC-006, AC-040 |
| D27 | S3 | Terminal session close | AC-015 |
| D28 | S3 | Full redaction before canonicalization | AC-016, AC-037 |
| D29 | S5 | Digest outside package metadata | AC-034 |
| D30 | S5/phase 0 | Immutable inputs and protected workflow | AC-035 |
| D31 | S5 | Prospective exact agent provenance | AC-023 |

## Acceptance Criteria

The mechanically frozen criteria are in `magicite-v0.3.0-integrity-recovery.acceptance.md` and are part of this specification.

## Rejected Alternatives

1. **Native rewrite now** — rejected because the current latency target passes and profiling ranks graph reconstruction plus SQL/object conversion above numerical kernels. It creates ABI, packaging, memory-safety, and semantic-parity risk before correctness is established.
2. **Documentation-only 0.3.0** — rejected because it leaves reproducible partial-commit, lease, sync, and evaluation defects in runtime behavior.
3. **One cross-resource transaction** — rejected because SQLite and filesystem updates cannot be made truly atomic together; recovery must be an idempotent state machine with checkpoints and compensation.

## Risks

- P0: recovery state can itself become a new source of partial state. Mitigation: phase idempotency, injected failure tests, and resume from every boundary.
- P0: fencing changes may break legitimate long-running Dream work. Mitigation: ownership-verifying heartbeat and monotonic tokens.
- P1: negative-cue scoring can regress retrieval. Mitigation: separate coefficient, diagnostics, paired hard negatives, and conservative default.
- P1: route-index caching can become stale. Mitigation: generation key tied to every authoritative mutation plus differential tests against uncached computation.
- P1: correcting baseline-(c) invalidates published comparisons. Mitigation: preserve old data as superseded and publish corrected results explicitly.
- P2: immutable dependency pinning increases maintenance work. Mitigation: automated update PRs and explicit review.

Compatibility and rollback: schema changes must upgrade a copied v0.2 database in place and retain a backup/rebuild route; cursor input is additive; ended-session rejection is an intentional behavioral correction; offline-by-default requires an explicit fetch command; negative-cue scoring is separately weighted and can be set to zero for rollback; the route-index always retains an uncached reference path for differential verification.

## Confidence

The audit reproduced the highest-risk registry defects, current CI is green, existing internal patterns cover migrations, events, and routing, and each story has mechanical tests. Confidence is high for an autonomous release-candidate branch; merge/release remains human-governed.
