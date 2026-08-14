# Architecture Decision Record Template

Use for recording architecture and design decisions with their context and consequences.

---

## Document Skeleton

```markdown
# ADR-[NNN]: [Decision Title]

**Status**: Proposed | Accepted | Deprecated | Superseded by ADR-[NNN]
**Date**: [date]
**Decision makers**: [who decided]
**Consulted**: [who was consulted]

---

## Context

[What situation or problem prompted this decision? What forces are at play — technical constraints, business requirements, team capabilities, timeline pressures?]

[Include relevant metrics, error rates, performance numbers, or user impact data if available in source material.]

---

## Decision

[State the decision clearly in 1–3 sentences.]

[DECISION] [Concise restatement for scanners]

---

## Rationale

[Why this option over the alternatives? What trade-offs were explicitly accepted?]

---

## Alternatives Considered

### [Alternative 1 Name]

- **Description**: [what it would involve]
- **Pros**: [advantages]
- **Cons**: [disadvantages]
- **Rejected because**: [specific reason]

### [Alternative 2 Name]

[Same structure. Include at least 2 alternatives for meaningful ADRs.]

---

## Consequences

### Positive

- [expected benefit 1]
- [expected benefit 2]

### Negative

- [accepted trade-off 1]
- [accepted trade-off 2]

### Risks

- [risk 1 — with mitigation if known]

---

## Follow-Up Actions

| Action | Owner | Priority |
|--------|-------|----------|
| [what] | [who] | P0/P1/P2 |

---

## Provenance

- **Scribe version**: <version>
- **Document type**: adr
- **Generated**: [timestamp]
- **Source artifacts**: [list; ECL envelope sources MAY be cited as `ecl://thread/<thread_id>/message/<message_id>`; when the envelope carries `ise.assertion_grade`, append it, e.g. `(validated)`, so upstream work that was mechanically validated is distinguishable from work that was only self-attested]
- **CHT scores**: C:[N]/5 H:[N]/5 T:[N]/5
- **Coverage**: [assessment]
- **Flags**: [any unresolved markers]
```

---

## Guidance

- **Scope**: One decision per ADR. If the session produced multiple architectural decisions, produce multiple ADRs.
- **Alternatives**: At least two considered alternatives are expected. If only one was considered, flag with `[GAP] No alternatives documented in source material`.
- **Consequences**: Be honest about negatives. An ADR that lists only positive consequences is suspect.
- **Numbering**: Continue the existing ADR numbering sequence in the project. If no sequence exists, start at 001.

---

*Scribe — ADR Template*
