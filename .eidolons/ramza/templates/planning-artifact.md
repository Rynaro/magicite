---
artifact: planning-artifact
version: 0.1.0
---

# Planning Artifact — RAMZA Output Template

RAMZA produces dual-format output: human-readable Markdown + agent-executable
structured data (YAML/JSON), plus the state file that is the audit trail.
Plans are never code.

## Output Contract

Every RAMZA Assemble phase produces:

1. **Markdown spec** (`.spectra/plans/<slug>.md`) — human-readable,
   reviewer-friendly, structured per the skeleton below.
2. **YAML/JSON block** — agent-executable structured data, embedded in the
   Markdown spec's Assemble section.
3. **`plan.json`** (`.spectra/plans/<slug>.plan.json`) — a Junction
   §7.5-compatible dispatch plan for the hand-off, when the consumer project
   runs Junction. Skeleton: `templates/plan.junction.json`.
4. **`<slug>.state.json`** — the plan-state audit trail (`schema:
   ramza/plan-state.v1`), maintained exclusively by the `bin/ramza-*` tools;
   never hand-edited (see `schemas/plan-state.v1.json`).
5. **`<slug>.envelope.json`** — the ECL envelope sidecar, only when
   `ECL_VERSION` is present (see "ECL Envelope Sidecar" below).

## Plan skeleton (section headings `ramza-lint` requires)

`ramza-lint --plan <file> --tier <tier>` (or `--state <state>`, which reads
the tier from the state file) checks for these `## `-prefixed sections,
additive by tier (see `docs/methodology/tiers.md`):

| Tier | Required sections |
|---|---|
| trivial | Scope, Approach, Acceptance Criteria (**and** ≤120 lines total) |
| lite | + Stories, Confidence |
| full | + Rejected Alternatives, Risks |

The skeleton below has every section a **full**-tier plan needs — a
trivial-tier plan simply omits Stories/Confidence/Rejected
Alternatives/Risks and stays under the 120-line budget; a lite-tier plan
omits only Rejected Alternatives/Risks.

```markdown
# <Plan title>

## Scope

Intent class: <IDEA|REQUEST|CHANGE|BUG_SPEC|STRATEGIC>
In: <what this plan covers>
Out: <what it explicitly does not cover>
Deferred: <what's postponed, and why>
Assumptions: <assumption> — risk if wrong: <risk>

Complexity (`ramza-score --rubric complexity`): <total>/12 → <standard|extended|human_loop>

## Approach

<The selected approach, in prose — the winning hypothesis from Explore, or the
single approach taken directly at trivial tier.>

## Stories

### Story 1: <title>

As a <role>, I want <capability>, so that <outcome>.
Timebox: <1d…8d, never points>.
Risk tag: <P0|P1|P2>.
Executor hint: <frontier|mid|economy> tier — <goals-only | +action plan | +explicit steps/schemas>.

## Acceptance Criteria

<EARS-form blocks per `templates/acceptance-criteria.md` — lintable by
`ramza-ears-lint`, one atomic assertion per criterion.>

### AC-001 (event-driven)
GIVEN <precondition>
WHEN  <trigger>
THEN  <single assertion>
VERIFY: <mechanical check>

## Confidence

`ramza-score --rubric confidence`: <total>% → <AUTO_PROCEED|VALIDATE|COLLABORATE|ESCALATE>

## Rejected Alternatives

- **<Hypothesis B>** — `ramza-score --rubric explore` total <X>: <why it lost, per-dimension>.
- **<Hypothesis C>** — total <Y>: <why it lost>.

## Risks

| Risk | Tag | Mitigation |
|---|---|---|
| <risk> | P0\|P1\|P2 | <mitigation> |
```

---

## ECL Envelope Sidecar

When `ECL_VERSION` is present in the install root, every Assemble output
includes a fifth file alongside the Markdown + `plan.json` + state triple:

**File location:** `<payload>.envelope.json` — sibling of the Markdown spec at
`.spectra/plans/{date}-{feature}.envelope.json`.

**Required fields (per ECL v2.0 §1.1):**

| Field | Value |
|-------|-------|
| `envelope_version` | `"2.0"` |
| `message_id` | UUIDv7 (unique per emission) |
| `thread_id` | UUIDv7 (same for all envelopes in a mission) |
| `parent_id` | `null` (RAMZA is the thread initiator on this edge) |
| `from.eidolon` | `"ramza"` |
| `from.version` | SemVer of the installed RAMZA |
| `to.eidolon` | `"apivr"` |
| `performative` | `"PROPOSE"` |
| `artifact.kind` | `"spec"` |
| `artifact.sha256` | sha256 hex digest of the Markdown payload bytes |
| `integrity.method` | `"sha256"` |
| `integrity.value` | MUST equal `artifact.sha256` |
| `trace.ts` | RFC 3339 UTC timestamp at emit time |
| `trace.host` | Host environment slug (e.g. `claude-code`) |
| `trace.model` | Model identifier (e.g. `claude-sonnet-5`) |
| `trace.tier` | `"standard"` (or `"trance"` for TRANCE-tier sessions) |

**Optional ISE block (ECL v2.0 §6.5):** `ise.assertion_grade` is
`"self-attested"` — a spec is decision-ready, not externally verified.
`ise.receiver_authorization` is `{auto_route: true, auto_merge: false,
auto_deploy: false}`. `ise.provenance.methodology_version` is
`"ramza-<installed-version>"`.

**Acceptance-criteria hash:** carry the frozen SHA-256 of the criteria content
(`ramza-freeze`'s output) as the `x_ramza_acceptance_criteria` vendor extension
(`{path, sha256}`) so a downstream verifier can prove the checks it runs are
the exact set frozen at spec time (see `templates/acceptance-criteria.md`).

**sha256 anchor:** The integrity check is the hex digest of the Markdown file
bytes at the moment of emission. `ramza-verify-emit --spec <spec> --envelope
<envelope>` recomputes this and DENIES the emission on a mismatch — this is
the mandatory Assemble exit gate, not a downstream-only check.

**When emitted:** Only when `ECL_VERSION` is present in the install root.
Non-ECL consumers ignore the file entirely.

**Template:** Use `templates/spec.envelope.json` as the skeleton — fill every
`<placeholder>` before emitting. Validate with `ramza-verify-emit --spec
<spec> --envelope <envelope>` before handing off (`schemas/ecl-envelope.v1.json`
is retained for the ECL §7.3 back-compat window; new specs validate against
`schemas/ecl-envelope.v2.json`).

---

*RAMZA — Planning Artifact Template*
