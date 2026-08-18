"""AC-018: ``register(skill) -> export -> register(skill)`` is stable at
the second import (spec §5.4's round-trip test, CR-8's duplicate-import
detection)."""

from __future__ import annotations

import shutil

import pytest

from magicite.core import registry as registry_mod
from magicite.engram import skillmd

pytestmark = pytest.mark.acceptance


def _snapshot(conn, name: str) -> dict:
    """The semantic (non-timestamp) durable fields for one engram, keyed
    consistently with storage.queries.durable_projection's exclusion list
    (created_at/updated_at/s_decayed_at/file_mtime_ns/provenance_journal
    timestamps are expected to advance on any re-write; everything else
    that defines the engram's *content* must not)."""
    row = conn.execute(
        """
        SELECT id, name, status, verification_status, intent_does, intent_use_when,
               intent_not_when, storage_strength, exposure_count, success_count, failure_count
        FROM engram WHERE name = ?
        """,
        (name,),
    ).fetchone()
    assert row is not None, f"expected an engram named {name!r}"
    triggers = conn.execute(
        "SELECT polarity, ord, text FROM engram_trigger WHERE engram_id = ? ORDER BY polarity, ord",
        (row["id"],),
    ).fetchall()
    return {**dict(row), "triggers": [dict(t) for t in triggers]}


def test_export_import_stable(cfg, db_conn, embedder, toy_registry_dir) -> None:
    """GIVEN a consolidated engram
    WHEN export(out_dir=...) runs and the result is re-registered
    THEN the second import SHALL produce no change to the original engram's
    durable state."""
    skills_src = toy_registry_dir / "skills" / "wine-dxvk-cache-clear"
    skills_dir = cfg.project_root / "skills" / "wine-dxvk-cache-clear"
    shutil.copytree(skills_src, skills_dir)

    first = registry_mod.register(cfg, db_conn, embedder, path="skills", fmt="skill")
    assert first.ingested == 1
    imported_id = first.registered[0].id
    assert first.registered[0].status == "draft"

    # GIVEN a *consolidated* engram: M1 has no lifecycle FSM yet (M5) and
    # imports never reach 'consolidated' on their own -- this direct DB
    # write stands in for what promote()/Dream would later do, purely to
    # satisfy export()'s eligibility filter (status IN ('consolidated',
    # 'promoted')) for this test's GIVEN clause.
    db_conn.execute("UPDATE engram SET status = 'consolidated' WHERE id = ?", (imported_id,))

    before = _snapshot(db_conn, "wine-dxvk-cache-clear")

    export_outcome = registry_mod.export(cfg, db_conn, out_dir="exported", min_status="consolidated")
    assert export_outcome.exported == 1
    exported_skillmd = cfg.project_root / "exported" / "wine-dxvk-cache-clear" / "SKILL.md"
    assert exported_skillmd.is_file()
    original_source = skillmd.parse_source((skills_src / "SKILL.md").read_text(encoding="utf-8"))
    exported_source = skillmd.parse_source(exported_skillmd.read_text(encoding="utf-8"))
    assert exported_source.body_text == original_source.body_text
    assert exported_source.extra_frontmatter == original_source.extra_frontmatter

    second = registry_mod.register(cfg, db_conn, embedder, path="exported", fmt="skill")
    # The second import is a genuine no-op: same identity+routing content
    # (CR-8) -> same id -> duplicate-import short-circuit, nothing written.
    assert second.ingested == 0
    assert second.skipped_unchanged == 1
    assert second.validation_errors == []

    after = _snapshot(db_conn, "wine-dxvk-cache-clear")
    assert after == before
    assert after["id"] == imported_id
    assert after["status"] == "consolidated"  # untouched by the second import
