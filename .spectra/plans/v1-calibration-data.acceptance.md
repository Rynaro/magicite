# Frozen calibration-data controls criteria

### AC-CD-01 (event-driven)
GIVEN a companion packet and referenced files.
WHEN the packet is validated.
THEN the validator SHALL verify schema, contained paths, exact hashes/counts, complete pool/query/label membership and stable source revision; malformed, missing or contradictory inputs SHALL fail without changing CorpusManifest/1 identity semantics.
VERIFY: Tamper/path/duplicate/dangling and unchanged legacy-hash regressions.

### AC-CD-02 (event-driven)
GIVEN typed source, author/reviewer and group declarations.
WHEN provenance readiness is assessed.
THEN the report SHALL distinguish verified byte references from self-attested identity/independence claims; missing authentic evidence SHALL remain unavailable and fixture declarations SHALL not qualify empirical readiness.
VERIFY: Missing-reference, same-reviewer and relabeled-fixture controls with explicit reasons.

### AC-CD-03 (event-driven)
GIVEN development, calibration and final partition families.
WHEN queries are assigned or imported.
THEN the validator SHALL reject pairwise family leakage by query identity, normalized content or declared related source groups, preserve upstream split names and freeze deterministic explicit assignments without inferring independent groups from hashes.
VERIFY: Development/calibration collision, final collision and related-source aliases; valid isolated assignment replay.

### AC-CD-04 (event-driven)
GIVEN positive, ambiguous or no-match labels against a frozen complete pool.
WHEN labels and runtime inputs are projected.
THEN positive targets SHALL belong to that pool; no-match SHALL require explicit pool-bound judgment/provenance rather than empty relevance or invalid IDs; runtime rows SHALL contain only query_id/query_text/compatibility_context.
VERIFY: Contradictory/no-match rationale and dangling targets fail; runtime gold/context injection fails.

### AC-CD-05 (event-driven)
GIVEN a project-temporal claim or unavailable event history.
WHEN temporal readiness is checked.
THEN the validator SHALL check distinct event/collection/annotation declarations and cutoff ordering from referenced evidence; absent authentic event history SHALL remain UNEVALUATED rather than receive invented timestamps.
VERIFY: Timezone/order/missing-evidence cases; fixture temporal fields never become authentic evidence.

### AC-CD-06 (event-driven)
GIVEN a valid packet and preregistration.
WHEN freeze and verification run.
THEN the workflow SHALL atomically bind source/runner/preregistration plus complete pool/query/label/partition/provenance identities; replay SHALL be deterministic and drift SHALL fail.
VERIFY: Two offline freezes have equal content identity; individual-byte and preregistration mutations fail.

### AC-CD-07 (event-driven)
GIVEN frozen final scoring labels requested through the qualification workflow.
WHEN evaluation-consumer access is requested.
THEN the guard SHALL verify bindings and durably record exact first-access intent before releasing labels; write failure or conflicting concurrent fresh access SHALL fail; exact replay SHALL retain exposure rather than claim untouched data.
VERIFY: Ordering, failed receipt persistence, race and explicit replay controls; local/out-of-band limits documented.

### AC-CD-08 (event-driven)
GIVEN existing final-consumer entrypoints and the committed official-test exposure.
WHEN qualification is requested with a boolean seal or a renamed experiment.
THEN qualifying validation SHALL reject a bare final_labels_opened flag and known exposed final identity; existing fixture execution SHALL remain explicitly diagnostic and incapable of qualification.
VERIFY: Actual retrieval/paired/abstention and claim-integrity callsite tests; actual official archive identity replay under new experiment fails fresh-final readiness.

### AC-CD-09 (event-driven)
GIVEN the reviewed synthetic/native control fixture on a clean candidate.
WHEN offline verification runs.
THEN all meaningful rejection and valid-path controls SHALL pass without model calls, ranking, calibration fitting/save/activation or source-data alteration; recorded outcomes SHALL identify actual source and inputs.
VERIFY: Independent fixture/callsite review and focused tests; no model/fit entrypoint call guards.

### AC-CD-10 (event-driven)
GIVEN control evidence and known missing authentic data.
WHEN the final report and archive are emitted.
THEN the report SHALL separate engineering controls from empirical obligations and retain authentic no-match/group/temporal/fresh-final/E2/E3/E6 readiness as UNEVALUATED where unsupported; archive and CI SHALL identify their exact sources without rewriting previous evidence.
VERIFY: Independent recomputation, complete input/result inventory, current-head CI and explicit limitations review.

