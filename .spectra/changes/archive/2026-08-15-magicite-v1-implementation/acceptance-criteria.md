### AC-001 (event-driven)
GIVEN a project root containing `.spectra/engrams/` with the toy registry
WHEN an MCP client sends `initialize` to `magicite serve` over stdio
THEN the server SHALL complete the handshake and report `serverInfo.name == "magicite"`
VERIFY: test: tests/acceptance/test_stdio_handshake.py::test_initialize

### AC-002 (ubiquitous)
THEN the server SHALL write no byte to stdout other than MCP protocol frames
VERIFY: test: tests/acceptance/test_stdio_handshake.py::test_stdout_is_protocol_only

### AC-003 (event-driven)
GIVEN a booted server
WHEN the client calls `tools/list`
THEN the response SHALL contain exactly the 16 tool names listed in spec §3.2
VERIFY: test: tests/acceptance/test_tool_manifest.py::test_sixteen_tools

### AC-004 (ubiquitous)
THEN every registered tool SHALL expose a non-null `risk_class`, `side_effect` and `idempotent` metadata triple
VERIFY: test: tests/unit/mcp/test_registry.py::test_metadata_complete

### AC-005 (unwanted-behavior)
GIVEN any tool input model
WHEN a client sends a payload containing a field not present in the schema
THEN the server SHALL reject the call with `invalid_input`, never ignoring the unknown field
VERIFY: test: tests/unit/mcp/test_schemas.py::test_unknown_field_rejected

### AC-006 (event-driven)
GIVEN the toy registry of 7 engrams
WHEN the client calls `register(path=".spectra/engrams")` then `route(query="rollback proton for a steam game")`
THEN the response SHALL rank `proton-ge-proton-downgrade` first
VERIFY: test: tests/acceptance/test_walking_skeleton.py::test_register_route_introspect

### AC-007 (ubiquitous)
THEN a `.egr.md` file SHALL only ever be replaced atomically via a temp file plus `os.replace`
VERIFY: test: tests/unit/engram/test_writer.py::test_atomic_replace_never_partial

### AC-008 (event-driven)
GIVEN a SKILL.md corpus lacking `not_when` and negative triggers
WHEN the client calls `register(path="skills/", format="skill")`
THEN every converted engram SHALL land with `status="draft"` and appear in `warnings[]`
VERIFY: test: tests/integration/test_register_import.py::test_import_profile_downgrades

### AC-009 (event-driven)
GIVEN a registry that has been consolidated at least once
WHEN `skill-graph.db` is deleted and `sync()` is called
THEN the durable projection of Tier A plus Tier B state SHALL be byte-identical to the pre-deletion projection
VERIFY: test: tests/acceptance/test_rebuild_invariant.py::test_durable_state_survives_rebuild

### AC-010 (state-driven)
GIVEN a freshly rebuilt index
THEN all Tier-C tables (`eph_retrieval`, `eph_tag`, `eph_candidate_edge`, `eph_embedding` excepted for recompute) SHALL be empty
VERIFY: test: tests/acceptance/test_rebuild_invariant.py::test_only_tier_c_is_lost

### AC-011 (event-driven)
GIVEN a registry of 1000 synthetic engrams
WHEN `route(query=..., k=5)` is called 100 times
THEN the p95 end-to-end latency SHALL be below 100ms
VERIFY: test: tests/integration/test_route_latency.py::test_p95_under_100ms

### AC-012 (event-driven)
GIVEN an engram declaring `needs: [steam-prefix-access]`
WHEN that engram wins routing
THEN `composition_plan` SHALL list `steam-prefix-access` before the winner
VERIFY: test: tests/integration/test_route_end_to_end.py::test_topological_plan_order

### AC-013 (unwanted-behavior)
GIVEN a hot-path tool holding an authorizer-restricted connection
WHEN it attempts any INSERT, UPDATE or DELETE on a non-`eph_` table
THEN SQLite SHALL deny the statement and the tool SHALL surface an internal error
VERIFY: test: tests/acceptance/test_p0_hot_path.py::test_authorizer_denies_durable_write

### AC-014 (unwanted-behavior)
GIVEN a Tier-0 inferred signal
WHEN `plasticity.apply()` is asked to move storage strength
THEN it SHALL raise `P0Violation`, leaving S unchanged
VERIFY: test: tests/unit/core/test_plasticity.py::test_tier0_cannot_move_S

### AC-015 (event-driven)
GIVEN a caller that supplies no `adapter_token`
WHEN it calls `signal_outcome(valence=1.0)`
THEN the recorded signal tier SHALL be 1, regardless of any tier the caller claims
VERIFY: test: tests/unit/core/test_signals.py::test_tier_assigned_server_side

### AC-016 (unwanted-behavior)
GIVEN an engram with `S=0.4` and `pass_rate=0.8`
WHEN `promote(name=...)` is called
THEN the call SHALL return `transition_denied` naming the unmet guards, leaving the status unchanged
VERIFY: test: tests/unit/core/test_lifecycle.py::test_promote_denied_below_evidence_bar

