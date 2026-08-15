<!--
  ADDENDUM — NOT PART OF THE FROZEN CRITERIA SET.

  The frozen anchor is acceptance-criteria.md (AC-001 … AC-033), sha256
  7bd3d1842de8d0d55c00fc4803e52e012885a26094cc928b7801af556eada448, which is byte-identical
  and was NOT re-frozen by this amendment. Do not merge this file into it: `ramza-freeze`
  writes plan-state.json criteria_sha256, and that pointer must keep naming the frozen 33.

  These nine criteria (AC-034 … AC-042) are added by the amendment recorded in
  decisions/DECLARED-EDGES-AMENDED.md (spec.md §3.3.1, "Effective edge weight"). Their tamper
  anchor is this file's own sha256, recorded in spec.yaml artifacts[]. They are linted by
  `ramza-ears-lint` and are attested by Kupo alongside AC-001 … AC-033.

  AC-023 is NOT edited by this addendum. It is provenance-underspecified in its GIVEN — a
  coverage defect in the frozen set, not a checker failure and not a test-fidelity failure:
  tests/unit/core/test_router.py:80 proves AC-023 exactly as written, on a state no production
  path can reach. AC-034 is the criterion that names the reachable state.
-->

### AC-034 (event-driven)
GIVEN a registry whose engrams were ingested only through `register()` and one of whose `.egr.md` frontmatters declares `inhibits: [<competitor>]`
WHEN `route()` scores a query that activates both the inhibitor and the competitor
THEN the competitor's score SHALL be strictly lower than its score in an otherwise identical run with `declared_edge_strength = 0.0`
VERIFY: test: tests/acceptance/test_declared_edge_weight.py::test_inhibition_is_reachable_from_register

### AC-035 (event-driven)
GIVEN an engram ingested through `register()` whose frontmatter declares `needs: [<target>]` and whose target is registered
WHEN the activation graph is built for a `route()` call
THEN that declared edge SHALL be present in the graph with raw weight `declared_edge_strength * type_gain["depends_on"]`
VERIFY: test: tests/unit/core/test_edge_weight.py::test_declared_edge_enters_the_activation_graph

### AC-036 (state-driven)
GIVEN a declared edge that no Dream run has ever potentiated
THEN its persisted `edge.storage_strength` SHALL still be exactly 0.0
VERIFY: test: tests/unit/storage/test_durable.py::test_authored_weight_is_never_persisted

### AC-037 (event-driven)
GIVEN a winning engram ingested through `register()` whose declared `needs`/`composes` targets all resolve to registered engrams
WHEN `route()` returns a `composition_plan` of more than one node
THEN `plan_confidence` SHALL equal 1.0
VERIFY: test: tests/integration/test_route_end_to_end.py::test_plan_confidence_is_one_when_fully_resolved

### AC-038 (event-driven)
GIVEN a winning engram declaring exactly two `needs` targets of which exactly one is registered
WHEN `route()` returns its `composition_plan`
THEN `plan_confidence` SHALL equal 0.5
VERIFY: test: tests/integration/test_route_end_to_end.py::test_plan_confidence_reports_the_unresolved_share

### AC-039 (unwanted-behavior)
GIVEN a registry containing declared edges and a config with `declared_edge_strength = 0.0`
WHEN `route()` scores a query
THEN every returned score SHALL equal the score from the same registry with its declared edges deleted
VERIFY: test: tests/unit/core/test_edge_weight.py::test_zero_declared_strength_is_an_exact_revert

### AC-040 (ubiquitous)
THEN no module under `src/magicite/` outside `magicite.core.edge_weight` SHALL derive an edge routing weight from `edge.storage_strength` without calling `effective_strength`
VERIFY: test: tests/unit/test_p0_enforcement.py::test_edge_weight_helper_is_the_only_weighting_site

### AC-041 (event-driven)
GIVEN a declared edge that no Dream run has ever potentiated
WHEN `introspect(skill_id=...)` returns that engram's outbound edges
THEN each returned edge row SHALL carry an `effective_strength` field equal to `declared_edge_strength`
VERIFY: test: tests/unit/storage/test_queries.py::test_edge_rows_report_effective_strength

### AC-042 (event-driven)
GIVEN a composition cycle whose candidate edges all share the same effective strength
WHEN the plan is expanded twice over the same registry
THEN the two `composition_plan` orders SHALL be identical
VERIFY: test: tests/unit/core/test_composition.py::test_cycle_break_is_deterministic
