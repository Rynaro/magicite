# Magicite 0.3.0 frozen acceptance criteria

### AC-001 (unwanted-behavior)
GIVEN a Dream run fails after any durable phase
WHEN the same consolidation is resumed
THEN each input effect SHALL be committed at most once
VERIFY: test: tests/unit/core/test_dream_recovery.py

### AC-002 (unwanted-behavior)
GIVEN two processes contend for an expired Dream lease
WHEN acquisition occurs concurrently
THEN exactly one process SHALL receive the current fencing token
VERIFY: test: tests/integration/test_lease_multiprocess.py::test_concurrent_expired_acquisition_has_one_winner

### AC-003 (state-driven)
GIVEN a Dream worker no longer owns its fencing token
THEN the worker SHALL reject subsequent durable writes
VERIFY: test: tests/unit/core/test_dream_recovery.py::test_lost_lease_fences_writes

### AC-004 (unwanted-behavior)
GIVEN an authoritative engram file replaces a valid file with invalid content
WHEN registry synchronization runs
THEN the previous database projection SHALL not remain routable
VERIFY: test: tests/unit/core/test_registry.py::test_invalid_authoritative_file_disables_projection

### AC-005 (event-driven)
GIVEN a file-owned edge is removed from an engram
WHEN registry synchronization runs
THEN the removed edge SHALL be absent from durable graph state
VERIFY: test: tests/unit/core/test_registry.py::test_sync_reconciles_removed_edges

### AC-006 (unwanted-behavior)
GIVEN a migration fails before its final statement
WHEN the database reopens
THEN the migration version SHALL remain unchanged
VERIFY: test: tests/unit/storage/test_db.py::test_failed_migration_is_atomic

### AC-007 (event-driven)
GIVEN an idempotency record is past its TTL
WHEN the same canonical request is submitted
THEN the request SHALL execute as a new operation
VERIFY: test: tests/unit/server/test_app.py::test_expired_idempotency_key_executes_again

### AC-008 (event-driven)
GIVEN a query is similar to a skill contraindication
WHEN routing scores that skill
THEN route diagnostics SHALL expose a non-positive contraindication contribution
VERIFY: test: tests/unit/core/test_router.py::test_negative_cue_penalty_is_diagnosable

### AC-009 (ubiquitous)
THEN production routing and baseline-c SHALL use the same seed-count primitive
VERIFY: test: tests/unit/eval/test_bench_baselines.py::test_baseline_c_seed_parity

### AC-010 (state-driven)
GIVEN baseline-c includes declared inhibition
THEN its activation output SHALL match the shared production primitive
VERIFY: test: tests/unit/eval/test_bench_baselines.py::test_baseline_c_inhibition_parity

### AC-011 (event-driven)
GIVEN an ingested skill declares a failure context
WHEN a matching recent failure is routed
THEN the context boost SHALL be populated through the canonical ingestion path
VERIFY: test: tests/integration/test_recent_failure_context.py

### AC-012 (ubiquitous)
THEN composition evaluation SHALL use labels authored independently of the production expansion function
VERIFY: gate: python -m magicite.eval validate-corpus docs/evaluation/composition-v0.3.json

### AC-013 (event-driven)
GIVEN a valid consolidation identifier
WHEN introspection is requested
THEN the current consolidation record SHALL be returned
VERIFY: test: tests/unit/server/test_bind_inspect.py::test_introspect_consolidation

### AC-014 (event-driven)
GIVEN a skill body exceeds the response limit
WHEN the returned cursor is submitted
THEN the next response SHALL begin at the previous next offset
VERIFY: test: tests/unit/server/test_bind_retrieval.py::test_load_skill_body_cursor_round_trip

### AC-015 (state-driven)
GIVEN a session has ended
THEN resolving that identifier SHALL not silently reactivate it
VERIFY: test: tests/unit/core/test_session.py::test_ended_session_is_terminal

### AC-016 (ubiquitous)
THEN durable event and idempotency canonical arguments SHALL redact adapter secrets before hashing
VERIFY: test: tests/unit/core/test_events.py::test_adapter_secret_redacted_before_hashing

### AC-017 (state-driven)
GIVEN no embedding model is locally available
THEN default Magicite operation SHALL not initiate a network download
VERIFY: test: tests/unit/embeddings/test_fastembed_provider.py::test_default_is_offline

### AC-018 (event-driven)
GIVEN the registry generation is unchanged
WHEN repeated routes execute
THEN graph normalization SHALL be reused
VERIFY: test: tests/unit/core/test_router.py::test_route_index_reused_for_same_generation

### AC-019 (event-driven)
GIVEN any routing-authoritative mutation occurs
WHEN the next route executes
THEN the routing index SHALL be recomputed for the new generation
VERIFY: test: tests/unit/core/test_router.py::test_route_index_invalidated_on_mutation

### AC-020 (ubiquitous)
THEN the generated public tool inventory SHALL contain exactly the registered 16 tools
VERIFY: gate: python scripts/check_generated_docs.py

### AC-021 (ubiquitous)
THEN current normative documents SHALL identify one v0.3 authority order with no dead live references
VERIFY: gate: python scripts/check_docs.py

### AC-022 (ubiquitous)
THEN the package version and draft release notes SHALL identify 0.3.0
VERIFY: gate: python -m pytest tests/unit/test_version.py

### AC-023 (ubiquitous)
THEN future agent provenance SHALL record model host role commit range checker and approval without inferring missing historical authorship
VERIFY: gate: python scripts/check_provenance.py .spectra/changes/magicite-v0.3.0-integrity-recovery/change.json

