---
spec: engram/0.2
name: proton-verify-installation
id: egr_4a57b485
version: 1
provenance: authored
intent:
  does: "Verify a Proton installation is healthy after install or downgrade"
  use_when: "you want to confirm GE-Proton is correctly wired to a game after changing versions"
  not_when: "no install or version change has happened recently"
triggers:
  positive:
    - "verify proton install worked"
    - "check proton log after launch"
    - "confirm ge-proton version active"
  negative:
    - "the game has never been launched even once"
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
1. Set PROTON_LOG=1 in the game's Steam launch options.
2. Launch the game and reproduce the scenario you want to verify.
3. Inspect the log tail under steamapps/compatdata/<appid>/pfx and confirm the expected version string.

## Pitfalls
- (×1) The log file only appears after the first launch attempt with PROTON_LOG=1 set.

## Examples
+ "did the ge-proton downgrade actually take effect" → full procedure
- "how do I install proton" → NOT this engram (route to proton-clean-install)

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
