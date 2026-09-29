# ADR-001: Stable dense-v1 as the V1 routing incumbent

**Status**: Accepted
**Date**: 2026-09-29
**Decision makers**: Magicite V1 / S00 implementation (Vivi)
**Consulted**: RAMZA contracts C0/C4/C7; ATLAS scout A01/A12/A13

---

## Context

Production routing blended dense similarity, graph activation, retrieval
strength, excitability and community rerank unconditionally. Dream wrote
node/edge strengths that the default scorer consumed, so answers drifted
with popularity and consolidation. V1 requires a reproducible nonadaptive
default; adaptation may remain only as an explicit experimental family.

## Decision

[DECISION] The default routing policy is `dense-v1`: eligible cosine
similarity with stable-ID tie-breaks. Legacy adaptive blend is available
only as `experimental/adaptive-blend-v1` via explicit `Config.routing_policy`
(or `MAGICITE_ROUTING_POLICY`). Dream may still checkpoint historical
strength; it must not change `active_stable_policy_digest()`.

## Rationale

- Dense was already the strongest simple baseline in published v0.3
  evidence (Hit@1 0.5476 vs full blend 0.5333 on the pinned toy/bench
  artifact) and matches C4's initial incumbent.
- Explicit policy identity makes fallback/rollback fingerprints durable
  for S07 policy_store activation without implying learning.
- Separating historical plasticity from stable scoring satisfies C0/C7
  isolation without deleting research baselines.

## Alternatives Considered

### Keep implicit adaptive blend as default

- **Description**: Leave router defaults as the pre-V1 weighted blend.
- **Pros**: No ranking change for existing adaptive-tuned expectations.
- **Cons**: Violates C0 nonadaptive default; Dream mutates answers.
- **Rejected because**: Release gate POLICY requires nonadaptive default.

### Sparse/trigger incumbent instead of dense

- **Description**: Freeze sparse or trigger as the simple incumbent.
- **Pros**: Might win on a future development split.
- **Cons**: No stronger pinned evidence yet; C4 already names dense-v1 as
  the initial incumbent pending evaluated replacement via S07.
- **Rejected because**: Premature relative to evaluation.md promotion rules.

## Consequences

### Positive

- Stable answers ignore usage/strength/community/Dream state.
- Experimental research path retained behind visible diagnostics.
- Raw route query text no longer persisted in `eph_event` payloads.

### Negative

- Adaptive unit/acceptance tests must opt into the experimental policy.
- MCP `RouteOutput` does not yet surface `policy_id` (S11 follow-up).

### Risks

- Callers that relied on implicit adaptation see ranking changes until
  they set `routing_policy=experimental/adaptive-blend-v1` — mitigated by
  documenting the config knob and preserving the experimental path.

## Follow-Up Actions

| Action | Owner | Priority |
|--------|-------|----------|
| Wire `policy_id`/`policy_digest` onto public RouteOutput | S11 | P0 |
| Reviewed activation via policy_store | S07 | P0 |
| Durable evidence ledger / historical query cleanup | S09 | P0 |
| Index this ADR from docs authority | S15 | P1 |

## Provenance

- **Document type**: adr
- **Generated**: 2026-09-29
- **Source artifacts**: `.spectra/plans/magicite-v1/slices/s00-stable-policy-boundary-and-dense-incumbent.md`, `contracts.md` C0/C4/C7
- **Flags**: none
