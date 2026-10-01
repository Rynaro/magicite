# Magicite v1 integration authority

The installed package remains 0.3.1. This integration is not a published v1
release or a declaration that every release gate has passed.

## Authority order

1. Frozen [v1 acceptance criteria](../.spectra/plans/magicite-v1/acceptance.md),
   contracts, evaluation and release gates, plus explicitly accepted amendments.
2. Installed runtime schemas and tests, with the [generated reference](generated/runtime-reference.json)
   checked against actual version, engram schemas, 16 MCP tools, CLI and config.
3. Current documents `02` through `07`, operations and adapter guides. The
   current v1 contract at each document's start supersedes its historical context.
4. Historical v0.3 results and append-only archives, retained as evidence rather
   than proof of v1 performance or efficacy.
5. Exploratory research and unaccepted proposals, which cannot change behavior.

The stable default is `dense-v1`; see [ADR 001](decisions/001-stable-dense-v1-incumbent.md).
Adaptive selection is experimental. S10 efficacy is held; its containment is mandatory.
A passing fixture establishes only that fixture's obligation. Official external data,
production envelopes, real-host transcripts, fuzz evidence and independent operator
validation remain separately [UNEVALUATED](evaluation/v1/unevaluated.md).

`skill-graph.db` is local, rebuildable state and is ignored by default. Complete
recovery also requires canonical bodies/assets, approvals, policy/trust state,
evidence checkpoints and separately protected fingerprint-key custody; a copied DB
is not a complete backup. Authenticated trust history is held by a separately
provisioned custodian under the adopted trust-hardening amendment; the project-local
journal is a verified projection, not an authority. Separate-UID Linux/macOS
deployment qualification remains UNEVALUATED until actually run.

## Historical 0.3 semantic decisions (superseded where v1 differs)

- FastEmbed is the default production provider; hashing is the deterministic
  CI provider and Ollama is optional.
- Source use is offline by default. `magicite fetch-model` is the explicit
  network-bearing acquisition step; the container bakes the model at build
  time and remains offline at runtime.
- Engram IDs are immutable identity/routing hashes. Whole-file drift is tracked
  by a separate content digest.
- The canonical routing view is versioned and consists of intent, positive
  triggers, and procedure text. Contraindications (`not_when` and negative
  triggers) use a separate representation and score contribution. Pitfalls and
  examples are not silently claimed as routing inputs.
- `yields` is portable composition metadata in 0.3; it is not a graph edge
  until a future governed semantics defines its producer/consumer behavior.
- `skill-graph.db` is local, rebuildable state and is ignored by default. The
  `.egr.md` registry and durable approval mirrors are the portable authority.
- Lifecycle status, verification status, and operation execution status are
  independent dimensions. The word `pending` must always name its dimension.
- Register, sync, sharpen, lifecycle operations, and Dream may write durable
  state under the single-writer protocol. Dream alone performs consolidation
  and learned-state checkpointing; it is not the only legitimate file writer.
- Baseline-(c) shares production seed selection and declared-inhibition
  semantics. Results derived from the 0.2 divergent evaluator are superseded,
  not erased.
- Version 0.3 has no native C routing implementation. Index reuse and batched
  native-library operations must be exhausted before reconsideration.

