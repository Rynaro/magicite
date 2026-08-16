"""[DATA-DIR-AMENDED 2026-08-15] Data-directory resolution (AC-M1..AC-M4).

Magicite owns ``.magicite/``; ``.spectra/`` is ESL/tonberry's and was the
pre-amendment location. These tests pin the three properties that make the
move safe: a fresh project lands on the new directory, a legacy project keeps
working, and an ESL-only ``.spectra/changes/`` tree never drags a fresh
project backwards.
"""

from __future__ import annotations

from pathlib import Path

from magicite.config import (
    DEFAULT_DATA_DIR_NAME,
    LEGACY_DATA_DIR_NAME,
    Config,
    resolve_data_dir_name,
)


def test_data_dir_defaults_to_dot_magicite(tmp_path: Path) -> None:
    """AC-M1: every Magicite-owned path hangs off ``.magicite/``."""
    cfg = Config(project_root=tmp_path)

    assert cfg.data_dir == tmp_path / ".magicite"
    assert cfg.registry_dir == tmp_path / ".magicite" / "engrams"
    assert cfg.archive_dir == tmp_path / ".magicite" / "archive"
    assert cfg.approvals_dir == tmp_path / ".magicite" / "approvals"
    assert cfg.runtime_dir == tmp_path / ".magicite" / "runtime"
    assert cfg.toml_path == tmp_path / ".magicite" / "magicite.toml"
    assert cfg.bench_queries_path == tmp_path / ".magicite" / "bench" / "queries.jsonl"
    assert cfg.db_path == tmp_path / ".magicite" / "engrams" / "skill-graph.db"
    assert cfg.dream_lock_path == tmp_path / ".magicite" / "runtime" / "dream.lock"
    assert cfg.uses_legacy_layout is False


def test_data_dir_ensure_dirs_creates_under_magicite(tmp_path: Path) -> None:
    """AC-M1: ``ensure_dirs()`` creates the tree in the new location, and
    creates nothing at all under the legacy one."""
    Config(project_root=tmp_path).ensure_dirs()

    assert (tmp_path / ".magicite" / "engrams").is_dir()
    assert (tmp_path / ".magicite" / "archive").is_dir()
    assert (tmp_path / ".magicite" / "approvals").is_dir()
    assert (tmp_path / ".magicite" / "runtime").is_dir()
    assert not (tmp_path / ".spectra").exists()


def test_fresh_project_resolves_to_magicite(tmp_path: Path) -> None:
    """AC-M3: nothing on disk -> the current layout."""
    assert resolve_data_dir_name(tmp_path, {}) == DEFAULT_DATA_DIR_NAME


def test_legacy_registry_is_still_found(tmp_path: Path) -> None:
    """AC-M2: a project whose registry lives in the old place keeps working.

    Silently resolving to an empty ``.magicite/`` would leave the server
    routing against zero skills -- a silent failure, which is why the fallback
    exists at all.
    """
    (tmp_path / ".spectra" / "engrams").mkdir(parents=True)

    assert resolve_data_dir_name(tmp_path, {}) == LEGACY_DATA_DIR_NAME

    cfg = Config.load(tmp_path, env={})
    assert cfg.uses_legacy_layout is True
    assert cfg.registry_dir == tmp_path / ".spectra" / "engrams"


def test_fresh_project_ignores_esl_changes_dir(tmp_path: Path) -> None:
    """AC-M3: ``.spectra/changes/`` is ESL's and says nothing about Magicite.

    This is the case that makes the fallback safe to ship: every project in
    this ecosystem that uses ESL has a ``.spectra/changes/`` tree, and none of
    them should be pulled onto Magicite's deprecated layout because of it.
    """
    (tmp_path / ".spectra" / "changes" / "some-change").mkdir(parents=True)

    assert resolve_data_dir_name(tmp_path, {}) == DEFAULT_DATA_DIR_NAME
    assert Config.load(tmp_path, env={}).uses_legacy_layout is False


def test_new_layout_wins_when_both_exist(tmp_path: Path) -> None:
    """A half-migrated project prefers the new tree rather than flip-flopping."""
    (tmp_path / ".magicite" / "engrams").mkdir(parents=True)
    (tmp_path / ".spectra" / "engrams").mkdir(parents=True)

    assert resolve_data_dir_name(tmp_path, {}) == DEFAULT_DATA_DIR_NAME


def test_env_override_beats_default_and_legacy(tmp_path: Path) -> None:
    """AC-M4: an explicit host decision always wins."""
    (tmp_path / ".spectra" / "engrams").mkdir(parents=True)

    assert resolve_data_dir_name(tmp_path, {"MAGICITE_DATA_DIR": ".elsewhere"}) == ".elsewhere"

    cfg = Config.load(tmp_path, env={"MAGICITE_DATA_DIR": ".elsewhere"})
    assert cfg.data_dir == tmp_path / ".elsewhere"
    assert cfg.registry_dir == tmp_path / ".elsewhere" / "engrams"
    # An explicit override is not the legacy layout, even though a legacy tree
    # exists -- the flag reports what resolution actually chose.
    assert cfg.uses_legacy_layout is False


def test_blank_env_override_is_ignored(tmp_path: Path) -> None:
    """An empty/whitespace value is an unset variable, not a request for ``""``."""
    assert resolve_data_dir_name(tmp_path, {"MAGICITE_DATA_DIR": "   "}) == DEFAULT_DATA_DIR_NAME


def test_toml_cannot_relocate_its_own_directory(tmp_path: Path) -> None:
    """``magicite.toml`` lives inside the directory it would be naming, so the
    field is deliberately excluded from TOML application (circularity)."""
    data_dir = tmp_path / ".magicite"
    data_dir.mkdir()
    (data_dir / "magicite.toml").write_text(
        '[storage]\ndata_dir_name = ".hijacked"\n\n[routing]\nppr_restart = 0.5\n',
        encoding="utf-8",
    )

    cfg = Config.load(tmp_path, env={})

    assert cfg.data_dir_name == DEFAULT_DATA_DIR_NAME
    # ...while an ordinary field from the same file still applies, proving the
    # exclusion is targeted rather than the TOML being ignored wholesale.
    assert cfg.ppr_restart == 0.5
