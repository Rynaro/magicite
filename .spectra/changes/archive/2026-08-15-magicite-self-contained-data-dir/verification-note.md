# Verification note — magicite-self-contained-data-dir

Checker: `magicite-self-verify`. The oracles are Magicite's own surface (sync,
doctor, the 16-tool session), the frozen verify command, the dogfood guards,
and the container smoke suite run against a **freshly built** image.

## AC-M1 — fresh project resolves and creates under `.magicite/` — **PASS**

`tests/unit/test_config.py::test_data_dir_defaults_to_dot_magicite` and
`::test_data_dir_ensure_dirs_creates_under_magicite`. Every derived path —
`registry_dir`, `archive_dir`, `approvals_dir`, `runtime_dir`, `toml_path`,
`bench_queries_path`, `db_path`, `dream_lock_path` — is asserted individually,
and `ensure_dirs()` is asserted to create **nothing** under `.spectra/`.

## AC-M2 — legacy registry still found — **PASS**

`::test_legacy_registry_is_still_found`. A project whose only Magicite state is
`.spectra/engrams/` resolves to the legacy tree and reports
`uses_legacy_layout == True`.

This is the check that justifies the tradeoff. A clean break would not have
errored on such a project — an absent registry directory is a legal state for a
fresh project — it would have come up with an empty registry and routed against
zero skills.

## AC-M3 — ESL's directory never triggers the fallback — **PASS**

`::test_fresh_project_ignores_esl_changes_dir`. A project with
`.spectra/changes/` and no Magicite state resolves to `.magicite/`. The
fallback keys on `.spectra/engrams/` specifically, so every ESL-using project
in this ecosystem — which is all of them — lands on the current layout.

`::test_new_layout_wins_when_both_exist` additionally pins that a
half-migrated project prefers the new tree rather than flip-flopping.

## AC-M4 — `MAGICITE_DATA_DIR` wins — **PASS**

`::test_env_override_beats_default_and_legacy`, including that the override is
honoured even when a legacy tree exists, and
`::test_blank_env_override_is_ignored` (whitespace is an unset variable, not a
request for `""`).

`::test_toml_cannot_relocate_its_own_directory` pins the circularity guard:
`magicite.toml` lives inside the directory it would be naming, so
`data_dir_name` is excluded from TOML application — while an ordinary field
from the same file still applies, proving the exclusion is targeted rather than
the TOML being ignored.

## AC-M5 — doctor reports the deprecation — **PASS**

`tests/unit/obs/test_doctor.py::test_layout_check_flags_the_legacy_directory`
and `::test_layout_check_is_quiet_on_the_current_layout`. The finding names
both directories, includes the `git mv` to run, and flips `healthy` to false.
Against this repository, `magicite doctor` reports
`layout.legacy: false`, `data_dir_name: ".magicite"`, empty note.

## AC-M6 — the repository's own registry is operational at the new path — **PASS**

The registry moved by `git mv .spectra/engrams .magicite/engrams` (rename
preserved in history, not delete+add).

```
uv run magicite sync --project-root .
-> {"synced": 30, "removed": [], "validation_errors": [], "dangling": [], ...}
```

`magicite doctor` reports `indexed_registry_size: 30`.

`scripts/dogfood_graph_check.py`: 30 engrams (16 operational, 14 codebase), 35
declared edges, 0 dangling, 4 cross-tranche; 130 source files scanned, 30 path
references and 50 symbol references verified. All checks pass.

`scripts/dogfood_session.py`: handshake OK, 16 tools advertised, 4/4 probes in
top-3, and every tool in the surface returned a non-error result —
`introspect`, `route`, `load_skill_body`, `signal_use`, `signal_outcome`,
`session_end`, `consolidate`, `checkpoint` (24 engrams written), `flag_dead`,
`sync`, `export`, and the four R3 proposal tools (all `requires_approval`,
nothing mutated).

## AC-M7 — frozen verify — **PASS**

`ruff check .` clean; `mypy src` clean (61 files); **489 passed** at 94.53%
coverage (up from 478 / 94.26% — the 11 new resolution and layout tests);
45 acceptance tests pass; `magicite tools` reports exactly 16.

Two pre-existing tests were touched, and both were *improved* rather than
weakened: `test_register_import.py` and `test_registry_core.py` built registry
paths from hardcoded string parts and now use `cfg.registry_dir`, so they no
longer encode any layout at all.

## AC-M8 — no stale user-facing reference — **PASS**

Swept `README.md`, `docs/`, `Dockerfile`, `.dockerignore`, `.gitignore`,
`src/`, `scripts/`, `tests/`, and the engrams. Every surviving `.spectra`
mention is one of: ESL's own `.spectra/changes/`; the deliberate legacy-fallback
code and its tests; the migration instructions in `docs/operations.md` §15; or
`.dockerignore` excluding **both** directories from the build context.

**Container verified against a freshly built image**, which is where this AC
earned its keep. The first run reported one failure — and the cause was that
`IMAGE_TAG` in `test_docker_smoke.py` is hardcoded to `magicite:verify`, so the
suite had silently run against a **stale pre-change image** that still wrote to
`.spectra/`. Four tests passed because they are path-agnostic; the one test
that asserts where `skill-graph.db` lands caught it. After
`docker build` + retag, all **5 container tests pass**, including the
`--network none` handshake and the uid-ownership assertion now checking
`.magicite/engrams/skill-graph.db`.

A direct container probe confirmed the layout independently: 7 engrams
ingested, `.magicite/{engrams,archive,approvals,runtime}` created, DB at
`.magicite/engrams/skill-graph.db`, and nothing written to `.spectra/`.

## Scope discipline

`.spectra/changes/` was not touched. Routing, plasticity, the trust gate, and
the 16-tool surface are unchanged — this change moves files and resolves paths,
and produced no measurement, so it licenses no claim about anything in
`docs/01`'s Falsification Record.
