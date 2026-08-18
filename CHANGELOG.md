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

## [Unreleased]

## [0.3.1] — 2026-08-18

### Changed

- SKILL.md import/export now preserves arbitrary Markdown bodies and host-specific
  YAML frontmatter, recognizes colonless `Use when` / `NOT for` routing
  markers, and fails before writing when a preserved body would be exported
  lossily.

## [0.3.0] — 2026-08-18

The integrity-recovery release closes the runtime, evaluation, interface, and
verification drifts identified by the full 0.2 audit. It retains Python as the
single routing semantic reference; native C remains deferred until a measured
production hotspot can justify the extra implementation and integrity risk.

### Added

- Resumable, fenced Dream checkpoints and crash-recovery coverage.
- Process-level lease race and stale-writer verification.
- Recoverable idempotency reservations spanning effects, event commit, and
  response persistence.
- Auditable approval decisions and resume transitions.
- An independently authored composition corpus, superseding evaluation
  results, executable documentation contracts, and a 1k/10k benchmark matrix.

### Changed

- Routing uses a versioned canonical view, separate contraindication vectors,
  shared production/evaluation activation primitives, and bounded exact cache
  reuse.
- Embedding operation is offline by default and live introspection reports the
  current lifecycle, verification, execution, and learned-state dimensions.
- CI and release inputs are immutable; the release image is signed and
  accompanied by SBOM and provenance attestations.

## [0.3.0rc1] — 2026-08-18

Integrity-recovery release candidate. The final 0.3.0 tag remains blocked on
independent review and the acceptance gates in the governed recovery plan.

### Added

- Recoverable durable-write checkpoints, lease fencing, fail-closed registry
  reconciliation, migration rollback safety, and idempotency recovery.
- A distinct contraindication routing view, production/baseline seed parity,
  truthful live introspection, and resumable skill-body pagination.
- `docs/AUTHORITY.md`, archive redirects, prospective agent provenance, and
  an explicit 0.3 release-gate record.
- Immutable GitHub Action SHAs and digest-pinned Python/uv container inputs.

### Changed

- Source operation is offline by default; model acquisition is explicit.
- The route/evaluation contract supersedes 0.2 baseline-(c) measurements that
  used a different seed rule and omitted documented inhibition.
- Package metadata no longer embeds the image digest produced after the
  package itself is built; the signed release record is the digest authority.
- Performance work uses generation-keyed index reuse and differential tests.
  No native C implementation is included.

### Append-only errata for 0.1/0.2

- The frozen acceptance categories total 33 (19 event-driven, 3 state-driven,
  5 ubiquitous, 6 unwanted-behavior), plus 9 addendum criteria; the prior
  chronicle total of 35 was an accounting error.
- The runtime lifecycle is `nascent → probation → consolidated → promoted →
  archived`; `active` and `dormant` in the 0.1 prose were stale design names.
- Declared edges use the accepted effective-strength floor; the 0.1 paragraph
  saying they remained at zero effect is superseded.
- The `no_tag_capture` ablation is invalid and supports no Principle 0 claim.
  Prior evidence is preserved; corrected results must be published separately.

## [0.2.0] — 2026-08-16

### Changed — BREAKING (data layout)

- **Magicite's project state moved from `.spectra/` to `.magicite/`.**
  `.spectra/` belongs to ESL/tonberry, which owns `.spectra/changes/`; the two
  tenants collided confusingly (Magicite's `.spectra/archive/` — decayed
  engrams — sat one level from ESL's `.spectra/changes/archive/` — archived
  change records). The ecosystem convention is one dot-directory per tool
  (`.atlas/`, `.eidolons/`), and Magicite now follows it. `engrams/`,
  `archive/`, `approvals/`, `runtime/`, `magicite.toml`, and `bench/` all live
  under `.magicite/`. `.spectra/changes/` is untouched.

  **Existing projects keep working without intervention.** Path resolution
  falls back to `.spectra/` when `.magicite/` is absent *and* a real
  `.spectra/engrams/` exists — a fresh project carrying only an ESL
  `.spectra/changes/` tree is never dragged onto the old layout. `magicite
  doctor` reports the fallback as a deprecation warning rather than staying
  quiet; a silent fallback against an empty registry is the worst failure mode
  a router has. The fallback is scheduled for removal in a future minor
  version. Migration steps: `docs/operations.md` §15.

### Added

- `MAGICITE_DATA_DIR` overrides the data directory name, beating both the
  default and the legacy fallback. Environment only — `magicite.toml` lives
  inside the directory it would be naming.
- `magicite doctor` reports a `layout` section (`data_dir`, `legacy`,
  `expected`) and warns on the deprecated location.
- A first-party engram registry for this repository itself (30 engrams,
  `.magicite/engrams/`) plus the dogfood harness under `scripts/`. See
  `docs/adapters/dogfooding.md`, including what dogfooding exposed: notably
  that `triggers.negative` and `intent.not_when` have **no effect on
  retrieval** in 0.1.0 — they are linted, fitness-scored, stored, and hashed
  into the engram id, and consulted by nothing at query time.

### Unchanged

- The 16-tool MCP surface, routing behaviour, plasticity, and the trust gate.
  This release moves files and resolves paths; it changes no decision the
  router or the Dream worker makes. No claim in `docs/01`'s Falsification
  Record is affected.

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
