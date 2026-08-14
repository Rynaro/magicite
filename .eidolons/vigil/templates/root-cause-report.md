# Root-Cause Report Template

> **ECL v2.0 note:** When emitted via VIGIL, this payload is accompanied by `<basename>.envelope.json` (or `<basename>.envelope.<recipient>.json` for fan-out) per ECL v2.0. Body shape unchanged — the envelope wraps but does not redefine. The envelope's optional `ise.assertion_grade` is conditional on authority; see `skills/intervene.md` § ISE Grade on the Root-Cause Report. See `skills/learn.md` § Envelope Emission for the sidecar construction procedure.

The primary deliverable of a VIGIL mission. Fill each section with evidence-anchored content. Schema-validated at emission.

---

## Document Skeleton

```markdown
# Root-Cause Report: [Failure Title]

**Mission ID**: VIGIL-YYYYMMDD-NNN
**Date**: [ISO-8601]
**Authority**: read-only | sandbox | write
**Upstream trigger**: [APIVR-Δ escalation | consultant invocation | post-hoc review]
**Entry artifact**: [path/to/upstream/artifact]

---

## Summary

[One sentence stating the root cause in plain language. No hedging. If you cannot write this sentence without hedging, the attribution is not high-confidence enough — re-enter Intervene or escalate.]

**Classification**: [LOGIC_ERROR | REGRESSION | BUILD_ERROR | TYPE_ERROR | LINT_VIOLATION | RUNTIME_ERROR | INTEGRATION_ERROR | ENVIRONMENT_ERROR | HEISENBUG | COMPOUND | SPEC_DEFECT]

**Confidence**: H | M | L

**Handoff**: → [APIVR-Δ | SPECTRA | IDG | FORGE | human]

---

## Reproduction

**Mode**: deterministic | statistical

**Invocation**:
```
[exact command]
```

**Observed signature**:
- Error class: [class]
- Assertion: [observable symptom]
- Stack top:
  1. [frame]
  2. [frame]
  3. [frame]

**Evidence**:
- Deterministic mode: [N] consistent runs with matching signature
- Statistical mode: [N] failures in [M] runs, failure rate [X], CI95 [L, H]

[FLAKE] marker set if statistical. Otherwise omit.

---

## Fault Surface

[Summarize the Isolate phase result. Reference `fault-surface.md` by path.]

| Candidate | Path | Rank | Rationale |
|-----------|------|------|-----------|
| C-001 | [path:lines] | 1 | [one-line reason] |
| C-002 | [path:lines] | 2 | [one-line reason] |

**Ruled out** (key entries):
- [path] — [why]
- [path] — [why]

---

## Information Dependency Graph

[Summarize the Graph phase result. Reference `idg.md` by path.]

**Root candidates**:
- [N-ID] at [path:lines] — descendant count: [N] — rank: [N]

**Symptom nodes** (explicitly NOT root cause):
- [N-ID] at [path:lines] — [SYMPTOM]
- [N-ID] at [path:lines] — [SYMPTOM]

**Graph shape**: normal | cyclic | disconnected | single_node

[If cyclic or disputed, explain inline and note escalation.]

---

## Intervention Log Summary

[Summarize the Intervene phase. Reference `intervention-log.md` by path.]

| Intervention | Hypothesis | Type | Result |
|--------------|------------|------|--------|
| I-001 | H-001 | oracle_injection | FLIPPED |
| I-002 | H-002 | code_change | NO_CHANGE |

**Survivor**: I-001 / H-001

**Budget used**: [N] / 5

---

## Findings

[One [FINDING-NNN] per factual claim. All must be anchored.]

### [FINDING-001]

**Claim**: [One-sentence factual statement. Declarative.]

**Evidence**:
- path: [path]
  lines: [start-end]
  excerpt_ref: memex://excerpt/[hash]
- path: [path]
  lines: [start-end]
  excerpt_ref: memex://excerpt/[hash]

**Confidence**: H | M | L

**Counterfactual result**: FLIPPED | NO_CHANGE | N/A (supporting finding)

**Intervention ref**: I-[NNN] (if applicable)

### [FINDING-002]
...

---

## Root Cause

[The verified root cause. Prose, ≤200 words. Cites findings by ID.]

**[ROOT-CAUSE]** [FINDING-001] describes the primary mechanism. The failure propagates through [FINDING-002] and [FINDING-003] to produce the observed assertion.

**Why this is the root cause**: The counterfactual intervention I-[NNN] — [brief description] — flipped the failure to success in [deterministic: 1 run | statistical: N/M runs]. No other hypothesis survived falsification within the 5-intervention budget.

---

## Originating Decision

[Walk-back to where and when the defect was introduced.]

### [FINDING-NNN]

**Claim**: The defect was introduced in commit `[SHA]` on [date], which [summary of change].

**Evidence**:
- commit: [SHA]
- message: "[commit message]"
- PR/issue: [#NNN]
- path: [path]
  lines: [start-end]

**Classification**: implementation_bug | spec_defect | contract_drift | dep_change | missing_test | env_drift

**Downstream route**: APIVR-Δ | SPECTRA | human

---

## Recommended Fix

[If authority ≥ sandbox and fix is a code change: "See `verified-patch.diff`."]

[If authority = read-only: describe the fix textually, specify that execution and verification must happen downstream.]

[If fix is NOT a code change: describe the required action — spec revision, contract migration, env correction — and route appropriately.]

**Scope**: [files affected, by path]
**Risk**: low | medium | high — [one-line rationale]
**Verification**: [how downstream confirms the fix works — which tests to run, what to check]

---

## Compound Findings (if any)

[Populated only if intervention runs produced `NEW_FAILURE` outcomes. These are secondary issues surfaced by the investigation but not the primary root cause. Route separately.]

| Finding | Description | Recommended downstream |
|---------|-------------|------------------------|
| — | — | — |

---

## Handoff

```yaml
primary_recipient: APIVR-Delta | SPECTRA | IDG | FORGE | human
rationale: "[one-sentence routing reason]"
artifact_path: "[path to this report]"
supplementary_artifacts:
  - intervention-log.md
  - verified-patch.diff        # if emitted
  - fault-surface.md
  - idg.md
  - reproduction.md
fallback_recipient: human
```

---

## Telemetry

```yaml
tokens_in: [N]
tokens_out: [N]
tool_calls:
  verify: [N]
  isolate: [N]
  graph: [N]
  intervene: [N]
  learn: [N]
interventions_used: [N]
interventions_cap: 5
wall_clock_s: [N]
phase_breakdown:
  verify: [seconds]
  isolate: [seconds]
  graph: [seconds]
  intervene: [seconds]
  learn: [seconds]
```

---

## Provenance

- **VIGIL version**: <version>
- **Methodology version**: 1.0
- **Generated**: [ISO-8601]
- **Failure signature ref**: [VSIG-YYYYMMDD-NNN] (memory entry)
- **Flags**: [FLAKE | DISPUTED | GAP — only if applicable]
```

---

## Guidance

- **Summary must be one sentence.** If you need two, your attribution has ambiguity — escalate or re-run Intervene.
- **Findings are the load-bearing artifact.** Everything else is scaffolding around them. Do not assert anything in prose that is not backed by a finding.
- **`[ROOT-CAUSE]` requires `FLIPPED` counterfactual.** No exceptions. If you did not get a flip, emit `escalation-brief.md` instead of this report.
- **Originating decision is mandatory, not optional.** If you cannot trace the commit, emit `[GAP]` and flag — don't skip.
- **Telemetry is how we verify the 5-intervention cap held.** Always populate.

---

*VIGIL — Root-Cause Report Template*
