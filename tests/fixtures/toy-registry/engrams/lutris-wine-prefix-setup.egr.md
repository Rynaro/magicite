---
spec: engram/0.2
name: lutris-wine-prefix-setup
id: egr_0f9dcd1a
version: 1
provenance: authored
intent:
  does: "Create a fresh Wine prefix for a non-Steam game under Lutris"
  use_when: "installing a game through Lutris rather than Steam"
  not_when: "the game is a native Steam Proton title"
triggers:
  positive:
    - "create lutris wine prefix"
    - "set up wine prefix for lutris"
    - "install game via lutris installer"
  negative:
    - "the game is launched through steam, not lutris"
context_affinity: [lutris, wine]
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
1. Create a new Lutris install and choose "Create prefix" instead of an existing one.
2. Pick the Wine/Proton runner version Lutris recommends for the title.
3. Run winetricks inside the new prefix for any dependencies the game needs.

## Pitfalls
- (×2) Reusing an old prefix from a different game causes DLL version conflicts.

## Examples
+ "installing a GOG game through lutris, need a wine prefix" → full procedure
- "where is my steam proton prefix" → NOT this engram (route to steam-prefix-access)

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
