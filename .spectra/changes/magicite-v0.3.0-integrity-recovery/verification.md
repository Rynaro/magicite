# Magicite 0.3.0 verification ledger

Verified against `fd309e6..HEAD` on 2026-08-18. This ledger records the
integrated final candidate; it does not waive or rewrite the 46 frozen
acceptance criteria.

## Green local release gates

| Area | Evidence |
| --- | --- |
| Combined runtime | 543 passed, 5 Docker-only checks skipped, 1 benchmark deselected; identical pass² result |
| Frozen selectors | All 42 exact test selectors named by the frozen criteria pass |
| Static analysis | Ruff clean; mypy clean across 62 source files |
| Dream integrity | Spawned-process termination, phase-boundary resume, deterministic checkpoint bytes, and stale-token fencing pass |
| Lease/idempotency | Real multiprocess acquisition/TTL-overrun tests and effect/event/response process-death recovery pass |
| Routing/interface parity | Canonical routing view, contraindications, shared activation, baseline-c, live state dimensions, cursor, and cache-equivalence tests pass |
| Governance/evaluation | Audited approve/deny/resume; 24 independent composition cases; superseding results reproduce |
| Documentation | Generated 16-tool inventory and all semantic contracts pass |
| Performance | Local hashing 1k p95 passes AC-025; 1k/10k cold/miss/hit cells emit named budgets and preserve semantic equality |
| Package | 0.3.0 wheel and sdist build; wheel metadata identifies 0.3.0 and has no torch dependency |
| Plan integrity | Frozen criteria hash, 46-criterion EARS lint, plan lint, and 98-file declared-scope drift pass |
| Supply chain | Workflow actions and container inputs are immutable; package metadata has no self-referential release digest |

## Frozen criteria closed by this integration

- AC-001/003/041: resumable Dream phases, exact-once recovery, and fenced
  durable writes.
- AC-002/042: OS-process lease race, heartbeat, token loss, and stale-writer
  rejection.
- AC-043: staged idempotency response recovery plus audited operator
  inspect/complete/abandon transitions.
- AC-012/038/044: 24-case independent corpus and superseding baseline,
  composition, and affected-hypothesis results.
- AC-030: auditable approve, deny, resume, and terminal outcome transitions.
- AC-020/021/027/032/033/045: executable generated-document and semantic
  authority contracts.
- AC-025: measured 1,000-node warm hashing p95 remains below 100 ms.

## External final-tag blockers

| Gate | Required closure |
| --- | --- |
| AC-046 production evidence | The blocking GitHub CI step must complete the 1k/10k production FastEmbed matrix inside the built, network-disabled image; the model and Docker are unavailable locally |
| AC-035 repository governance | GitHub currently reports no repository rulesets. Activate protected `main` and `v*` tags with PRs, non-author approval, required current checks, stale-review dismissal, resolved conversations, linear history, and blocked force-push/deletion |
| Independent review | PR #2 is still draft with no review; the governed plan prohibits final merge/tag before an approving reviewer other than the author |

## Performance and native-code decision

Pure C remains a no-go for the 0.3.0 implementation because AC-024 freezes
Python as the sole routing semantic reference. The local hashing matrix passes
the 1,000-node target but records 10,000-node p95 misses (approximately 1.28 s
index-miss and 0.85 s index-hit). That evidence activates a post-0.3 profiling
and scaling investigation; it does not justify inserting a second semantic
implementation before the production-container profile distinguishes
algorithmic work, provider cost, SQLite/object conversion, and Python CPU.
