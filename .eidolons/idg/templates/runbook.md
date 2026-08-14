# Runbook Template

Use for operational procedures — deployments, incident response, maintenance tasks, recovery procedures.

---

## Document Skeleton

```markdown
# Runbook: [Procedure Name]

**Last updated**: [date]
**Owner**: [team or individual]
**Frequency**: On-demand | Scheduled ([cadence]) | Triggered by [event]
**Estimated duration**: [time]
**Risk level**: Low | Medium | High | Critical

---

## Summary

[1–2 sentences: what this runbook does and when to use it.]

---

## Prerequisites

- [ ] [Access/permission requirement]
- [ ] [Tool/system availability]
- [ ] [Data/artifact requirement]
- [ ] [Communication requirement — who to notify before starting]

---

## Steps

### Step 1: [Action Title]

**Command/Action**:
```bash
[exact command or UI action]
```

**Expected output**: [what success looks like]
**If this fails**: [specific recovery action or link to troubleshooting]

### Step 2: [Action Title]

[Same structure. Every step must have expected output and failure handling.]

---

## Verification

[How to confirm the procedure completed successfully.]

```bash
[verification command(s)]
```

**Expected result**: [what confirms success]

---

## Rollback

[How to undo this procedure if something goes wrong. This section is mandatory for Medium+ risk runbooks.]

### Rollback Steps

1. [Step to revert change 1]
2. [Step to revert change 2]

### Rollback Verification

[How to confirm the rollback succeeded]

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| [what you see] | [why it happens] | [what to do] |

---

## Follow-Up Actions

| Action | Owner | Trigger |
|--------|-------|---------|
| [what] | [who] | [when/what triggers it] |

---

## Provenance

- **Scribe version**: <version>
- **Document type**: runbook
- **Generated**: [timestamp]
- **Source artifacts**: [list; ECL envelope sources MAY be cited as `ecl://thread/<thread_id>/message/<message_id>`; when the envelope carries `ise.assertion_grade`, append it, e.g. `(validated)`, so upstream work that was mechanically validated is distinguishable from work that was only self-attested]
- **CHT scores**: C:[N]/5 H:[N]/5 T:[N]/5
- **Coverage**: [assessment]
- **Flags**: [any unresolved markers]
```

---

## Guidance

- **Exactness**: Commands must be copy-pasteable. No pseudo-code or "something like" instructions.
- **Failure handling**: Every step needs a "if this fails" clause. A runbook without failure handling is dangerous.
- **Rollback**: Mandatory for Medium+ risk. If rollback is not possible, document that explicitly with `[GAP] No rollback procedure available — this operation is irreversible`.
- **Verification**: Both post-procedure and post-rollback verification must be included.
- **Audience**: Assume the reader is a competent engineer who has never performed this specific procedure before. Include enough context to act independently at 3 AM during an incident.

---

*Scribe — Runbook Template*