### AC-024 (ubiquitous)
THEN the Python implementation SHALL remain the sole routing semantic reference for 0.3.0
VERIFY: gate: test ! -d src/magicite/native

### AC-025 (state-driven)
GIVEN a 1000-node warm hashing-provider registry
THEN route latency p95 SHALL remain below 100 milliseconds
VERIFY: test: tests/integration/test_route_latency.py

### AC-026 (ubiquitous)
THEN documentation configuration and routing code SHALL expose one retrieval formula with `w_retrieval=0.05`
VERIFY: test: tests/unit/test_config.py::test_documented_retrieval_default_matches_runtime

### AC-027 (ubiquitous)
THEN the `yields` field SHALL be declared metadata-only until a separately governed graph semantics exists
VERIFY: test: tests/unit/core/test_audit.py::test_yields_reported_as_metadata_only

### AC-028 (event-driven)
GIVEN reciprocal inhibition edges have equal effective strength
WHEN inhibition is applied to two positive activations
THEN both activations SHALL receive the same multiplicative factor
VERIFY: test: tests/unit/core/test_activation.py::test_reciprocal_equal_strength_inhibition

### AC-029 (ubiquitous)
THEN every embedding SHALL identify routing-view schema `magicite-routing-view/1` and its included fields
VERIFY: test: tests/unit/core/test_registry_core.py::test_canonical_routing_view_v1

### AC-030 (event-driven)
GIVEN a governed proposal is pending review
WHEN an authorized decision approves or denies it
THEN the proposal SHALL transition through an auditable decide operation
VERIFY: test: tests/unit/core/test_approvals.py::test_decide_and_resume_transitions

### AC-031 (ubiquitous)
THEN lifecycle verification and execution states SHALL remain independently queryable
VERIFY: test: tests/unit/mcp/test_bind_inspect.py::test_independent_state_dimensions

### AC-032 (ubiquitous)
THEN current documentation SHALL define `skill-graph.db` as local rebuildable state excluded from source control by default
VERIFY: gate: python scripts/check_docs.py --contract database-local-rebuildable

### AC-033 (ubiquitous)
THEN count state changelog and ablation corrections SHALL be appended as errata without modifying archived evidence
VERIFY: gate: python scripts/check_docs.py --contract append-only-errata

### AC-034 (ubiquitous)
THEN package metadata SHALL not contain the release image's self-referential digest
VERIFY: test: tests/unit/test_version.py::test_package_metadata_has_no_self_digest

### AC-035 (ubiquitous)
THEN workflow actions and container base inputs SHALL use immutable commit or image digests with repository protection evidence recorded
VERIFY: gate: python scripts/check_supply_chain.py

### AC-036 (event-driven)
GIVEN a skill has decayed strength reliability live tags and pending deltas
WHEN skill introspection is requested
THEN the response SHALL project all four current values
VERIFY: test: tests/unit/mcp/test_bind_inspect.py::test_introspect_projects_live_state

### AC-037 (unwanted-behavior)
GIVEN two candidate weak adapter secrets and persisted event data
WHEN an offline comparison is attempted
THEN the persisted data SHALL not distinguish which candidate was used
VERIFY: test: tests/unit/obs/test_events.py::test_persisted_event_cannot_verify_adapter_secret

### AC-038 (ubiquitous)
THEN the composition evaluation corpus SHALL contain at least 20 independently authored ordered plans with cycle and dangling cases
VERIFY: test: tests/unit/eval/test_metrics.py::test_composition_corpus_is_independent_and_sufficient

### AC-039 (event-driven)
GIVEN cached and uncached routing operate on the same registry generation
WHEN the same query context and k are evaluated
THEN their ordered results and diagnostic contributions SHALL be identical
VERIFY: test: tests/unit/core/test_router.py::test_cached_and_uncached_routes_match

### AC-040 (event-driven)
GIVEN a copied v0.2 database
WHEN 0.3 migrations run and the connection reopens
THEN all v0.2 engrams edges events and approvals SHALL remain readable
VERIFY: test: tests/integration/test_upgrade_v02.py

### AC-041 (unwanted-behavior)
GIVEN process death occurs after a selected intra-phase Dream write or checkpoint file
WHEN the worker restarts and resumes
THEN recovered durable state SHALL equal one uninterrupted run
VERIFY: test: tests/integration/test_dream_crash_recovery.py

### AC-042 (unwanted-behavior)
GIVEN a lease crosses its TTL while a stale writer retains a connection
WHEN heartbeat or a protected mutation checks ownership
THEN the stale writer SHALL be fenced before committing
VERIFY: test: tests/integration/test_lease_multiprocess.py::test_ttl_overrun_fences_stale_writer

### AC-043 (unwanted-behavior)
GIVEN process death occurs between handler effect event commit and response persistence
WHEN the same idempotency key is retried
THEN the side effect SHALL not execute twice
VERIFY: test: tests/integration/test_idempotency_recovery.py

### AC-044 (ubiquitous)
THEN corrected baseline-c composition and affected hypothesis results SHALL be published as superseding prior results
VERIFY: gate: python scripts/check_evaluation_results.py docs/evaluation/v0.3-results.json

### AC-045 (ubiquitous)
THEN docs 02 through 07 SHALL match the v0.3 authority manifest for providers imports identity tools strengths Dream evaluation and state semantics
VERIFY: gate: python scripts/check_docs.py --contract v0.3-semantic-parity

### AC-046 (event-driven)
GIVEN cold miss and hit route benchmarks at 1000 and 10000 nodes
WHEN the production container runs the benchmark matrix
THEN every measured class SHALL emit a named latency budget result
VERIFY: gate: python scripts/run_benchmark_matrix.py --provider production --sizes 1000 10000
