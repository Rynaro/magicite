---
spec: engram/0.2
name: magicite-embedding-provider-contract
id: egr_1f4796d0
version: 1
provenance: authored
intent:
  does: "Implement or change a Magicite embedding provider against the Embedder protocol without breaking the router's cosine assumptions"
  use_when: "adding a new embedding backend, changing an existing provider, or cosine scores look wrong across the whole registry"
  not_when: "the provider is fine and the question is how to provision or pre-download a model in a sealed environment"
triggers:
  positive:
  - "add a new embedding provider to magicite"
  - "magicite Embedder protocol model_name dim embed_batch"
  - "magicite cosine scores look wrong across every candidate"
  - "must magicite embeddings be l2 normalised"
  negative:
  - "how do I pre-download the magicite embedding model offline"
context_affinity: [magicite, embeddings, architecture, numerics]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: [magicite-offline-embedding-setup]
yields: [conforming-embedder]
composes: []
inhibits: []
provenance_journal:
- version: 1
  timestamp: '2026-08-15T00:00:00Z'
  author: claude-orchestrator
  event: authored
  note: Codebase tranche (change magicite-codebase-skill-tranche, AC-T1)
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Implement the protocol in `embeddings/base.py` exactly: two attributes, `model_name` and `dim`, and two methods, `embed` for one string and `embed_batch` for many. It is a runtime-checkable protocol, so structural conformance is what matters, not inheritance.
2. Return L2-normalised float32 vectors of the declared dimension, every time. The router's cosine step relies on this so it never has to know which provider produced a given stored vector, and an unnormalised provider makes cosine scores incomparable across the registry rather than merely wrong for one row.
3. Keep the module framework-free. No provider may import the MCP layer; providers are plain computation.
4. Set `model_name` to something stable and distinguishing, because it is stored alongside each embedding row and is how staleness is detected. Two different models sharing a name is a silent corruption, not a cosmetic problem.
5. Study the three existing providers before writing a fourth: the ONNX default that every published measurement was taken against, the local-daemon option, and the deterministic hashing provider that needs no download and backs the test suite.
6. Never report retrieval numbers taken under the hashing provider as production numbers. It is a hermetic test fixture with materially lower quality, and comparing across providers without naming them is how a measurement becomes meaningless.
7. Re-embed after changing a provider or a model. Stored vectors are only comparable to vectors from the same model, so a provider swap invalidates the index and requires a rebuild rather than a restart.
8. Implement `embed_batch` as a real batch where the backend supports it. It is the path registration and rebuild use, and a naive loop turns a rebuild into a per-engram round trip.

## Pitfalls
- (x1) Returning unnormalised vectors and debugging the router. Every cosine in the system assumes normalisation was done by the provider.
- (x1) Mixing vectors from two models in one index by reusing a model name, which produces plausible-looking nonsense rather than an error.
- (x1) Changing the provider without rebuilding the index, leaving stale vectors that are silently incomparable to fresh queries.
- (x1) Returning float64. The stored contract is float32, and widening it wastes space in every row for no retrieval benefit.

## Examples
+ "I wrote a provider and everything ranks identically" -> check normalisation and dimension, step 2
+ "can I swap providers on a live registry" -> only with a rebuild, step 7
- "our CI box cannot download the model" -> NOT this engram (that is provisioning)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
