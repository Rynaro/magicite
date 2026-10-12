# Frozen V1 acceptance criteria

Canonical aggregate of the slice criteria. Amend through RAMZA freeze before changing requirements. Verification targets describe future implementation evidence.

### AC-S00-01 (event-driven)
GIVEN an identical pinned query and registry
WHEN usage, strength and community state vary
THEN stable routing SHALL return the same semantic result
VERIFY: new tests/unit/core/test_policy_boundary.py::test_stable_ignores_adaptation

### AC-S00-02 (event-driven)
GIVEN stable default configuration
WHEN Dream runs or checkpoints historical strength
THEN the active stable policy digest SHALL remain unchanged
VERIFY: new test_policy_boundary.py::test_dream_cannot_change_stable_policy

### AC-S00-03 (event-driven)
GIVEN an explicit experimental policy selection
WHEN routing completes
THEN the decision SHALL identify the experimental policy
VERIFY: new test_policy_boundary.py::test_experimental_is_explicit

### AC-S00-04 (event-driven)
GIVEN a query containing a secret sentinel
WHEN route is invoked
THEN persistent route events SHALL omit the raw query
VERIFY: new test_policy_boundary.py::test_no_raw_query_logging

### AC-S01-01 (event-driven)
GIVEN a frozen manifest and local corpus
WHEN the runner repeats with identical pins
THEN per-query semantic predictions SHALL match
VERIFY: new tests/unit/eval/test_manifests.py::test_reproducible_predictions

### AC-S01-02 (event-driven)
GIVEN a corpus with train/test overlap or duplicate query IDs
WHEN validation runs
THEN the corpus SHALL be rejected
VERIFY: new test_manifests.py::test_leakage_rejected

### AC-S01-03 (event-driven)
GIVEN a result referencing changed labels or missing prediction bytes
WHEN claim validation runs
THEN the claim SHALL fail integrity validation
VERIFY: new test_manifests.py::test_claim_digest_failure

### AC-S01-04 (event-driven)
GIVEN an independent structural corpus
WHEN benchmark gold is loaded
THEN expected plans SHALL originate only from corpus annotations
VERIFY: extend tests/integration/test_bench.py with a production-planner sentinel that rejects calls during label loading

### AC-S02-01 (event-driven)
GIVEN archived 0.2 and SKILL.md fixtures with prose, fences and extensions
WHEN persist, export, writer-canonical parse/write, or the pure 0.2-to-1.0 transform runs
THEN preserved source bytes SHALL remain identical on the C1 identity set: imported skill_md_source.body_raw plus extra_frontmatter through persist/export; writer-canonical engram/0.2 parse-write bytes; on-disk source archives after the pure transform
VERIFY: extend tests/integration/test_skillmd_roundtrip.py with v1 fixture corpus covering body_raw/extra_frontmatter persist-export identity, writer-canonical 0.2 parse-write identity, and untouched on-disk archives under the pure transform

### AC-S02-02 (event-driven)
GIVEN a legacy ID and references
WHEN the pure transform runs twice
THEN the stable ID SHALL remain unchanged
VERIFY: new tests/unit/engram/test_v1_schema.py::test_identity_preserved

### AC-S02-03 (event-driven)
GIVEN an unknown mandatory extension or invalid version range
WHEN the artifact is validated
THEN validation SHALL fail closed
VERIFY: new test_v1_schema.py::test_required_unknown_rejected

### AC-S02-04 (event-driven)
GIVEN an external asset with traversal or changed bytes
WHEN the artifact is loaded
THEN asset validation SHALL reject it
VERIFY: new test_v1_schema.py::test_asset_containment_and_digest

### AC-S02-05 (event-driven)
GIVEN the shared host/artifact and exact-revision relation fixtures
WHEN schema validation and legacy transform run
THEN the resulting typed relations SHALL match C1 without inferred learned-edge semantics
VERIFY: new tests/unit/engram/test_v1_schema.py::test_shared_relation_fixtures

### AC-S03-01 (event-driven)
GIVEN a legacy snapshot and dry-run request
WHEN migration preview runs
THEN all input bytes SHALL remain unchanged
VERIFY: new tests/integration/test_migration_v1.py::test_preview_zero_write

