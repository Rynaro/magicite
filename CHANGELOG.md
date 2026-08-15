# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/) —
with one deliberate exception noted below: **0.1.0 is not a promise of
stability.** The central design hypothesis this project was built to test
remains unsupported by its own measurements (see "Honest limits" below),
and the pre-registered reversal conditions in `docs/01`'s Falsification
Record could still move shipped defaults. A `1.0.0` would claim a
confidence the evidence does not support, so the version does not move
until that changes.

## [0.1.0] — 2026-08-15

Initial release: a local-first, plasticity-inspired skill router speaking
MCP over stdio, distributed as a self-contained, offline-capable
container.

### Honest limits, up front

This section exists because the alternative — a features list without the
measurements attached — is exactly the failure class this release is
built to avoid. Full detail, methodology, and caveats: `docs/01-vision-
and-hypotheses.md`'s **Falsification Record** and "What the Evidence
Licenses" section.

- **The full pipeline does not beat plain dense embedding on Hit@1.** At
  70 skills / 210 queries, baseline (b) (dense embedding alone) scores
  Hit@1 0.5476; the full Magicite pipeline (baseline d: embedding + graph
  activation + learning) scores 0.5333. The gap is 3 queries out of 210 —
  statistically indistinguishable (prior measurement: an 18-query gap,
  p = 0.00053) — but it is not ahead. **H-BODY-b is falsified as
  implemented.**
- **Authored graph structure (the design's actual central claim —
  spreading activation over declared `needs`/`composes`/`inhibits`
  edges, not re-derived embeddings) has zero supporting measurements and
  two non-supporting ones.** Declared edges are written at `S_edge = 0.0`
  until Dream potentiates them, so on any registry that has not been
  through consolidation the activation graph contains only derived
  `similar_to` kNN edges — re-deriving the cosine signal already scored
  by the embedding arm — and the 11 declared `inhibits` relations in the
  70-engram benchmark registry were measured to have never had any
  production effect. **The design's central claim remains untested as
  designed.**
- **Outcome-gated plasticity degraded held-out routing 3.6× under an
  oracle teacher** (Hit@1 0.4697 → 0.1061), the opposite of the
  prediction. Signal noise was not the binding constraint; the
  score-combination rule is. **H-LEARN is falsified as implemented,
  under uniform demand.**
- **Community-hierarchy rerank contributes nothing measurable at 70
  skills** (ΔHit@3 = 0.0000); the log-decay-flattening claim is
  inconclusive in both directions on the data collected so far.
- **Zero compositional queries were run.** Plan-expansion (H-COMPOSE)
  is untested, not merely unvalidated.

These results come from a single-author benchmark (corpus, queries, and
annotation by one agent, one embedder, uniform learning workload) — see
`docs/01` for exactly what that bias does and does not license. The
0.4619 → 0.5333 improvement recorded in this release is **mechanism
repair** (a declared-edges amendment and an `inhib_gain` recalibration
that fixed defects actively suppressing measurement), not evidence that
the design hypothesis is correct.

### Added

- **16-tool MCP surface** over stdio: `register`, `sync`, `route`,
  `load_skill_body`, `signal_use`, `signal_outcome`, `session_end`,
  `consolidate`, `checkpoint`, `nucleate`, `sharpen`, `promote`,
  `archive`, `flag_dead`, `introspect`, `export`.
- **`.egr.md` — a portable, human-readable, git-diffable skill format**
  (YAML frontmatter + Markdown body), the sole source of truth for the
  registry.
- **A fully rebuildable index.** `skill-graph.db` (SQLite/WAL) is a
  derived cache: delete it and `sync()` reconstructs Tier A/B durable
  state byte-identically from the `.egr.md` files on disk (the rebuild
  invariant, `tests/acceptance/test_rebuild_invariant.py`).
- **Lifecycle governance**: a draft → probation → active/dormant →
  archived state machine, approval-gated promotion/sharpening, an
  append-only `provenance_journal` audit trail per engram, and trust
  metadata for authored/imported/distilled origin.
- **Offline-hardened, self-contained container image.** The `fastembed`
  ONNX embedding model (`BAAI/bge-small-en-v1.5`) is baked in at build
  time; the image completes its MCP handshake and a full
  `register()`/`route()` cycle with `--network none` and no network
  egress at runtime (`tests/acceptance/test_docker_smoke.py`). Follows
  the same hardened, sibling-MCP pattern this project's own `.mcp.json`
  uses for `crystalium`/`atomos`/`atlas-aci`: non-root, capability-
  dropped, digest-pinnable.
- **Three-tier signal ladder** (Tier-0 passive inference, Tier-1
  tool-mediated self-report, Tier-2 host-adapter hook verification, with
  server-side tier assignment) so learning degrades gracefully on hosts
  without hook support, rather than requiring one.
- **`magicite doctor`** diagnostics: filesystem-class warnings for
  `fcntl.flock()`-degraded mounts (NFS/CIFS), and an honest registry-size
  report that never treats crossing a reference skill count as evidence
  of a validated break-even.

### Fixed

- **`provenance_journal` entries appended during a Dream checkpoint or
  archive were computed in memory but never persisted to the `.egr.md`
  file.** `engram/writer.py::render_frontmatter`'s round-trip branch
  refreshed `version`/`plasticity`/`peak_storage_strength`/`synapses` on
  the parsed YAML carrier but never `provenance_journal` — so the
  governance audit trail `docs/06-trust-governance-lifecycle.md` sells as
  the mechanism for autonomous-mutation oversight silently no-opped on
  every checkpoint write. Fixed; regression-proven
  (`tests/acceptance/test_rebuild_invariant.py::
  test_checkpoint_persists_provenance_journal_to_file`, fails against the
  pre-fix code and passes after).

### Known gaps (tracked, not release-blocking)

- Compositional (`needs`/`yields`/`composes`) routing is implemented but
  has zero measured evaluation coverage.
- The declared-edges amendment and `inhib_gain` recalibration repaired
  measurement defects; they did not validate the graph-activation design
  hypothesis, which remains the pre-registered reversal condition to
  watch across future releases.
