---
spec: engram/0.2
name: steam-runtime-repair
id: egr_16145ef8
version: 1
provenance: authored
intent:
  does: "Repair a broken Steam client runtime so Steam itself will launch"
  use_when: "Steam will not open or crashes immediately on start, unrelated to any one game"
  not_when: "steam opens fine and only a specific game fails to launch"
triggers:
  positive:
    - "steam wont open"
    - "steam crashes on launch"
    - "repair steam runtime"
  negative:
    - "only one specific game fails while steam itself opens normally"
context_affinity: [steam]
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
1. Quit every Steam process, including background helpers, via the process manager.
2. Clear the local Steam cache directories (htmlcache, appcache) without touching userdata.
3. Relaunch Steam and let it rebuild its local caches.

## Pitfalls
- (×2) Deleting the userdata directory instead of appcache wipes cloud-unsynced saves.

## Examples
+ "steam.exe crashes right after the splash screen" → full procedure
- "Hades II stutters since GE-Proton 10-9" → NOT this engram (route to proton-ge-proton-downgrade)

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
