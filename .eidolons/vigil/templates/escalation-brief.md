# Escalation Brief Template

Emitted when the VIGIL mission cannot produce a verified `[ROOT-CAUSE]` within the 5-intervention budget. This is NOT a failure of the agent — it is the correct response to genuine ambiguity. The brief preserves all evidence gathered so downstream (FORGE, human, or a re-scoped VIGIL mission) can continue.

---

## Document Skeleton

```markdown
# VIGIL Escalation Brief

**Mission ID**: VIGIL-YYYYMMDD-NNN
**Escalation reason**: [budget_exhausted | intermittent_reproduction | cyclic_graph | disputed_oracle | authority_insufficient]
**Generated**: [ISO-8601]
**Routing target**: FORGE | human | re-scoped VIGIL

---

## Executive Summary

[3–5 sentences. What failure was investigated. What was attempted. What remains unresolved. What evidence is needed.]

---

## The Failure

**Classification (tentative)**: [category from failure taxonomy]

**Reproduction**:
- Mode: deterministic | statistical | not_reproduced
- Signature: [brief]
- Determinism verdict: [stable | flaky | intermittent | not_reproduced]

[If `not_reproduced`: escalation type is `intermittent_reproduction`. The brief documents what was tried and what env deltas or timing windows might explain the inability to reproduce.]

---

## Fault Surface Investigated

[From `fault-surface.md`. Narrow list of candidates examined.]

| Candidate | Path | Status after Intervene |
|-----------|------|------------------------|
| C-001 | [path:lines] | falsified by I-001 |
| C-002 | [path:lines] | falsified by I-002 |
| C-003 | [path:lines] | partial flip — see notes |
| C-004 | [path:lines] | not tested (budget exhausted) |

---

## Dependency Graph

[From `idg.md`. Summary of structural relationships.]

**Graph shape**: [normal | cyclic | disconnected | single_node | no_roots]

[If cyclic: describe the cycle. If no_roots: describe why the true root seems to be outside the explored scope.]

**Root candidates considered**:
- [N-ID] — [descendant count N] — falsified
- [N-ID] — [descendant count N] — falsified
- [N-ID] — [descendant count N] — not tested

---

## Interventions Attempted

All 5 interventions (or fewer if halted for a different reason):

### I-001: [one-line description]

- **Hypothesis**: H-[NNN]
- **Type**: [type]
- **Target**: [path:lines]
- **Result**: NO_CHANGE | NEW_FAILURE | FLIPPED (partial — see notes) | ERROR
- **Runs**: [1 deterministic | 5 statistical with N/M passing]
- **What it rules out**: [short prose]

### I-002: ...

[Continue for each intervention.]

---

## Competing Hypotheses Remaining

[Hypotheses that were not fully falsified. These are FORGE's or human's starting points.]

### [HYPOTHESIS-N]: [one-line description]

- **Based on candidate**: [N-ID]
- **Mechanism**: [description]
- **Why not tested**: [budget exhausted | requires authority we don't have | requires cross-component change | other]
- **Falsification criterion**: [what intervention would confirm or deny]
- **Prior confidence**: low | medium

---

## Observed Contradictions

[Record [DISPUTED] findings here — places where evidence pointed two directions.]

### [DISPUTED]: [one-line description]

**Evidence for A**: [brief with citation]
**Evidence for B**: [brief with citation]
**Why unresolved**: [what evidence or broader reasoning would resolve]

---

## What Evidence Would Resolve This

[The single most valuable section of this brief. Specific, actionable.]

1. [What evidence type, from where, that would flip one of the remaining hypotheses]
2. [What broader reasoning or domain knowledge — if routing to FORGE]
3. [What human-only judgment — if routing to human]

Example entries:

- "Production logs from the last 7 days for requests hitting `/vote` endpoint, grepped for `secret_key` null responses. Not available in test env."
- "Clarification from product team: is the spec's 'token' field intended to be session-scoped or global? The current implementation ambiguously mixes both."
- "Wider codebase analysis beyond the scoped mission: does any other call site of `TokenGenerator#generate_uuid` handle nil? If yes, the defect is absence of that handling here; if no, the whole contract is broken."

---

## What Has Been Ruled Out

[Negative findings with the same weight as positive ones. These save downstream time.]

- [Candidate/hypothesis] — ruled out by [I-NNN | evidence source] — [brief]
- [Candidate/hypothesis] — ruled out by [source] — [brief]

---

## Telemetry

```yaml
tokens_in: [N]
tokens_out: [N]
tool_calls_total: [N]
interventions_used: [N]
interventions_cap: 5
phase_durations_s:
  verify: [N]
  isolate: [N]
  graph: [N]
  intervene: [N]
wall_clock_s: [N]
escalation_reason: budget_exhausted | intermittent_reproduction | cyclic_graph | disputed_oracle | authority_insufficient
```

---

## Handoff

```yaml
primary_recipient: FORGE | human | VIGIL (re-scoped)
rationale: "[one-sentence reason]"
artifact_path: "[path to this brief]"
supplementary_artifacts:
  - reproduction.md
  - fault-surface.md
  - idg.md
  - intervention-log.md
fallback_recipient: human
recommended_next_action: |
  [Specific prose instruction for the recipient. Not "figure it out" —
  something actionable. Example: "Request production-log access for
  the /vote endpoint, then re-invoke VIGIL with the expanded evidence
  as upstream artifact."]
```

---

## Provenance

- **VIGIL version**: <version>
- **Generated**: [ISO-8601]
- **Failure signature**: [VSIG-YYYYMMDD-NNN — partial entry written with confidence=L, marked as open]
- **Flags**: [GAP | DISPUTED | FLAKE — applicable markers]
```

---

## Guidance

- **Escalation is a success, not a failure.** If the mission was genuinely ambiguous within the 5-intervention budget, this brief is the highest-value deliverable VIGIL could produce — a structured evidence bundle that will accelerate whoever comes next.
- **"What evidence would resolve this" is mandatory.** Do not escalate with "I don't know what to do." Always name what would unblock.
- **Ruled-out entries save cycles.** Do not omit them to keep the brief short. The reason we escalated the mission is precisely because ambiguity exists — preserving the negative findings is how downstream avoids re-treading.
- **Route honestly.** FORGE is for reasoning ambiguity (multiple hypotheses remain, needs broader analysis). Human is for judgment/policy calls (spec defects, authority decisions, safety-critical). Re-scoped VIGIL is for evidence gaps (need more logs, need wider scope).
- **Partial entries in the failure-signature ledger.** Write a VSIG entry with `confidence: L` and a note that the mission escalated. If a future mission resolves this, it can upgrade the entry.

---

*VIGIL — Escalation Brief Template*
