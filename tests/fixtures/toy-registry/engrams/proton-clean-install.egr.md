---
spec: engram/0.2
name: proton-clean-install
id: egr_9f0e8e70
version: 1
provenance: authored
intent:
  does: "Wipe and reinstall a broken Proton compatibility tool from scratch"
  use_when: "the Proton installation itself is corrupted, not just a game regression"
  not_when: "only a single game misbehaves and other games run fine on the same Proton build"
triggers:
  positive:
    - "reinstall proton from scratch"
    - "proton is completely broken"
    - "fix corrupted proton install"
  negative:
    - "a single game regressed after an update but proton itself launches other games fine"
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
1. Quit Steam completely and back up any save data outside the Proton prefix.
2. Delete the compatibilitytools.d entry and the affected game's compatdata prefix.
3. Reinstall the stock Proton build from the Steam Play settings.
4. Relaunch the game to force prefix recreation.

## Pitfalls
- (×3) Deleting the prefix loses in-prefix save data; back up first.

## Examples
+ "proton wont launch anything anymore, totally broken" → full procedure
- "just one game stutters since the last update" → NOT this engram (route to proton-ge-proton-downgrade)

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
