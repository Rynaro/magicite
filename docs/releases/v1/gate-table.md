# V1 gate table — NO-GA draft

All 17 gates are explicit. PASS rows describe executed mechanical obligations; their assembled evidence is pending independent review in this draft. No release is authorized.

| Gate | Status | Evidence and remaining obligation |
|---|---|---|
| POLICY | **PASS** | Nonadaptive default, Dream isolation, explicit experimental selection and dense-v1 incumbent have executable witnesses. [policy-nonadaptive-default](evidence/policy-nonadaptive-default.json), [policy-dream-isolation](evidence/policy-dream-isolation.json), [policy-explicit-experimental](evidence/policy-explicit-experimental.json), [policy-pinned-incumbent](evidence/policy-pinned-incumbent.json) |
| EVIDENCE | **UNEVALUATED** | Integrity and claim CI fixtures pass and negative history is retained, but independent final label provenance and a complete new-run chain are absent. [criterion ledger](criterion-ledger.json) |
| SCHEMA | **PASS** | Frozen schema, A01 roundtrip identity, migration/replay and complete-backup downgrade witnesses are available. [schema-frozen-schema](evidence/schema-frozen-schema.json), [schema-roundtrip-corpus](evidence/schema-roundtrip-corpus.json), [schema-migration-downgrade-replay](evidence/schema-migration-downgrade-replay.json) |
| TRUST | **FAIL** | Known live revocation mirror deletion can reinstate earlier admission; hardening remains a human decision. [criterion ledger](criterion-ledger.json), [observed failure](evidence/trust-mirror-loss.json) |
| ROUTING | **UNEVALUATED** | Index/eligibility/explanation mechanics exist; official final split, paired E3 quality and empirical abstention bounds are unrun. [criterion ledger](criterion-ledger.json) |
| COMPOSITION | **UNEVALUATED** | Structural fixtures and bounded safe invalid plans exist; independently authored actual host-task execution evidence is unavailable. [criterion ledger](criterion-ledger.json) |
| PRIVACY | **PASS** | Data map, default minimization, historical cleanup, checkpoint RPO and export/delete/retention/restore witnesses are available. [privacy-data-map](evidence/privacy-data-map.json), [privacy-no-default-raw](evidence/privacy-no-default-raw.json), [privacy-historical-cleanup](evidence/privacy-historical-cleanup.json), [privacy-checkpoint-rpo](evidence/privacy-checkpoint-rpo.json), [privacy-lifecycle-tests](evidence/privacy-lifecycle-tests.json) |
| LEARNING | **PASS** | Stable containment is mandatory and tested; optional S10 efficacy explicitly held/unavailable. [learning-containment](evidence/learning-containment.json), [learning-fully-gated-or-unavailable](evidence/learning-fully-gated-or-unavailable.json) |
| PROTOCOL | **UNEVALUATED** | Generic probes exist; advertised real Claude Code version/protocol transcript remains absent. [criterion ledger](criterion-ledger.json) |
| RELIABILITY | **UNEVALUATED** | Local kill/replay/fencing/backup/restore/doctor tests pass; complete advertised OS/channel/published-upgrade/store matrix has not run. [criterion ledger](criterion-ledger.json) |
| PERFORMANCE | **UNEVALUATED** | No dedicated production E6 budgets or licensed real 10k corpus evidence; hashing completeness is not performance qualification. [criterion ledger](criterion-ledger.json) |
| DISTRIBUTION | **UNEVALUATED** | Published channel installs, fetched signatures/checksums/SBOM/provenance and TestPyPI rehearsal are unrun; no publishing authority. [criterion ledger](criterion-ledger.json) |
| DOCS | **UNEVALUATED** | Generated/claims/support checks and scripted tutorial pass; required independent operator transcript remains absent. [criterion ledger](criterion-ledger.json) |
| RC-CONTRACT | **UNEVALUATED** | No two independently built RCs across the declared supported matrix; validator fixtures are not RC artifacts. [criterion ledger](criterion-ledger.json) |
| EXTERNAL | **UNEVALUATED** | No independent external reproduction or two external production pilots; agent review is not product validation. [criterion ledger](criterion-ledger.json) |
| SECURITY | **FAIL** | Known trust-history integrity weakness remains unresolved; bounded fuzz and complete security evidence are also missing. [criterion ledger](criterion-ledger.json), [observed failure](evidence/trust-mirror-loss.json) |
| GA-ALL | **FAIL** | Required gates do not all pass; maintainer release sign-off and fetched published artifact checks are absent. [criterion ledger](criterion-ledger.json) |

Four mechanical PASS candidates, ten UNEVALUATED gates, and three FAIL gates. The machine-readable manifest retains the exact obligation checklist for each gate.

Missing external evidence is not waived. The known trust failure and absent mandatory evidence independently block GA. See the [unadopted trust proposal](trust-ledger-proposal.md) and [package scope](README.md).