### AC-S03-02 (event-driven)
GIVEN fault injection at each file/SQL commit boundary
WHEN migration resumes
THEN the final state SHALL equal one uninterrupted migration
VERIFY: new test_migration_v1.py::test_crash_matrix

### AC-S03-03 (event-driven)
GIVEN a completed migration operation ID
WHEN apply is retried
THEN the operation SHALL produce zero duplicate effects
VERIFY: new test_migration_v1.py::test_idempotent_apply

### AC-S03-04 (event-driven)
GIVEN a new-schema registry and matching old backup
WHEN downgrade restoration runs
THEN the restored state SHALL match the supported backup manifest
VERIFY: extend tests/integration/test_upgrade_v02.py with v1 restore and future-version rejection

### AC-S04-01 (event-driven)
GIVEN an external file claiming authored and verified
WHEN intake runs
THEN the artifact SHALL remain pending or quarantined until local admission
VERIFY: new tests/unit/core/test_trust_intake.py::test_forged_origin

### AC-S04-02 (event-driven)
GIVEN a signed bundle with altered resource or unsafe archive member
WHEN verification runs
THEN the bundle SHALL be rejected before admission
VERIFY: new test_trust_bundle.py::test_tamper_and_escape

### AC-S04-03 (event-driven)
GIVEN an approved digest
WHEN content or local policy changes
THEN the prior approval SHALL cease to authorize routing
VERIFY: new test_trust_intake.py::test_digest_and_policy_binding

### AC-S04-04 (event-driven)
GIVEN concurrent repeated approval and a later DB rebuild
WHEN review execution completes
THEN the restored trust ledger SHALL contain exactly one applied decision
VERIFY: new tests/integration/test_trust_recovery.py::test_review_replay_rebuild

### AC-S05-01 (event-driven)
GIVEN fixtures whose only distinguishing text is raw prose or an exact error token
WHEN candidate generation runs
THEN the matching artifact SHALL be retrieved
VERIFY: new tests/unit/core/test_candidates.py::test_full_body_exact_terms

### AC-S05-02 (event-driven)
GIVEN equal component ranks and pinned inputs
WHEN fusion repeats
THEN candidate ordering SHALL be stable by ID
VERIFY: new test_candidates.py::test_rrf_ties

### AC-S05-03 (event-driven)
GIVEN a frontmatter-only edit or same-name model artifact change
WHEN index validation runs
THEN the stale generation SHALL be rejected
VERIFY: new tests/integration/test_index_generation.py::test_fingerprint_invalidates

### AC-S05-04 (event-driven)
GIVEN a partially built generation and a failed build
WHEN routing pins an index
THEN only a complete published generation SHALL be visible
VERIFY: new test_index_generation.py::test_atomic_swap_and_rollback

### AC-S06-01 (event-driven)
GIVEN a declared framework range and unknown host version
WHEN eligibility is evaluated
THEN the result SHALL be context_required
VERIFY: new tests/unit/core/test_eligibility.py::test_unknown_required_context

### AC-S06-02 (event-driven)
GIVEN request grants exceeding server permission policy
WHEN eligibility is evaluated
THEN the excess permission SHALL remain denied
VERIFY: new test_eligibility.py::test_request_cannot_elevate

### AC-S06-03 (event-driven)
GIVEN an archived or quarantined dependency behind an eligible winner
WHEN dependency eligibility is evaluated
THEN the dependency SHALL be excluded
VERIFY: new test_eligibility.py::test_transitive_denial

### AC-S06-04 (event-driven)
GIVEN malformed ranges and unsupported mandatory capability vocabulary
WHEN constraint validation runs
THEN the artifact SHALL not become eligible
VERIFY: new test_eligibility.py::test_unsupported_constraints

### AC-S06-05 (event-driven)
GIVEN consumer B requires artifact X and host artifact inventory is known-empty
WHEN pre-ranking eligibility runs
THEN B SHALL remain eligible with X listed in unsatisfied_artifacts
VERIFY: new tests/unit/core/test_eligibility.py::test_plannable_requirement_is_not_host_denial

