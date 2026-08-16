---
spec: engram/0.2
name: magicite-offline-embedding-setup
id: egr_90fe52ff
version: 1
provenance: authored
intent:
  does: "Choose and provision a Magicite embedding provider so registration and routing work with no network egress"
  use_when: "Magicite tries to download an ONNX model at runtime, or CI needs a deterministic embedder with no model fetch"
  not_when: "the server never reaches embedding at all because it dies at boot with a PermissionError against the project root"
triggers:
  positive:
    - "magicite is downloading a model at runtime"
    - "run magicite embeddings offline with no network"
    - "deterministic embedding provider for magicite in CI"
    - "magicite fetch-model bge-small-en-v1.5"
  negative:
    - "magicite container exits before the mcp handshake completes"
context_affinity: [magicite, embeddings, offline, ci]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [usable-embedding-provider]
composes: []
inhibits: []
provenance_journal:
  - version: 1
    timestamp: "2026-08-15T00:00:00Z"
    author: "claude-orchestrator"
    event: authored
    note: "First-party dogfood registry (change magicite-dogfoods-itself, AC-D1)"
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Decide which provider the environment needs. `fastembed` with `BAAI/bge-small-en-v1.5` is the production default and the one every published measurement in `docs/01` was taken against; `hashing` is deterministic and download-free but has materially lower routing quality; `ollama` targets a local daemon.
2. For a development machine, pre-download the model once with `uv run magicite fetch-model`. This is the step that makes every later run offline-capable.
3. Set `MAGICITE_EMBEDDING_OFFLINE=1` to make the runtime refuse any network fetch after the model is local. This converts a silent slow download into a loud failure, which is what you want in a sealed environment.
4. For CI and unit tests, set `MAGICITE_EMBEDDING_PROVIDER=hashing`. It is deterministic, needs no model, and keeps the suite hermetic — but never report retrieval numbers taken under it as if they were production numbers.
5. For the container, do nothing: the ONNX model is baked into the image at build time, which is why the image completes its MCP handshake with zero network access. That property is covered by `tests/acceptance/test_docker_smoke.py`, including a `--network none` register and route cycle.
6. Verify with `uv run magicite doctor --project-root .`, which reports the resolved provider and flags an environment that would need a fetch.

## Pitfalls
- (x1) Assuming the hashing provider is a drop-in equivalent. It is a test fixture, not a production embedder, and comparing routing quality across providers without saying which one ran is how a measurement becomes meaningless.
- (x1) Setting `MAGICITE_EMBEDDING_OFFLINE=1` before ever running `fetch-model`. The refusal is categorical, so the first registration fails rather than falling back to a download.
- (x1) Diagnosing a boot-time `PermissionError` as an embedding problem. If the server dies before the handshake, the embedder never ran — check the container privilege boundary first.

## Examples
+ "our build box has no internet and magicite hangs on first register" -> steps 2 through 4
+ "which embedder produced the docs/01 numbers" -> step 1
- "magicite container will not start at all under docker run" -> NOT this engram (privilege boundary, not embeddings)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the dogfood registry
