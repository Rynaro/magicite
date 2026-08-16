"""``obs/doctor.py``: the honest environment check (spec M7, Risks R7/R9)."""

from __future__ import annotations

from pathlib import Path

from magicite.core import registry as registry_mod
from magicite.obs import doctor as doctor_mod


def test_registry_check_reports_missing_dir(tmp_path: Path) -> None:
    from magicite.config import Config

    cfg = Config.load(tmp_path)
    result = doctor_mod.registry_check(cfg)
    assert result["registry_dir_exists"] is False
    assert result["db_exists"] is False
    assert result["indexed_registry_size"] is None
    assert "does not exist" in result["note"]


def test_registry_check_reports_unsynced_egr_files(project_root: Path) -> None:
    from magicite.config import Config

    cfg = Config.load(project_root)
    result = doctor_mod.registry_check(cfg)
    assert result["registry_dir_exists"] is True
    assert result["egr_md_file_count"] == 7
    assert result["db_exists"] is False
    assert "magicite sync" in result["note"]


def test_registry_check_reports_indexed_size_once_synced(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    result = doctor_mod.registry_check(cfg)
    assert result["db_exists"] is True
    assert result["indexed_registry_size"] == 7
    assert result["note"] is None


def test_filesystem_check_never_raises_on_a_real_path(tmp_path: Path) -> None:
    result = doctor_mod.filesystem_check(tmp_path)
    assert "path" in result
    assert result["network_filesystem"] in (True, False, None)
    assert result["note"]


def test_filesystem_check_flags_known_network_fstypes() -> None:
    assert "nfs" in doctor_mod._NETWORK_FSTYPES
    assert "cifs" in doctor_mod._NETWORK_FSTYPES
    assert "ext4" not in doctor_mod._NETWORK_FSTYPES


def test_embedding_check_hashing_provider(cfg) -> None:
    result = doctor_mod.embedding_check(cfg)
    assert result["provider"] == "hashing"
    assert "test/CI provider" in result["note"] or "deterministic" in result["note"]


def test_embedding_check_fastembed_offline_without_model(tmp_path: Path) -> None:
    from magicite.config import Config

    cfg = Config.load(
        tmp_path, env={"MAGICITE_EMBEDDING_PROVIDER": "fastembed", "MAGICITE_EMBEDDING_OFFLINE": "1"}
    )
    result = doctor_mod.embedding_check(cfg)
    assert result["provider"] == "fastembed"
    assert result["offline"] is True


def test_embedding_check_unknown_provider(tmp_path: Path) -> None:
    from magicite.config import Config

    cfg = Config.load(tmp_path, env={"MAGICITE_EMBEDDING_PROVIDER": "bogus"})
    result = doctor_mod.embedding_check(cfg)
    assert "unrecognized" in result["note"]


def test_governance_check_review_mode_default(cfg) -> None:
    result = doctor_mod.governance_check(cfg)
    assert result["autonomous"] is False
    assert result["hook_token_configured"] is False
    assert "review mode" in result["note"]


def test_governance_check_autonomous_mode(tmp_path: Path) -> None:
    from magicite.config import Config

    cfg = Config.load(tmp_path, env={"MAGICITE_AUTONOMOUS": "1"})
    result = doctor_mod.governance_check(cfg)
    assert result["autonomous"] is True
    assert "immediately" in result["note"]


def test_run_doctor_below_reference_size_is_not_reassuring(cfg, db_conn, embedder) -> None:
    """R9: the toy registry (7 engrams) is well below the ~50-skill
    cold-start reference size -- doctor must say so, not stay silent."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    report = doctor_mod.run_doctor(cfg)
    assert report["cold_start"]["below_reference_size"] is True
    assert report["healthy"] is False
    assert any("R9" in w for w in report["warnings"])


def test_run_doctor_empty_project_never_raises(tmp_path: Path) -> None:
    from magicite.config import Config

    cfg = Config.load(tmp_path)
    report = doctor_mod.run_doctor(cfg)
    assert report["project_root"] == str(tmp_path.resolve())
    assert isinstance(report["warnings"], list)
    assert report["healthy"] is False  # empty registry is always a warning


def test_run_doctor_json_serializable(cfg, db_conn, embedder) -> None:
    import json

    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    report = doctor_mod.run_doctor(cfg)
    # must not raise -- every value doctor produces is JSON-safe
    json.dumps(report, default=str)


def test_layout_check_is_quiet_on_the_current_layout(tmp_path: Path) -> None:
    """[DATA-DIR-AMENDED 2026-08-15] AC-M5: no deprecation noise for a project
    already on `.magicite/`."""
    from magicite.config import Config

    (tmp_path / ".magicite" / "engrams").mkdir(parents=True)
    layout = doctor_mod.layout_check(Config.load(tmp_path, env={}))

    assert layout["legacy"] is False
    assert layout["data_dir_name"] == ".magicite"
    assert layout["note"] == ""


def test_layout_check_flags_the_legacy_directory(tmp_path: Path) -> None:
    """AC-M5: a project still on `.spectra/` keeps working, and is told.

    The fallback is deliberately silent-proof: resolution succeeding is not a
    reason to leave a project on a deprecated layout indefinitely, so doctor
    reports it as a warning naming both directories and the move to make.
    """
    from magicite.config import Config

    (tmp_path / ".spectra" / "engrams").mkdir(parents=True)
    cfg = Config.load(tmp_path, env={})
    layout = doctor_mod.layout_check(cfg)

    assert layout["legacy"] is True
    assert layout["data_dir_name"] == ".spectra"
    assert layout["expected"] == ".magicite"
    assert ".magicite" in layout["note"] and ".spectra" in layout["note"]
    assert "git mv" in layout["note"]

    report = doctor_mod.run_doctor(cfg)
    assert report["healthy"] is False
    assert any("data layout" in w for w in report["warnings"])
