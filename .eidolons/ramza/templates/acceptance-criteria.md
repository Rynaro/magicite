---
artifact: acceptance-criteria
version: 0.1.0
---

# Acceptance Criteria — EARS Form Template (lintable)

This template is the canonical form for a RAMZA story's `## Acceptance Criteria`
section — not optional polish on top of something else. It is the exact
grammar `bin/ramza-ears-lint` parses mechanically; every acceptance-criteria
block in a RAMZA spec MUST match this shape or the lint fails at Test (T), and
`ramza-lint`'s `## Acceptance Criteria` section requirement (trivial/lite/full,
see `docs/methodology/tiers.md`) expects to find blocks in this shape inside it.

## Block shape (what `ramza-ears-lint` actually parses)

```
### AC-<id> (<form>)
GIVEN <precondition>
WHEN  <trigger>
THEN  <single assertion — no " AND ">
VERIFY: <mechanical check: test name, command, or gate>
```

Mechanically, `ramza-ears-lint` checks exactly four things per block, nothing
more:

1. The heading matches `### AC-<id> (<form>)` — `<id>` must look like
   `AC-[A-Za-z0-9_-]+` and be unique across the file; `<form>` (the
   parenthesized token) must be one of the five closed values below.
2. Exactly one `THEN ` line is present (zero ⇒ "missing THEN"; more than one
   ⇒ flagged as multiple assertions in one criterion).
3. That `THEN ` line contains no literal `" AND "` — a compound assertion
   must split into two criteria instead.
4. A `VERIFY:` line with a non-empty method is present.

`GIVEN`/`WHEN` lines are not themselves re-validated by the lint — they carry
the human-readable precondition/trigger for reviewers, but the mechanical
contract is on the heading, the `THEN` line, and the `VERIFY:` line. Any other
line inside a block (prose, blank lines) is simply ignored by the parser.

## The closed EARS grammar (five fixed forms)

`<form>` must be exactly one of these five values — anything else is an
"unknown form" violation:

| # | Form | Sentence pattern | GIVEN | WHEN | THEN |
|---|------|-------------------|---------|--------|--------|
| 1 | **ubiquitous** | THE SYSTEM SHALL \<response\> | — | — | \<response\> (always true, no trigger/state) |
| 2 | **event-driven** | WHEN \<trigger\> THE SYSTEM SHALL \<response\> | optional extra precondition | \<trigger\> | \<response\> |
| 3 | **state-driven** | WHILE \<state\> THE SYSTEM SHALL \<response\> | \<state\> | — | \<response\> |
| 4 | **unwanted-behavior** | IF \<trigger\> THEN THE SYSTEM SHALL \<response\> | optional extra precondition | \<trigger\> (guard/error condition) | \<response\>, phrased as the safe fallback |
| 5 | **optional-feature** | WHERE \<feature is included\> THE SYSTEM SHALL \<response\> | \<feature enabled/present\> | — | \<response\> |

## The rule: one criterion ↔ one mechanically checkable assertion

Each criterion verifies exactly ONE thing. Do not compound two assertions
behind a single `AND` inside one `THEN` line — split into two IDs instead
(`ramza-ears-lint` flags a `THEN` containing `" AND "` as a violation). This is
what makes `VERIFY:` possible at all: a checker must be able to run one test,
one gate, or one grep per ID and get a binary pass/fail. A criterion that
silently bundles two behaviors cannot be re-derived unambiguously later —
which defeats drift/re-derivation checks downstream (`ramza-drift`, ESL's
`drift_check`).

## Frozen at Assemble (hashed, tamper-evident)

Acceptance criteria are **frozen** the moment Assemble runs `ramza-freeze`:

```
ramza-freeze --state <state> --criteria <this-story's-criteria-file-or-section>
```

`ramza-freeze` computes the SHA-256 of the criteria content and records it in
the state file (`criteria_sha256`); the same hash rides the ECL spec envelope
as the `x_ramza_acceptance_criteria` vendor extension (see
`templates/spec.envelope.json`). Editing the criteria after freeze without
`ramza-freeze --amend --reason` makes a later `ramza-freeze --verify` fail —
that hash mismatch IS the tamper signal, never a re-derivable "looks
different" judgment. A downstream verifier (Kupo, or ESL's `drift_check`
transition) can recompute this digest and prove the checks it ran are the
exact set frozen at spec time.

## Worked examples (round-trip tested against `bin/ramza-ears-lint`)

```
### AC-001 (event-driven)
GIVEN the service has a healthy database connection pool
WHEN a GET request arrives at /healthz
THEN the endpoint SHALL respond within 200ms with HTTP 200 and body {"status":"ok","version":"<semver>"}
VERIFY: test: spec/requests/healthz_spec.rb#responds_ok

### AC-002 (unwanted-behavior)
GIVEN the database connection pool is exhausted
WHEN a health check probe times out after 2s
THEN the endpoint SHALL respond HTTP 503 with {"status":"degraded","reason":"db_timeout"}, never HTTP 200
VERIFY: test: spec/requests/healthz_spec.rb#responds_degraded_on_db_timeout

### AC-003 (state-driven)
GIVEN the service is running in maintenance mode (MAINTENANCE_MODE=true)
THEN the endpoint SHALL respond HTTP 200 with {"status":"maintenance"} regardless of downstream dependency health
VERIFY: test: spec/requests/healthz_spec.rb#responds_maintenance_when_flagged

### AC-004 (ubiquitous)
THEN the endpoint SHALL never expose downstream connection strings or credentials in its response body
VERIFY: test: spec/requests/healthz_spec.rb#never_leaks_credentials

### AC-005 (optional-feature)
GIVEN the deployment enables the readiness-probe feature flag
THEN the endpoint SHALL expose a separate /readyz route in addition to /healthz
VERIFY: test: spec/requests/healthz_spec.rb#readyz_present_when_flagged
```

Run the lint yourself before trusting any of the above. `ramza-ears-lint` is a
naive grep-level parser (by design — structure is mechanical, prose is not):
it matches ANY line starting with `### AC-`, including the illustrative
`### AC-<id> (<form>)` placeholder in "Block shape" above, which is NOT a real
criterion and correctly does NOT pass (its placeholder tokens are not a valid
ID or a closed-set form, and its prose `THEN` line names the literal word
"AND" as part of explaining the compound-assertion rule). Extract just the
"Worked examples" block above to a file and lint that in isolation:

```
awk '/^### AC-001/,/^VERIFY: test: spec\/requests\/healthz_spec.rb#readyz_present_when_flagged/' \
  templates/acceptance-criteria.md > /tmp/ac-worked-examples.md
bin/ramza-ears-lint /tmp/ac-worked-examples.md
# ok: 5 criteria pass EARS lint
```

## What this template does NOT do

- It does not re-declare ESL's `change.v1.json` schema — the field names
  (`id`, `given`, `when`, `then`, `verify_method`) referenced by ESL 1.1 §2.5
  correspond to this Markdown block's ID/GIVEN/WHEN/THEN/VERIFY lines by
  convention, not by re-declaration (ESL MUST NOT re-declare RAMZA's grammar,
  and RAMZA correspondingly does not re-declare ESL's manifest schema).
- It is never advisory-only. Unlike ESL 1.1's SHOULD-level C7 lint,
  `ramza-ears-lint` is a hard gate at Test (T): any violation exits 1 and
  blocks the phase.

---

*RAMZA — Acceptance Criteria Template (EARS form, lintable)*
