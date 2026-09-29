---
spec: engram/1.0
name: unknown-required-extension
id: egr_deadbeef
version: 1
intent:
  does: "Demonstrate fail-closed unknown required extension"
  use_when: "validating extension activation gates"
  not_when: "extension is optional"
routing:
  positive: ["a", "b", "c"]
  negative: ["d"]
  body_digest: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
origin:
  channel: authored
  verification_status: pending
extensions:
  com.example.unknown_widget:
    required: true
    config:
      mode: strict
---
## Procedure
1. This fixture must fail validation because the required extension is unknown.

## Pitfalls
- Optional unknown extensions must still be preserved losslessly when required is false.

## Examples
+ negative fixture

## Provenance
- S02 AC-S02-03
