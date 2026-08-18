from __future__ import annotations

import json

import pytest

from magicite.core import registry as registry_mod
from magicite.engram import parser as parser_mod
from magicite.errors import PathOutsideProjectError


def test_register_rejects_path_outside_project(cfg, db_conn, embedder, tmp_path) -> None:
    outside = tmp_path.parent / "definitely-outside"
    with pytest.raises(PathOutsideProjectError):
        registry_mod.register(cfg, db_conn, embedder, path=str(outside))


def test_canonical_routing_view_v1(cfg) -> None:
    parsed = parser_mod.parse_file(
        cfg.registry_dir / "proton-ge-proton-downgrade.egr.md",
        registry_root=cfg.project_root,
    )
    view = json.loads(registry_mod.canonical_routing_view(parsed.engram))
    assert view["schema"] == "magicite-routing-view/1"
    assert view["fields"] == list(registry_mod.ROUTING_VIEW_FIELDS)
    assert "intent.not_when" not in view
    negative = json.loads(registry_mod.canonical_contraindication_view(parsed.engram) or "{}")
    assert negative["schema"] == "magicite-contraindication-view/1"


def test_register_is_idempotent_on_unchanged_content(cfg, db_conn, embedder) -> None:
    first = registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert first.ingested == 7
    assert first.skipped_unchanged == 0

    second = registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert second.ingested == 0
    assert second.skipped_unchanged == 7


def test_register_wires_declared_needs_edge(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
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
    src = cfg.registry_dir / "proton-ge-proton-downgrade.egr.md"
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


def test_register_skill_format_ingests_via_import_pipeline(cfg, db_conn, embedder, toy_registry_dir) -> None:
    """M1: SKILL.md import is real (spec §5.3); superseded the M0
    not_implemented placeholder this test used to assert. Full CR-4/AC-008
    coverage lives in tests/integration/test_register_import.py."""
    skills_dir = cfg.project_root / "skills"
    import shutil

    shutil.copytree(toy_registry_dir / "skills", skills_dir)

    outcome = registry_mod.register(cfg, db_conn, embedder, path="skills", fmt="skill")
    assert outcome.ingested == 3
    assert outcome.validation_errors == []
    names = {e.name for e in outcome.registered}
    assert names == {
        "proton-battleye-eac-toggle",
        "steam-download-region-fix",
        "wine-dxvk-cache-clear",
    }
    for entry in outcome.registered:
        assert entry.origin == "imported"
        assert entry.verification_status == "pending"
        assert entry.status == "draft"  # negative triggers are always [] on import (CR-4)
        assert entry.warnings, "expected at least the negative_triggers import-profile warning"

    egr_files = sorted(p.name for p in cfg.registry_dir.glob("*.egr.md"))
    assert "proton-battleye-eac-toggle.egr.md" in egr_files


def test_sync_removes_rows_for_deleted_files(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    victim = cfg.registry_dir / "nvidia-prime-render-offload.egr.md"
    victim.unlink()

    outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert "nvidia-prime-render-offload" in outcome.removed
    remaining = db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]
    assert remaining == 6


def test_invalid_authoritative_file_disables_projection(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    victim = cfg.registry_dir / "proton-ge-proton-downgrade.egr.md"
    victim.write_text("not valid frontmatter\n", encoding="utf-8")

    outcome = registry_mod.sync(cfg, db_conn, embedder)

    assert any(error.path.endswith(victim.name) for error in outcome.validation_errors)
    row = db_conn.execute(
        "SELECT verification_status FROM engram WHERE name = 'proton-ge-proton-downgrade'"
    ).fetchone()
    assert row["verification_status"] == "quarantined"


def test_sync_reconciles_removed_edges(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    victim = cfg.registry_dir / "proton-ge-proton-downgrade.egr.md"
    text = victim.read_text(encoding="utf-8")
    victim.write_text(text.replace("needs: [steam-prefix-access]", "needs: []"), encoding="utf-8")

    registry_mod.sync(cfg, db_conn, embedder)

    edge = db_conn.execute(
        "SELECT 1 FROM edge WHERE src_id = "
        "(SELECT id FROM engram WHERE name = 'proton-ge-proton-downgrade') "
        "AND dst_name = 'steam-prefix-access' AND type = 'depends_on'"
    ).fetchone()
    assert edge is None
