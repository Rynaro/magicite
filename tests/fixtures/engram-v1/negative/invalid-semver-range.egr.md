---
spec: engram/1.0
name: invalid-semver-range
id: egr_cafebabe
version: 1
intent:
  does: "Demonstrate fail-closed invalid semver range"
  use_when: "validating version constraint grammar"
  not_when: "ranges use only V1-supported comparators"
compatibility:
  languages:
    python:
      scheme: semver
      range: "^1.2.3"
routing:
  positive: ["a", "b", "c"]
  negative: ["d"]
  body_digest: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
origin:
  channel: authored
---
## Procedure
1. Reject caret ranges.

## Pitfalls
- V1 excludes ^, ~, wildcard, and OR.

## Examples
+ negative fixture

## Provenance
- S02 AC-S02-03
