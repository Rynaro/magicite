# Magicite 0.3.0rc1 verification ledger

Verified against `fd309e6..HEAD` on 2026-08-18. This ledger records the
candidate state; it does not waive or rewrite the frozen acceptance criteria.

## Green candidate gates

| Area | Evidence |
| --- | --- |
| Combined runtime | 513 passed; 5 Docker-only checks skipped |
| Static analysis | Ruff clean; mypy clean across 61 source files |
| Integrity | Atomic migration, v0.2 upgrade, fencing, heartbeat, registry reconciliation, terminal sessions, idempotency reservation/TTL, and recursive adapter-token redaction tests pass |
| Routing parity | Canonical routing view, separate contraindications, shared activation primitives, baseline-c parity, reciprocal inhibition, recent-failure ingestion, live introspection, cursor, and cache-equivalence tests pass |
| Governance artifacts | Frozen criteria hash verifies; all changed files are inside declared scope; provenance contract validates |
| Supply chain | Workflow actions and container inputs are immutable; package metadata has no self-referential release digest |

## Final-tag blockers

| Frozen criteria | Required closure |
| --- | --- |
| AC-001, AC-003, AC-041 | Implement the complete resumable Dream phase/checkpoint state machine and process-death fault matrix; token-guard every direct durable phase mutation |
| AC-002, AC-042 | Add an OS-process lease race and TTL-overrun stale-writer fixture; current contention coverage uses independent SQLite connections in threads |
| AC-043 | Add end-to-end process-death recovery across handler effect, event commit, and response persistence; pending reservations currently fail closed but require operator recovery when no response exists |
| AC-012, AC-038, AC-044 | Add at least 20 independently authored composition plans and publish corrected, superseding baseline/composition/hypothesis results |
| AC-030 | Implement and verify auditable approve, deny, and resume governance transitions |
| AC-020, AC-021, AC-027, AC-032, AC-033, AC-045 | Land executable generated-doc and semantic-contract checks for the already-corrected documentation surfaces |
| AC-025, AC-046 | Run cold/miss/hit benchmarks at 1k and 10k with the production provider/container and record named budgets |
| AC-035 | Activate protected `main`/release tags and required reviews/checks in GitHub; the repository policy document is evidence of intent, not evidence of activation |

## Native-code decision

Pure C is a no-go for 0.3.0. The measured work first removed Python-level
graph-normalization repetition with a bounded content-keyed cache while keeping
Python as the sole semantic reference. Native code may be reconsidered only
after the production benchmark matrix identifies a residual CPU hotspot and a
differential test can prove exact semantic parity.