### AC-S07-01 (event-driven)
GIVEN a quarantined artifact with strongest raw score
WHEN routing runs
THEN the artifact SHALL be absent from returned candidates and bodies
VERIFY: extend tests/integration/test_route_end_to_end.py::test_eligibility_before_rerank

### AC-S07-02 (event-driven)
GIVEN an unrelated query in the locked rejection set
WHEN calibrated routing runs
THEN the result SHALL obey the frozen abstention decision rule
VERIFY: new tests/unit/core/test_calibration.py::test_threshold_manifest

### AC-S07-03 (event-driven)
GIVEN pinned semantic inputs
WHEN routing repeats across rebuild
THEN semantic decision fields SHALL match
VERIFY: new tests/integration/test_route_reproducibility.py::test_rebuild_determinism

### AC-S07-04 (event-driven)
GIVEN a reranker timeout or missing required model
WHEN routing runs
THEN the result SHALL identify configured fallback or explicit operational error
VERIFY: new tests/unit/core/test_router_policy.py::test_fallback_identity

### AC-S07-05 (event-driven)
GIVEN S10 is not installed and reviewed simple/hybrid policy artifacts exist
WHEN activation and rollback use the expected-current API
THEN the active digest SHALL follow exactly the approved compare-and-swap transitions
VERIFY: new tests/integration/test_stable_policy_activation.py::test_activation_without_learning including stale-current rejection

### AC-S08-01 (event-driven)
GIVEN an eligible winner with a denied or missing required dependency
WHEN composition runs
THEN the plan SHALL be invalid
VERIFY: extend tests/unit/core/test_composition.py::test_denied_dependency_invalid

### AC-S08-02 (event-driven)
GIVEN a cycle or exceeded node/depth/edge limit
WHEN composition runs
THEN the plan SHALL contain no executable prefix
VERIFY: new tests/integration/test_composition_v1.py::test_cycle_and_budget

### AC-S08-03 (event-driven)
GIVEN two satisfying producers with no explicit preference
WHEN composition runs
THEN the result SHALL report ambiguous_provider
VERIFY: new test_composition_v1.py::test_ambiguous_alternatives

### AC-S08-04 (event-driven)
GIVEN an independently authored task with a deterministic host verifier
WHEN the evaluation harness executes the plan
THEN the report SHALL distinguish structural validity from verified task outcome
VERIFY: new test_composition_v1.py::test_host_verifier_report

### AC-S08-05 (event-driven)
GIVEN known-empty artifact inventory, B requiring artifact X and eligible A producing X
WHEN composition selects B
THEN the valid plan SHALL order A before B
VERIFY: new tests/integration/test_composition_v1.py::test_producer_satisfies_consumer with denied-host-tool paired case

### AC-S08-06 (event-driven)
GIVEN selected exact revisions with before or supersedes relationships
WHEN the planner consumes the shared S02 fixtures
THEN relation behavior SHALL match C1 ordering and replacement-conflict semantics
VERIFY: new tests/integration/test_composition_v1.py::test_shared_relation_semantics

### AC-S09-01 (event-driven)
GIVEN a repeated event ID with identical or conflicting payload
WHEN checkpoint retries
THEN the ledger SHALL enforce one immutable payload per event ID
VERIFY: new tests/unit/core/test_evidence.py::test_event_id_idempotence

### AC-S09-02 (event-driven)
GIVEN an acknowledged checkpoint followed by process death and index rebuild
WHEN recovery runs
THEN acknowledged evidence SHALL remain available
VERIFY: new tests/integration/test_evidence_recovery.py::test_ack_survives_rebuild

### AC-S09-03 (event-driven)
GIVEN raw-query history and secret sentinels in inputs
WHEN upgrade and default checkpoint/export run
THEN managed current evidence stores SHALL contain no raw sentinel
VERIFY: new test_evidence_recovery.py::test_privacy_history_and_export

### AC-S09-04 (event-driven)
GIVEN deterministic selection or delayed self-reported feedback
WHEN the evidence is evaluated
THEN unsupported counterfactual efficacy SHALL remain unknown
VERIFY: new tests/unit/core/test_evidence.py::test_provenance_and_support

