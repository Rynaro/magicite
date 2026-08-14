---
spec: engram/0.2
name: proton-ge-proton-downgrade
id: egr_b5320dfd
version: 1
provenance: authored
intent:
  does: "Downgrade GE-Proton when a Steam game regresses after an update"
  use_when: "game crashes or performs worse immediately after GE-Proton update"
  not_when: "game never worked on any version of Proton"
triggers:
  positive:
    - "game X broke after proton update"
    - "rollback ge-proton for steam"
    - "new proton version regression"
  negative:
    - "proton not launching at all"
context_affinity: [steam, proton]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [steam-prefix-access]
inhibits: [proton-clean-install]
provenance_journal:
  - version: 1
    timestamp: "2026-06-14T00:00:00Z"
    author: "toy-registry-fixture"
    event: authored
    note: "Initial fixture for the M0 walking skeleton (AC-006)"
trust:
  origin: authored
  verification_status: verified
---
## Procedure
1. Identify the game's Steam appid and the compatdata prefix (see steam-prefix-access).
2. Download the target GE-Proton build into compatibilitytools.d.
3. Pin the version per-game via Steam launch options, not globally.
4. Restart Steam and verify the game now launches with the older build.

## Pitfalls
- (×4) Global version pinning breaks sibling games — always pin per-appid.
- (×2) NTFS-mounted libraries fail silently; check the filesystem first.

## Examples
+ "Hades II stutters since GE-Proton 10-9" → full procedure
- "Steam won't open" → NOT this engram (route to steam-runtime-repair)

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
