---
spec: engram/0.2
name: nvidia-prime-render-offload
id: egr_8d12f6f0
version: 1
provenance: authored
intent:
  does: "Force a game to run on the discrete NVIDIA GPU via PRIME offload"
  use_when: "a hybrid-GPU laptop runs a game on the integrated GPU by default"
  not_when: "the machine has only one GPU"
triggers:
  positive:
    - "force game onto nvidia gpu"
    - "prime offload not using dgpu"
    - "enable dgpu for game launch"
  negative:
    - "the laptop has no discrete gpu at all"
context_affinity: [nvidia, gpu]
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
1. Confirm the NVIDIA driver and prime-run (or similar offload helper) are installed.
2. Set the game's Steam launch options to prefix the command with the offload helper.
3. Launch the game and confirm via nvidia-smi that the process is using the discrete GPU.

## Pitfalls
- (×1) Wayland sessions may need an extra DRI_PRIME environment variable alongside prime-run.

## Examples
+ "my laptop game is running on the integrated graphics not the nvidia card" → full procedure
- "single-gpu desktop, game runs slow" → NOT this engram

## Provenance
- v1 2026-06-14 · authored by toy-registry-fixture
