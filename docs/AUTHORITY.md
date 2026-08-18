# Magicite 0.3 authority manifest

This manifest is the entry point for deciding what Magicite 0.3 means. It
exists because the 0.2 audit found live design prose, archived construction
records, and runtime behavior competing as if they had equal authority.

## Authority order

1. The 0.3 acceptance criteria and accepted amendments under
   `.spectra/plans/magicite-v0.3.0-integrity-recovery*`.
2. Public MCP schemas, engram schemas, configuration defaults, and tests in
   the tagged release.
3. Current documents `02` through `07`, `operations.md`, and adapter guides.
4. `01-vision-and-hypotheses.md` for claims and falsification status only.
5. Archived `.spectra/changes/archive/` records as immutable historical
   evidence. Archives do not override a later accepted criterion.
6. `docs/research/exploratory/` as research context, never current behavior.

When two current sources disagree, the release is blocked until the mismatch
is resolved or recorded as a scoped erratum. Code passing a test is not by
itself permission to silently change a frozen acceptance criterion.

## 0.3 semantic decisions

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

