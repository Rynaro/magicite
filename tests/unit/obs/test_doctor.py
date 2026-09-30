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


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, int, str]]:
    """Hash every file under root: size, mtime_ns, sha256. Detects creates too."""
    import hashlib

    out: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        st = path.stat()
        out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())
    # Also record directory names so mkdir would show up via new empty dirs... 
    # empty dirs: encode as dir markers
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            rel = path.relative_to(root).as_posix() + "/"
            out.setdefault(rel, (0, path.stat().st_mtime_ns, "dir"))
    return out


def test_zero_write_matrix(tmp_path: Path) -> None:
    """AC-S12-01: doctor never mutates filesystem or DB bytes.

    Covers missing / old-schema / corrupt / read-only data directories.
    """
    import os
    import sqlite3
    import stat

    from magicite.config import Config
    from magicite.storage import db as db_mod

    cases: list[tuple[str, Path]] = []

    # 1) Missing data dir entirely
    missing = tmp_path / "missing"
    missing.mkdir()
    cases.append(("missing", missing))

    # 2) Old-schema DB (user_version=1 material, no later migrations applied content-wise —
    #    create via migrate then rewind user_version is wrong; instead write a minimal
    #    sqlite with user_version=1 and no engram table expected by count).
    old = tmp_path / "old-schema"
    (old / ".magicite" / "engrams").mkdir(parents=True)
    old_db = old / ".magicite" / "engrams" / "skill-graph.db"
    conn = sqlite3.connect(str(old_db))
    conn.execute("PRAGMA user_version = 1")
    conn.execute("CREATE TABLE engram (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    cases.append(("old-schema", old))

    # 3) Corrupt DB bytes
    corrupt = tmp_path / "corrupt"
    (corrupt / ".magicite" / "engrams").mkdir(parents=True)
    (corrupt / ".magicite" / "engrams" / "skill-graph.db").write_bytes(b"not-a-sqlite-database")
    (corrupt / ".magicite" / "engrams" / "toy.egr.md").write_text(
        "---\nspec: engram/0.2\nname: toy\nid: egr_deadbeef\nversion: 1\n"
        "provenance: authored\nintent:\n  does: x\n  use_when: y\n  not_when: z\n"
        "triggers:\n  positive: [a]\n  negative: [b]\n---\n## Procedure\n1. x\n",
        encoding="utf-8",
    )
    cases.append(("corrupt", corrupt))

    # 4) Read-only data directory (after creating a valid migrated DB)
    ro = tmp_path / "readonly"
    (ro / ".magicite" / "engrams").mkdir(parents=True)
    cfg_ro = Config.load(ro, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg_ro.ensure_dirs()
    live = db_mod.connect(cfg_ro.db_path)
    live.execute("INSERT INTO schema_meta (key, value) VALUES ('doctor-ro', '1')")
    live.close()
    # chmod files + dirs read-only
    for path in sorted(ro.rglob("*"), reverse=True):
        mode = stat.S_IRUSR | stat.S_IXUSR if path.is_dir() else stat.S_IRUSR
        os.chmod(path, mode)
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    cases.append(("read-only", ro))

    try:
        for label, root in cases:
            before = _tree_fingerprint(root)
            cfg = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
            report = doctor_mod.run_doctor(cfg)
            assert report["kind"] == "doctor/1"
            assert isinstance(report["checks"], list)
            assert {c["id"] for c in report["checks"]} >= {
                "filesystem.lock_semantics",
                "registry.presence",
                "embedding.provider",
                "cold_start.signal",
                "governance.mode",
                "layout.data_dir",
                "recovery.reconciliation",
            }
            for check in report["checks"]:
                assert check["status"] in {
                    "ok",
                    "warn",
                    "fail",
                    "unknown",
                    "not_applicable",
                }
            after = _tree_fingerprint(root)
            assert after == before, f"doctor mutated {label}: {before.keys() ^ after.keys()}"
    finally:
        # Restore writability so tmp teardown succeeds.
        for _label, root in cases:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    os.chmod(path, stat.S_IRWXU)
                except OSError:
                    pass
            try:
                os.chmod(root, stat.S_IRWXU)
            except OSError:
                pass


def test_doctor_report_is_doctor_v1(cfg) -> None:
    report = doctor_mod.run_doctor(cfg)
    assert report["kind"] == "doctor/1"
    assert "reconciliation_required" in report
    assert isinstance(report["checks"], list)