### AC-S09-05 (event-driven)
GIVEN managed export artifacts under the ledger export directory plus an unregistered operator copy outside that directory
WHEN privacy deletion runs
THEN deletion SHALL cover the C6 local-management set: purge managed export-directory artifacts plus any registered export-manifest paths; unregistered operator copies remain out of scope
VERIFY: new tests/integration/test_evidence_recovery.py::test_deletion_covers_managed_exports_only

### AC-S09-06 (event-driven)
GIVEN a default evidence export that uses fresh per-export pseudonyms
WHEN the export artifact is written
THEN the export SHALL include a notice that privacy deletion cannot follow operator copies
VERIFY: new tests/integration/test_evidence_recovery.py::test_export_carries_copy_deletion_notice

### AC-S10-01 (event-driven)
GIVEN a shadow candidate and a pinned incumbent
WHEN shadow scoring runs
THEN incumbent route results SHALL remain unchanged
VERIFY: new tests/unit/core/test_policy_learning.py::test_shadow_isolation

### AC-S10-02 (event-driven)
GIVEN logs with zero action support for candidate decisions
WHEN OPE runs
THEN the result SHALL be insufficient_support
VERIFY: new test_policy_learning.py::test_zero_support

### AC-S10-03 (event-driven)
GIVEN a promotion with stale approval digest
WHEN activation is attempted
THEN activation SHALL be rejected
VERIFY: new tests/integration/test_policy_promotion.py::test_stale_approval

### AC-S10-04 (event-driven)
GIVEN a canary crossing its frozen rollback rule
WHEN rollback executes
THEN the active policy SHALL equal the pinned prior incumbent
VERIFY: new test_policy_promotion.py::test_exact_rollback

### AC-S11-01 (event-driven)
GIVEN a decision whose body or trust policy changed
WHEN load_skill_body runs
THEN the call SHALL return stale_decision without the body
VERIFY: new tests/unit/mcp/test_v1_retrieval.py::test_stale_body_denied

### AC-S11-02 (event-driven)
GIVEN a valid v1 tool response
WHEN text and structured payloads are decoded
THEN both payloads SHALL represent identical data
VERIFY: extend tests/unit/mcp/test_dispatch_call.py::test_v1_payload_parity

### AC-S11-03 (event-driven)
GIVEN cancellation around durable commit followed by retry
WHEN the client reconnects
THEN the durable operation SHALL have at most one effect
VERIFY: new tests/acceptance/test_stdio_cancellation.py::test_commit_boundary

### AC-S11-04 (event-driven)
GIVEN each advertised host/SDK/protocol row
WHEN the installed conformance probe runs
THEN every required probe SHALL pass with an archived transcript
VERIFY: new tests/acceptance/test_v1_conformance.py and host evidence manifest

### AC-S12-01 (event-driven)
GIVEN missing, old-schema, corrupt or read-only data directories
WHEN doctor runs
THEN filesystem and database bytes SHALL remain unchanged
VERIFY: extend tests/unit/obs/test_doctor.py::test_zero_write_matrix

### AC-S12-02 (event-driven)
GIVEN a verified backup of all authoritative stores
WHEN restore and rebuild run
THEN all acknowledged durable records through the backup recovery point SHALL be recovered
VERIFY: new tests/integration/test_backup_restore_v1.py::test_complete_restore

### AC-S12-03 (event-driven)
GIVEN a revoked artifact or privacy deletion after backup plus a valid current RecoveryOverlay and sequence anchor
WHEN restore runs
THEN current revocation and deletion policy SHALL prevent reactivation
VERIFY: new test_backup_restore_v1.py::test_policy_reapplication

### AC-S12-04 (event-driven)
GIVEN a stale writer or killed lease holder
WHEN another process recovers
THEN the stale process SHALL be unable to commit
VERIFY: extend tests/integration/test_lease_multiprocess.py with evidence/trust/policy writers

