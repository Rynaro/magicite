# Session Chronicle Template

Use for documenting coding sessions — what happened, what changed, what was decided, what's next.

---

## Document Skeleton

```markdown
# Session Chronicle: [Topic]

**Date**: [date]
**Duration**: [estimated duration]
**Agents/Participants**: [who was involved]
**Scope**: [what was being worked on]

---

## Summary

[2–4 sentences: what was accomplished, key outcome, overall status]

---

## Context

[What was the starting state? Why was this session initiated? Link to relevant specs, tickets, or prior chronicles if available.]

---

## Work Performed

### [Phase/Task 1 Title]

[What was done, in what order. Include file paths, commands, and tool outputs where they clarify the narrative.]

[DECISION] markers inline where choices were made.

### [Phase/Task 2 Title]

[Continue chronologically or by logical grouping. Use subsections for distinct work streams.]

---

## Decisions Made

| Decision | Rationale | Alternatives Considered |
|----------|-----------|------------------------|
| [what] | [why] | [what was rejected and why] |

---

## Issues Encountered

[Problems hit during the session. For each: what happened, how it was resolved (or if it wasn't).]

[DISPUTED] markers if diagnostic information conflicts.

---

## Changes Produced

| File/Component | Change Type | Description |
|---------------|-------------|-------------|
| [path] | Created / Modified / Deleted | [brief description] |

---

## Follow-Up Actions

| Action | Owner | Priority | Deadline/Trigger |
|--------|-------|----------|-----------------|
| [what] | [who] | P0/P1/P2 | [when] |

---

## Lessons Learned

[What went well, what was harder than expected, what the team should remember for next time. Keep this honest — it's the most valuable section for future sessions.]

---

## Communication Lineage

> OPTIONAL — Populate when source artefacts arrived inside ECL v2.0 envelopes.
> Omit this section entirely when no source carried an envelope.

| Step | from → to | performative | message_id | verify | ise_grade |
|---|---|---|---|---|---|
| 1 | … → idg | … | … | pass | unverified / self-attested / validated / human-reviewed / — |

---

## Provenance

- **Scribe version**: <version>
- **Document type**: session-chronicle
- **Generated**: [timestamp]
- **Source artifacts**: [list; ECL envelope sources may be cited as `ecl://thread/<thread_id>/message/<message_id>`; when the envelope carries `ise.assertion_grade`, append it, e.g. `(validated)`, so upstream work that was mechanically validated is distinguishable from work that was only self-attested]
- **CHT scores**: C:[N]/5 H:[N]/5 T:[N]/5
- **Coverage**: [assessment]
- **Flags**: [any unresolved markers]
```

---

## Guidance

- **Chronological vs. logical grouping**: Default to chronological. Switch to logical grouping only if the session involved parallel work streams.
- **Granularity**: Include enough detail that someone who wasn't present can understand what happened and why. Omit mechanical steps that don't contribute to understanding (e.g., "ran `cd` to change directory").
- **Code snippets**: Include only when they illustrate a decision or a non-obvious approach. Don't reproduce entire files.
- **Failures**: Document them honestly. Failed approaches are as valuable as successful ones for future sessions.

---

*Scribe — Session Chronicle Template*
