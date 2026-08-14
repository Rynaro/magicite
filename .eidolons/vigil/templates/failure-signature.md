# Failure Signature Template

Semantic memory entry written at the end of a successful VIGIL mission. Purpose: future missions can match recurring failures against prior attributions and reuse the verified intervention pattern.

Location: typically `memories/vigil-failures.yaml` in the host project, or wherever the team's shared semantic memory lives.

---

## Schema

```yaml
signature_id: VSIG-YYYYMMDD-NNN

# Categorical data — used for matching
normalized_error: "[short identifier, e.g. 'ballot.token = nil at RecordVote#call']"
error_class: LOGIC_ERROR | REGRESSION | BUILD_ERROR | TYPE_ERROR | LINT_VIOLATION | RUNTIME_ERROR | INTEGRATION_ERROR | ENVIRONMENT_ERROR | HEISENBUG | COMPOUND | SPEC_DEFECT
subcategory: "[optional finer tag, e.g. 'null-guard-missing', 'race-condition', 'version-mismatch']"
key_frames:
  - "[top stack frame]"
  - "[second frame]"
  - "[third frame]"
key_symbols:
  - "[function or method]"
  - "[another symbol]"

# Root cause summary
root_cause_summary: |
  [2–3 sentences describing the root cause in plain language.
  Not location-specific — describes the pattern, not the file.]
root_cause_path: "[path:lines — where the defect lives]"
root_cause_kind: code | config | schema | data | env

# Provenance
originating_commit: "[SHA]"
originating_date: "[ISO-8601 date]"
originating_pr: "[#NNN or null]"

# Fix pattern
intervention_pattern: |
  [How the fix worked, described as a reusable pattern.
  Example: "Add nil-coalescing fallback at call site, OR raise in
  generator on nil input."]
intervention_type: code_change | oracle_injection | state_correction | timing_fix
fix_scope: "[small | medium | large] — [N files]"

# Lifecycle data
first_seen: "[ISO-8601 — when this signature was first attributed]"
last_seen: "[ISO-8601 — same as first_seen initially; updated on match]"
frequency: 1                                    # incremented when matched
related_signatures: []                          # bidirectional refs to similar VSIGs

# Downstream routing (recorded for analytics)
downstream_route: APIVR-Delta | SPECTRA | IDG | human

# Mission provenance
mission_ref: VIGIL-YYYYMMDD-NNN
confidence: H | M | L
determinism_mode: deterministic | statistical
interventions_used: [N]
```

---

## De-duplication Matching

Before writing a new signature, VIGIL queries existing entries for matches on this scoring:

| Match dimension | Weight | Threshold |
|-----------------|--------|-----------|
| `error_class` exact | 3 | Required |
| `key_frames` overlap ≥2 | 2 | Required for match |
| `root_cause_path` prefix | 2 | Strong signal |
| `key_symbols` overlap ≥1 | 1 | Contributes |
| `subcategory` exact | 1 | Contributes |

**Total score ≥ 5** → match. Update existing entry:
- Increment `frequency`
- Update `last_seen` to current ISO-8601
- Append current `mission_ref` to list
- Add current new entry to `related_signatures` bidirectionally

**Score < 5** → write new signature.

---

## Consolidation (ledger hygiene)

The ledger has a soft cap of **50 entries** by default. When exceeded:

1. Entries with `frequency = 1` and `last_seen > 180 days ago` are archived to `memories/vigil-failures.archive.yaml`.
2. Entries with `frequency ≥ 5` are promoted to a `high-recurrence` section — these represent patterns worth attention from SPECTRA or human review.
3. Entries matching each other's `related_signatures` can be merged under a canonical entry if their `root_cause_summary` describes the same underlying pattern. Mark the canonical entry; preserve the merged mission refs.

Consolidation runs opportunistically at end of each mission; never mid-mission.

---

## Example

```yaml
signature_id: VSIG-20260416-001

normalized_error: "ballot.token = nil at RecordVote#call"
error_class: LOGIC_ERROR
subcategory: null-guard-missing
key_frames:
  - "RecordVote#call"
  - "TokenGenerator#generate_uuid"
  - "Session#secret_key"
key_symbols:
  - "generate_uuid"
  - "Session#secret_key"
  - "ballot.token"

root_cause_summary: |
  `generate_uuid` returns nil when its session argument lacks a
  secret_key attribute. No nil-guard exists at the call site. The
  function's contract does not document that it may return nil, and
  its callers assume a UUID is always returned.

root_cause_path: "app/utils/uuid_gen.rb:12-18"
root_cause_kind: code

originating_commit: "abc123def"
originating_date: "2026-03-14"
originating_pr: "#472"

intervention_pattern: |
  Add raise-on-nil at the generator boundary (producer-side fail-fast),
  OR add nil-coalescing fallback with SecureRandom.uuid at every call
  site. Producer-side preferred — discovers the broken contract
  immediately rather than masking it.

intervention_type: code_change
fix_scope: "small — 1 file, 2 lines"

first_seen: "2026-04-16T14:22:00Z"
last_seen: "2026-04-16T14:22:00Z"
frequency: 1
related_signatures: []

downstream_route: APIVR-Delta
mission_ref: VIGIL-20260416-001
confidence: H
determinism_mode: deterministic
interventions_used: 2
```

---

## Guidance

- **`normalized_error` is load-bearing for matching.** Keep it short and symbolic. Not the verbose error message — a categorical summary.
- **`root_cause_summary` describes the pattern, not the location.** Write it so a future VIGIL mission on a similar codebase would recognize the same pattern even at a different path.
- **`intervention_pattern` is reusable advice.** Write it as "how to fix failures like this," not "the exact diff we applied."
- **Don't skip `related_signatures` maintenance.** The ledger becomes more valuable as clusters of related failures link up. Bidirectional references matter.
- **Archive, don't delete.** The archive file preserves signatures that outlived their relevance but may still inform long-term pattern analysis.

---

*VIGIL — Failure Signature Template*
