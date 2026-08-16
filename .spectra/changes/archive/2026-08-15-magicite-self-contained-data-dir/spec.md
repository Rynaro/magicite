# magicite-self-contained-data-dir (tier: full)

## GIVEN / WHEN / THEN

**GIVEN** Magicite writes all of its own state into `<project_root>/.spectra/`
— `engrams/`, `archive/`, `approvals/`, `runtime/`, `magicite.toml`, and
`bench/queries.jsonl` — a directory that belongs to a *different* tool: ESL /
tonberry owns `.spectra/changes/`, and this repository's own tree shows both
tenants side by side, with Magicite's `.spectra/archive/` (decayed engrams)
sitting one level from ESL's `.spectra/changes/archive/` (archived change
records), which are unrelated things with near-identical paths,

**AND GIVEN** the surrounding ecosystem already establishes the opposite
convention — `atlas-aci` uses `.atlas/`, Eidolons uses `.eidolons/`, each tool
in its own namespaced dot-directory — so a standalone Magicite user who has
never heard of SPECTRA still gets a directory named after someone else's tool,

**WHEN** Magicite resolves its data paths,

**THEN**:

1. All Magicite-owned state SHALL live under a single self-contained
   `<project_root>/.magicite/` directory, matching the ecosystem's
   one-tool-one-dot-directory convention.
2. `.spectra/changes/` SHALL be left entirely alone — it is ESL's, and this
   change does not touch it.
3. An existing project whose registry lives at `.spectra/engrams/` SHALL keep
   working without intervention, and SHALL be told, loudly, that its layout is
   legacy.
4. The data directory SHALL be overridable, so a host that needs a different
   location is not forced to fork.
5. No behaviour other than path resolution SHALL change: the 16-tool surface,
   routing, plasticity, and the trust gate are untouched.

## Tradeoff of record

**Clean break, or read both layouts?**

- *Clean break* (move the default, fail loudly on the old layout) is the
  smallest amount of code and leaves exactly one path to reason about. Its
  cost lands entirely on existing projects: a `magicite serve` against a
  project with `.spectra/engrams/` would come up with an **empty registry** —
  not an error, just silence, because an absent registry directory is a legal
  state for a fresh project. Silent zero-skill routing is the worst possible
  failure mode for a router.
- *Read both* keeps a legacy branch alive in path resolution forever, which is
  real complexity in the one module that must stay simple.

**Decision: read both, with a loud deprecation.** Resolution prefers
`.magicite/`; it falls back to `.spectra/` only when `.magicite/` is absent
**and** a legacy `.spectra/engrams/` actually exists, so a genuinely fresh
project can never be dragged onto the old layout by accident. The fallback
announces itself through `magicite doctor` and a startup warning rather than
happening quietly. The complexity is bounded to one resolution function with
one documented branch, and v0.1.0's installed base is small enough that this
branch can be dropped at the next minor version — but "small" is not "zero",
and the failure mode being defended against is silent, not noisy.

**Why `.magicite/` and not, say, `.skills/`:** the name has to be unambiguous
in a project that may host several of these tools at once. This repository
already carries `.atlas/`, `.eidolons/`, and `.spectra/`; a generic name would
recreate exactly the collision this change exists to remove.

## Acceptance checks

- **AC-M1** GIVEN a project with no Magicite state, WHEN `Config` resolves its
  paths, THEN `registry_dir`, `archive_dir`, `approvals_dir`, `runtime_dir`,
  `toml_path`, and the bench query path SHALL all resolve under
  `<project_root>/.magicite/`, and `ensure_dirs()` SHALL create them there.
  `verify_method`: `pytest tests/unit/test_config.py -k data_dir`

- **AC-M2** GIVEN a project whose only Magicite state is a legacy
  `.spectra/engrams/` directory, WHEN `Config.load()` resolves, THEN it SHALL
  resolve to the legacy `.spectra/` tree so the registry keeps working, and
  SHALL report that it did so.
  `verify_method`: `pytest tests/unit/test_config.py -k legacy`

- **AC-M3** GIVEN a project with **no** Magicite state at all, WHEN
  `Config.load()` resolves, THEN it SHALL choose `.magicite/` — a fresh
  project SHALL NOT be dragged onto the legacy layout by the mere presence of
  an ESL `.spectra/changes/` directory.
  `verify_method`: `pytest tests/unit/test_config.py -k fresh_project_ignores_esl`

- **AC-M4** GIVEN `MAGICITE_DATA_DIR` is set, WHEN `Config.load()` resolves,
  THEN that value SHALL win over both the default and the legacy fallback.
  `verify_method`: `pytest tests/unit/test_config.py -k env_override`

- **AC-M5** GIVEN a project on the legacy layout, WHEN `magicite doctor` runs,
  THEN it SHALL emit a distinct, actionable deprecation finding naming both
  the current and the intended directory.
  `verify_method`: `pytest tests/unit/obs/test_doctor.py -k legacy`

- **AC-M6** GIVEN this repository's own registry moved to `.magicite/engrams/`,
  WHEN the index is rebuilt and the dogfood guards run, THEN sync SHALL report
  30 engrams with zero validation errors and zero dangling edges, and both
  `dogfood_graph_check.py` checks SHALL pass.
  `verify_method`: `uv run magicite sync --project-root . && uv run python scripts/dogfood_graph_check.py`

- **AC-M7** GIVEN all changes, WHEN the frozen verify command runs, THEN
  `ruff check .`, `mypy src`, `pytest --cov-fail-under=70`, and
  `pytest -m acceptance` SHALL pass and `magicite tools` SHALL report exactly
  16 tools, with no assertion weakened to accommodate the move.
  `verify_method`: the frozen verify command

- **AC-M8** GIVEN the docs, container files, and CI, WHEN read, THEN no
  user-facing instruction SHALL still tell a reader to use `.spectra/` for
  Magicite state, and the `.dockerignore` SHALL exclude both directories from
  the build context.
  `verify_method`: `grep` sweep recorded in the verification note

## Scope

`src/magicite/config.py` (path resolution + the amendment record),
`src/magicite/obs/doctor.py` (the deprecation finding),
`src/magicite/eval/bench.py` (the bench query path), docstrings in
`core/{registry,decay,approvals}.py` and `mcp/bind_lifecycle.py`, the test
suite, `tests/conftest.py`, `Dockerfile`, `.dockerignore`, `.gitignore`,
`.github/workflows/ci.yml`, `README.md`, `docs/**`, `scripts/dogfood_*.py`,
and the engrams whose prose names the old path. The repository's own registry
moves via `git mv`.

Out of scope: `.spectra/changes/` and anything else ESL owns; the 16-tool MCP
surface; routing, plasticity, and trust behaviour. This change moves files and
resolves paths — it changes no decision the router or the Dream worker makes.
