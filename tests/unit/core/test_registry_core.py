from __future__ import annotations

import pytest

from magicite.core import registry as registry_mod
from magicite.errors import NotImplementedToolError, PathOutsideProjectError


def test_register_rejects_path_outside_project(cfg, db_conn, embedder, tmp_path) -> None:
    outside = tmp_path.parent / "definitely-outside"
    with pytest.raises(PathOutsideProjectError):
        registry_mod.register(cfg, db_conn, embedder, path=str(outside))


def test_register_is_idempotent_on_unchanged_content(cfg, db_conn, embedder) -> None:
    first = registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    assert first.ingested == 7
    assert first.skipped_unchanged == 0

    second = registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    assert second.ingested == 0
    assert second.skipped_unchanged == 7


def test_register_wires_declared_needs_edge(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    row = db_conn.execute(
        "SELECT dst_name, dangling FROM edge WHERE src_id = "
        "(SELECT id FROM engram WHERE name = 'proton-ge-proton-downgrade') AND type = 'depends_on'"
    ).fetchone()
    assert row["dst_name"] == "steam-prefix-access"
    assert row["dangling"] == 0


def test_register_reports_dangling_edge_for_unregistered_target(cfg, db_conn, embedder, tmp_path) -> None:
    # Register only the downgrade engram (whose `needs: [steam-prefix-access]` target is absent).
    only_one = tmp_path / "solo"
    only_one.mkdir()
    src = cfg.project_root / ".spectra" / "engrams" / "proton-ge-proton-downgrade.egr.md"
    (only_one / "proton-ge-proton-downgrade.egr.md").write_text(src.read_text())

    outcome = registry_mod.register(
        cfg, db_conn, embedder, path=str(only_one.relative_to(cfg.project_root))
    )
    assert outcome.ingested == 1
    row = db_conn.execute(
        "SELECT dangling FROM edge WHERE dst_name = 'steam-prefix-access' AND type = 'depends_on'"
    ).fetchone()
    assert row is not None
    assert row["dangling"] == 1


def test_register_skill_format_not_implemented_in_m0(cfg, db_conn, embedder, toy_registry_dir) -> None:
    skills_dir = cfg.project_root / "skills"
    import shutil

    shutil.copytree(toy_registry_dir / "skills", skills_dir)

    with pytest.raises(NotImplementedToolError):
        registry_mod.register(cfg, db_conn, embedder, path="skills", fmt="skill")


def test_sync_removes_rows_for_deleted_files(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    victim = cfg.registry_dir / "nvidia-prime-render-offload.egr.md"
    victim.unlink()

    outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert "nvidia-prime-render-offload" in outcome.removed
    remaining = db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]
    assert remaining == 6
