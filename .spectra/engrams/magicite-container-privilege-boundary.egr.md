---
spec: engram/0.2
name: magicite-container-privilege-boundary
id: egr_4509fb97
version: 1
provenance: authored
intent:
  does: "Diagnose and fix a containerised Magicite server that dies before completing its MCP handshake against a bind-mounted project"
  use_when: "the Magicite container exits or hangs at startup, or throws PermissionError against .spectra, under docker run"
  not_when: "the server boots and answers tools/list but retrieval quality is poor or a model download is attempted"
triggers:
  positive:
  - "magicite container exits before the mcp handshake completes"
  - "permissionerror on .spectra when running magicite in docker"
  - "magicite docker run user flag required"
  - "magicite mcp server never initializes under docker"
  negative:
  - "magicite is downloading a model at runtime"
context_affinity: [magicite, docker, container, mcp, permissions]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [booting-container-server]
composes: []
inhibits: [magicite-offline-embedding-setup]
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Confirm the symptom is pre-handshake: the process dies or hangs before `initialize` returns, so no tool ever runs. If `tools/list` answers, this is the wrong engram.
2. Check the `docker run` invocation for `--user "$(id -u):$(id -g)"`. Its absence is the single most likely cause and it is a hard requirement, not hygiene.
3. Understand why, so the fix is not cargo-culted: `magicite serve` calls `Config.ensure_dirs()` on every boot and creates `.spectra/archive`, `.spectra/approvals`, and `.spectra/runtime` if absent. A bind mount preserves the host's file ownership, so the image's baked-in default UID 10001 cannot write into a normal host-owned project root, and the failure lands before `initialize` can complete.
4. Substitute your real host ids rather than copying `1000:1000` from the README if your user is not UID and GID 1000.
5. Keep the rest of the hardening intact while fixing this — `--cap-drop ALL`, `--security-opt no-new-privileges`, and the `:z` relabel on the bind mount are all part of the documented posture, and dropping them is not a valid workaround for an ownership problem.
6. Confirm the fix mechanically rather than by eye: `tests/acceptance/test_docker_smoke.py` covers both directions, asserting that the uid override preserves host file ownership and that omitting it leaves the server unable to boot at all.

## Pitfalls
- (x1) Reaching for `--privileged` or dropping `--cap-drop ALL` to make the error disappear. That trades a one-flag ownership fix for a materially weaker sandbox and does not address the cause.
- (x1) Running the container as root to sidestep it. The files it then creates in `.spectra` are root-owned on the host, which breaks the next non-root run and every later local `magicite sync`.
- (x1) Reading the pre-handshake death as an embedding or model-download failure. Nothing embedding-related has executed yet at that point.

## Examples
+ "magicite in docker throws PermissionError creating .spectra/runtime" -> steps 2 and 3
+ "our host user is uid 1001 and the README snippet fails" -> step 4
- "magicite answers tools/list but tries to fetch an ONNX model" -> NOT this engram (embedding provider setup)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