### AC-017 (event-driven)
GIVEN captured tags from three sessions with positive outcomes
WHEN a Dream run completes
THEN the affected edge's `storage_strength` SHALL increase by no more than `eta * (1 - w/w_max)` per capture
VERIFY: test: tests/unit/core/test_plasticity.py::test_metaplastic_saturation_bound

### AC-018 (event-driven)
GIVEN a consolidated engram
WHEN `export(out_dir=...)` runs and the result is re-registered
THEN the second import SHALL produce no change to the original engram's durable state
VERIFY: test: tests/integration/test_skillmd_roundtrip.py::test_export_import_stable

### AC-019 (event-driven)
GIVEN a write tool called twice with the same `request_id` and identical arguments
WHEN the second call arrives
THEN the server SHALL return the stored response without repeating the side effect
VERIFY: test: tests/unit/mcp/test_idempotency.py::test_replay_returns_cached_response

### AC-020 (event-driven)
GIVEN a completed Dream run with no new events since its watermark
WHEN `consolidate()` runs again
THEN the second run SHALL write zero `.egr.md` files
VERIFY: test: tests/acceptance/test_dream_idempotent.py::test_second_run_is_a_noop

### AC-021 (event-driven)
GIVEN identical durable state
WHEN the checkpoint procedure renders an engram twice
THEN the two files SHALL be byte-identical
VERIFY: test: tests/unit/engram/test_writer.py::test_render_is_deterministic

### AC-022 (state-driven)
GIVEN `python-igraph` and `leidenalg` are not installed
THEN community detection SHALL fall back to label propagation and report `detector="label_propagation"`
VERIFY: test: tests/unit/core/test_communities.py::test_fallback_detector

### AC-023 (event-driven)
GIVEN an engram whose `inhibits` edge targets a competitor engram
WHEN both are activated by a query
THEN the inhibited engram's score SHALL be strictly lower than without the inhibition edge
VERIFY: test: tests/unit/core/test_router.py::test_inhibition_lowers_score

### AC-024 (ubiquitous)
THEN the modules `magicite.core.router` and `magicite.core.signals` SHALL never import `magicite.storage.durable` or `magicite.engram.writer`
VERIFY: test: tests/unit/test_p0_enforcement.py::test_forbidden_imports

### AC-025 (unwanted-behavior)
GIVEN a Dream run already holding the writer lease
WHEN a second Dream run attempts to start
THEN the second attempt SHALL return `busy` without writing any durable state
VERIFY: test: tests/integration/test_dream_cycle.py::test_single_writer_enforced

### AC-026 (event-driven)
GIVEN the published container image
WHEN it is started with `--cap-drop ALL --security-opt no-new-privileges` and a mounted project
THEN the MCP handshake SHALL succeed with no network access
VERIFY: test: tests/acceptance/test_docker_smoke.py::test_offline_handshake

### AC-027 (unwanted-behavior)
GIVEN review mode (the default)
WHEN `archive(name=...)` is called
THEN the tool SHALL create an approval in state `proposed` without mutating the engram
VERIFY: test: tests/unit/core/test_approvals.py::test_r3_requires_approval_by_default

### AC-028 (event-driven)
GIVEN an imported engram carrying an exec block
WHEN `register()` ingests it
THEN the engram SHALL be recorded with `verification_status="quarantined"` and excluded from routing
VERIFY: test: tests/integration/test_register_import.py::test_exec_block_quarantined

### AC-029 (event-driven)
GIVEN the labelled toy benchmark
WHEN `magicite-bench --baseline b --baseline d` runs
THEN it SHALL emit Hit@1, Hit@3, Hit@5, MRR and Plan F1 for both baselines
VERIFY: test: tests/integration/test_bench.py::test_baseline_metrics_emitted

### AC-030 (event-driven)
GIVEN a registry where one engram absorbs more than 50% of routing traffic
WHEN the audit phase runs
THEN the audit report SHALL flag that engram as a black-hole hub
VERIFY: test: tests/unit/core/test_audit.py::test_hub_detection

### AC-031 (ubiquitous)
THEN the dependency tree SHALL contain no `torch` distribution
VERIFY: command: uv pip list --format json | jq -e 'map(.name) | index("torch") == null'

### AC-032 (event-driven)
GIVEN two `session_end` calls arriving inside the `dream.min_interval_s` window
WHEN the second call is handled
THEN the server SHALL return the already-enqueued `dream_run_id` rather than enqueuing a second run
VERIFY: test: tests/integration/test_dream_cycle.py::test_session_end_debounce

### AC-033 (state-driven)
GIVEN an engram whose effective storage strength has decayed below `floor_archived`
THEN the next Dream run SHALL move its file into `.spectra/archive/` without deleting it
VERIFY: test: tests/integration/test_dream_cycle.py::test_decay_floor_archives_never_deletes

---

