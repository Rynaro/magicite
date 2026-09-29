---
spec: engram/1.0
name: relation-before-supersedes
id: egr_33333333
version: 1
intent:
  does: "Order and replace exact revisions without selection side effects"
  use_when: "composition must honour before and supersedes semantics"
  not_when: "targets are absent from the selected set"
relations:
  requires: []
  before:
    - id: egr_aaaa0001
      version: 2
    - id: egr_aaaa0002
      version: 1
  supersedes:
    - id: egr_bbbb0001
      version: 4
routing:
  positive:
    - "before supersedes exact revision"
    - "composition ordering fixture"
    - "replacement lineage conflict"
  negative:
    - "learned affinity edge"
  body_digest: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
origin:
  channel: authored
  verification_status: verified
capabilities:
  requires: []
  produces: []
  alternatives: []
  conflicts_with: []
---
## Procedure
1. Treat `before` as ordering among already-selected nodes only.
2. Treat `supersedes` as replacement lineage with pairwise conflict when both revisions are selected.
3. Never rewrite a selected ID from a supersedes edge.

## Pitfalls
- Inferring before/supersedes from learned synapses is forbidden.

## Examples
+ "Exact revision before/supersedes fixtures" → this engram

## Provenance
- S02 shared relation fixture for S08
