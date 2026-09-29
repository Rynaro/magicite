---
spec: engram/1.0
name: sample-host-tooling
id: egr_a1b2c3d4
version: 2
intent:
  does: "Prepare a host toolchain for Proton prefix repair"
  use_when: "a declared host capability and artifact producer are required"
  not_when: "the host cannot disclose capabilities"
compatibility:
  os: [linux]
  languages:
    python:
      scheme: pep440
      range: ">=3.11,<4"
  hosts:
    - id: cursor
      version:
        scheme: semver
        range: ">=0.40.0,<1.0.0"
capabilities:
  requires:
    - kind: host
      id: "host.fs.read-project"
      version:
        scheme: semver
        range: ">=1.0.0"
    - kind: artifact
      id: "artifact.wine-prefix"
      version:
        scheme: semver
        range: ">=1.0.0"
  produces:
    - kind: artifact
      id: "artifact.wine-prefix-report"
      version: "1.0.0"
  alternatives: []
  conflicts_with: []
relations:
  requires:
    - id: egr_b5320dfd
      version: 1
  before:
    - id: egr_aaaa0001
      version: 2
    - id: egr_aaaa0002
      version: 1
  supersedes:
    - id: egr_bbbb0001
      version: 4
risk:
  filesystem: read-project
  subprocess:
    mode: declared-tools
    tools: [protontricks]
  network:
    mode: none
    destinations: []
  secrets: none
routing:
  positive:
    - "prepare proton tooling"
    - "host capability wine prefix"
    - "artifact producer report"
  negative:
    - "windows-only host"
  body_digest: "016217582883ef9a02208b342ab53143268111be43d9899dd2ed105128312a99"
origin:
  channel: authored
  verification_status: verified
  content_hashes:
    body_sha256: "016217582883ef9a02208b342ab53143268111be43d9899dd2ed105128312a99"
assets: {}
extensions: {}
---
## Procedure
1. Confirm the host exposes `host.fs.read-project`.
2. Require exact revision `egr_b5320dfd@version=1` before mutating the prefix.
3. Emit `artifact.wine-prefix-report@1.0.0`.

## Pitfalls
- Do not invent learned before/supersedes edges from affinity scores.

## Examples
+ "Need wine prefix tooling with typed host requires" → this engram
- "Just ranking similar skills" → NOT this engram

## Provenance
- v2 authored for S02 relation fixtures