### AC-S12-05 (event-driven)
GIVEN a clean-machine backup with absent or stale current overlay/anchor
WHEN restore is attempted
THEN routing and evidence access SHALL remain disabled with reconciliation_required
VERIFY: new tests/integration/test_backup_restore_v1.py::test_missing_stale_overlay_closed

### AC-S13-01 (event-driven)
GIVEN a built wheel in a clean temporary environment outside checkout
WHEN installation and fixture probe run
THEN the packaged runtime SHALL route the offline fixture
VERIFY: new tests/acceptance/test_install_channels.py::test_clean_wheel

### AC-S13-02 (event-driven)
GIVEN a fresh install without model cache
WHEN offline routing is attempted
THEN the error SHALL provide explicit model acquisition remediation
VERIFY: new test_install_channels.py::test_missing_model

### AC-S13-03 (event-driven)
GIVEN each advertised distribution channel
WHEN the published artifact is installed
THEN the same conformance fixture SHALL pass
VERIFY: new test_install_channels.py::test_channel_matrix with recorded channel versions/digests

### AC-S13-04 (event-driven)
GIVEN a release manifest and fetched image/wheel
WHEN signature/provenance/resource checks run
THEN every advertised artifact SHALL match its declared digest
VERIFY: extend scripts/check_supply_chain.py and published artifact verification job

### AC-S14-01 (event-driven)
GIVEN a declared benchmark profile
WHEN the matrix runs
THEN the result SHALL identify every cache state and hardware/model fingerprint
VERIFY: extend tests/unit/eval/test_bench_baselines.py::test_profile_manifest

### AC-S14-02 (event-driven)
GIVEN a claimed supported scale
WHEN release evidence is checked
THEN the complete production-provider envelope SHALL satisfy its preregistered budget
VERIFY: dedicated runner validates evaluation.md envelopes; shared CI validates completeness only

### AC-S14-03 (event-driven)
GIVEN a locked local or external final split
WHEN ranking quality is assessed
THEN the promotion verdict SHALL follow the preregistered paired interval rule
VERIFY: new tests/unit/eval/test_release_verdicts.py with hand-computed boundary cases

### AC-S14-04 (event-driven)
GIVEN an end-task usefulness claim
WHEN claim validation runs
THEN the evidence SHALL include paired host-verifier outcomes
VERIFY: new test_release_verdicts.py::test_no_structural_efficacy_substitution

### AC-S15-01 (event-driven)
GIVEN generated references and installed runtime
WHEN drift checks run
THEN declared versions/schema/tool inventories SHALL match runtime
VERIFY: extend scripts/check_generated_docs.py with snapshot fixtures

### AC-S15-02 (event-driven)
GIVEN a quantitative README claim without an accepted evidence manifest
WHEN documentation CI runs
THEN the claim SHALL fail validation
VERIFY: extend scripts/check_docs.py with claim-ledger fixture

### AC-S15-03 (event-driven)
GIVEN a fresh supported operator environment
WHEN the complete tutorial is followed
THEN all advertised steps SHALL complete with captured output
VERIFY: scripted tutorial smoke plus independent operator transcript

### AC-S15-04 (event-driven)
GIVEN a deprecated contract or changed support row
WHEN docs validation runs
THEN the published policy SHALL identify replacement and support deadline
VERIFY: new documentation contract fixture for S15

### AC-S16-01 (event-driven)
GIVEN a release gate with missing evidence
WHEN the GA validator runs
THEN release eligibility SHALL be false
VERIFY: new release-manifest validation fixture with missing gate

### AC-S16-02 (event-driven)
GIVEN two RC artifacts
WHEN API/schema fingerprints are compared
THEN stable fingerprints SHALL match or require a fresh RC pair
VERIFY: release gate RC-CONTRACT

### AC-S16-03 (event-driven)
GIVEN external validation evidence
WHEN independence review runs
THEN the evidence SHALL satisfy one reproduction or two-pilot path
VERIFY: release gate EXTERNAL with named independent reviewer and immutable report

### AC-S16-04 (event-driven)
GIVEN a complete candidate release manifest
WHEN GA eligibility is evaluated
THEN all nonwaivable release gates SHALL pass
VERIFY: release gate GA-ALL
