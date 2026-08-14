# Change Narrative Template

Use for release notes, changelogs, and PR summaries — narrating what changed and why.

---

## Document Skeleton

```markdown
# [Release/Change Title]

**Version**: [version or PR identifier]
**Date**: [date]
**Authors**: [who made the changes]

---

## Overview

[2–3 sentences: what changed at a high level and why it matters to the reader.]

---

## Changes

### Added

- **[Feature/Component name]** — [what it does and why it was added]. ([source])

### Changed

- **[Feature/Component name]** — [what changed and why]. ([source])

### Fixed

- **[Bug/Issue description]** — [what was wrong and how it was fixed]. ([source])

### Removed

- **[Feature/Component name]** — [what was removed and why]. ([source])

### Deprecated

- **[Feature/Component name]** — [what is being deprecated, timeline, migration path]. ([source])

---

## Breaking Changes

[If any changes break backward compatibility, document them prominently here.]

| Change | Impact | Migration |
|--------|--------|-----------|
| [what changed] | [what breaks] | [how to adapt] |

[If no breaking changes: "No breaking changes in this release."]

---

## Technical Notes

[Implementation details relevant to engineers — architectural choices, performance implications, known limitations. Skip this section for user-facing release notes.]

---

## Follow-Up Actions

| Action | Owner | Priority |
|--------|-------|----------|
| [what] | [who] | P0/P1/P2 |

---

## Provenance

- **Scribe version**: <version>
- **Document type**: change-narrative
- **Generated**: [timestamp]
- **Source artifacts**: [list; ECL envelope sources MAY be cited as `ecl://thread/<thread_id>/message/<message_id>`; when the envelope carries `ise.assertion_grade`, append it, e.g. `(validated)`, so upstream work that was mechanically validated is distinguishable from work that was only self-attested]
- **CHT scores**: C:[N]/5 H:[N]/5 T:[N]/5
- **Coverage**: [assessment]
- **Flags**: [any unresolved markers]
```

---

## Guidance

- **Audience awareness**: Release notes for end users should omit Technical Notes and use benefit-oriented language ("Faster page loads" not "Reduced p95 latency by 40ms"). Internal changelogs can include full technical detail.
- **Categorization**: Use the Keep a Changelog categories (Added, Changed, Fixed, Removed, Deprecated). If a change doesn't fit cleanly, prefer the category closest to the user impact.
- **Breaking changes**: Always call these out prominently, even if the list is empty. Readers scan for this section specifically.
- **Source citations**: Link each change to its source (commit, PR, ticket) so readers can drill down.
- **Ordering within categories**: Most impactful changes first within each category.
- **Upstream validation grade**: When a cited source arrived under a verified ECL envelope, note whether the upstream work was mechanically `validated`, only `self-attested`, or otherwise (`ise.assertion_grade`, ECL v2.0 §6.5) — a reader deciding how much to trust a change benefits from knowing whether it passed spec-mandated gates or was only reported by its own author.

---

*Scribe — Change Narrative Template*
