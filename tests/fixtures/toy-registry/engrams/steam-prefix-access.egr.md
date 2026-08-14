---
spec: engram/0.2
name: steam-prefix-access
id: egr_f0b27d6a
version: 1
provenance: authored
intent:
  does: "Locate and prepare a Steam Proton compatdata prefix for a given appid"
  use_when: "you need direct filesystem access to a Steam game's Proton prefix"
  not_when: "the game is not a Steam title and has no Proton prefix"
triggers:
  positive:
    - "find steam prefix for appid"
    - "locate compatdata folder"
    - "open proton prefix in file manager"
  negative:
    - "steam is not installed on this machine"
context_affinity: [steam, proton]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
provenance_journal:
  - version: 1
    timestamp: "2026-06-14T00:00:00Z"
    author: "toy-registry-fixture"
    event: authored
    note: "Initial fixture for the M0 walking skeleton"
trust:
  origin: authored
  verification_status: verified
---
## Procedure
1. Find the game's Steam appid via the store page URL or steamapps/appmanifest files.
2. Open steamapps/compatdata/<appid>/pfx in a file manager or terminal.
3. Confirm the prefix exists and Proton has run at least once for that appid.

## Pitfalls
- (×2) Library on a second Steam library folder is easy to miss; check libraryfolders.vdf.

## Examples
+ "where is the wine prefix for my steam game" → full procedure
- "I need a wine prefix for a lutris game" → NOT this engram (route to lutris-wine-prefix-setup)

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
