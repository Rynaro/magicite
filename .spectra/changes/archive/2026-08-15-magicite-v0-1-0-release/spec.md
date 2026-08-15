# magicite-v0-1-0-release (tier: lite)

## GIVEN / WHEN / THEN

**GIVEN** the `magicite-v1-implementation` drift-fix pass (archived,
verified) left one defect deliberately out of scope: `engram/writer.py::
render_frontmatter`'s round-trip branch refreshes `version`/`plasticity`/
`peak_storage_strength`/`synapses` on the ruamel carrier document but never
`provenance_journal` -- so every Dream-checkpoint-appended entry
(`event: consolidated`, `archived`, ...) is computed in memory
(`core/dream.py::_phase7_checkpoint`, `core/decay.py`'s archive path) but
never persisted to the `.egr.md` file, even on a dirty checkpoint write --
and the v0.1.0 release is container-first, with `CHANGELOG.md`,
`.github/workflows/release.yml`'s never-executed container path, and a
genuinely offline image as the remaining release-blocking work,

**WHEN** a registered engram accrues a new `provenance_journal` entry
during a Dream checkpoint or archive, and separately, when the v0.1.0
container is built and run with `--network none`,

**THEN**:
1. The appended `provenance_journal` entry SHALL be persisted onto the
   `.egr.md` file's own bytes on disk, not just held on the in-memory
   `Engram` object -- readable back by a fresh `parser.parse_file()` of
   the same file.
2. The MCP stdio handshake and a real `register()`/`route()` cycle SHALL
   complete inside the container with no network egress, using the baked
   embedding model.
3. `CHANGELOG.md` (Keep a Changelog, `0.1.0`) SHALL state the honest
   claim scope: the full pipeline does not beat plain dense embedding on
   Hit@1 (0.5333 vs 0.5476, a 3-query gap in 210, statistically
   indistinguishable but not ahead), authored graph structure has zero
   supporting measurements and two non-supporting ones, and the design's
   central claim remains untested as designed (pointing at docs/01's
   Falsification Record) -- alongside the real capability surface (16-tool
   MCP surface, portable `.egr.md` format, rebuildable index, lifecycle
   governance, offline hardened container).
4. `.github/workflows/release.yml`'s container job SHALL be reviewed for
   anything likely to fail on its first (never-executed) run: image
   build, GHCR push, multi-arch build, cosign signing, SBOM/provenance
   attestation, Trivy gate, and any secret/permission dependency.

## Acceptance checks

- **AC-1** GIVEN a registered engram with an existing `provenance_journal`
  entry, WHEN a Dream checkpoint (or decay archive) appends a new entry to
  the in-memory frontmatter and writes through `render_document`'s
  round-trip branch, THEN the new entry's `event`/`author`/`timestamp`
  SHALL appear in the raw bytes of the `.egr.md` file on disk and SHALL
  be readable back by a fresh parse.
  `verify_method`: `pytest tests/acceptance/test_rebuild_invariant.py::test_checkpoint_persists_provenance_journal_to_file`
  -- proven to fail at `dfe460c` (pristine worktree) and pass after the fix.

- **AC-2** GIVEN the fix, WHEN the full suite runs, THEN
  `ruff check . && mypy src`, `pytest --cov=src/magicite --cov-fail-under=70`,
  and `pytest -m acceptance` SHALL all pass, and `magicite tools` SHALL
  still report exactly 16 tools, with no weakened assertion anywhere.

- **AC-3** GIVEN the v0.1.0 Docker image built from `Dockerfile`, WHEN run
  with `docker run --network none`, THEN the MCP stdio handshake and a
  `register()`/`route()` cycle SHALL complete using the baked embedding
  model with no network access.

- **AC-4** GIVEN `.github/workflows/release.yml`'s container job, WHEN
  reviewed line-by-line, THEN any step likely to fail on a first,
  never-executed run (missing secret, missing permission, wrong trigger
  condition, broken multi-arch/signing/SBOM/Trivy wiring) SHALL be
  identified and reported to the human operator (not silently patched
  around without disclosure).

- **AC-5** GIVEN `CHANGELOG.md`, WHEN read, THEN it SHALL state the Hit@1
  falsification numbers (0.5333 vs 0.5476, a 3-query gap in 210) and the
  zero-supporting/two-non-supporting authored-graph-structure claim
  without overstating results, pointing at docs/01's Falsification
  Record, and SHALL NOT bump the version past `0.1.0`.

## Scope

`src/magicite/*`, `tests/*`, `pyproject.toml`, `uv.lock`, `Dockerfile*`,
`.dockerignore`, `.gitignore`, `.github/workflows/*`, `README.md`,
`CHANGELOG.md`, `LICENSE`, `docs/adapters/*`, `docs/operations.md`. Never
`.spectra/changes/magicite-v1-implementation/**`, `docs/README.md`,
`docs/0[1-7]-*.md`, `docs/research/**`, `.eidolons/**`, `EIDOLONS.md`,
`AGENTS.md`, `CLAUDE.md`. No version bump; no commit; no push.
