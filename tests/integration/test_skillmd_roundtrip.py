"""AC-018: ``register(skill) -> export -> register(skill)`` is stable at
the second import (spec §5.4's round-trip test, CR-8's duplicate-import
detection).

AC-S02-01: archived 0.2 and SKILL.md fixtures with prose, fences and
extensions preserve source bytes through parse/write/export (v1 corpus).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from magicite.core import registry as registry_mod
from magicite.engram import parser as parser_mod
from magicite.engram import skillmd
from magicite.engram import transform as transform_mod
from magicite.engram import writer as writer_mod

pytestmark = pytest.mark.acceptance

V1_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "engram-v1"


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
    assert second.ingested == 0
    assert second.skipped_unchanged == 1
    assert second.validation_errors == []

    after = _snapshot(db_conn, "wine-dxvk-cache-clear")
    assert after == before
    assert after["id"] == imported_id
    assert after["status"] == "consolidated"


def test_v1_fixture_corpus_preserves_skillmd_and_02_bytes(toy_registry_dir: Path) -> None:
    """AC-S02-01: preserved SKILL.md body bytes and writer-canonical 0.2 bytes."""
    raw_skill = (
        "---\nname: corpus-fences\ndescription: |\n"
        "  Keep fences. Use when exporting. NOT for empty bodies.\n"
        "paths:\n  - 'scripts/**/*.sh'\n"
        "metadata:\n  agents: [all]\n  version: 1.0.0\n"
        "---\n\n# Title\n\nProse before sections.\n\n"
        "## Procedure\n\n1. Run the check.\n\n"
        "```bash\necho preserved\n```\n\n"
        "## Pitfalls\n\n- Do not strip fences.\n"
    )
    _yaml_text, original_body = parser_mod.split_frontmatter(raw_skill)
    source = skillmd.parse_source(raw_skill)
    # Exact body bytes from the source file (not a semantic proxy).
    assert source.body_text == original_body
    assert source.body_text.encode("utf-8") == original_body.encode("utf-8")

    engram = skillmd.to_engram(source, target_relpath="engrams/corpus-fences.egr.md")
    assert engram.frontmatter.skill_md_source is not None
    assert engram.frontmatter.skill_md_source.body_raw == original_body
    assert engram.frontmatter.skill_md_source.body_raw.encode("utf-8") == original_body.encode("utf-8")
    assert engram.frontmatter.skill_md_source.extra_frontmatter == source.extra_frontmatter

    persisted = writer_mod.render_document(engram)
    reparsed = parser_mod.parse_text(persisted, relpath="engrams/corpus-fences.egr.md").engram
    assert reparsed.frontmatter.skill_md_source is not None
    assert reparsed.frontmatter.skill_md_source.body_raw.encode("utf-8") == original_body.encode("utf-8")
    assert reparsed.frontmatter.skill_md_source.extra_frontmatter == source.extra_frontmatter

    exported = skillmd.render_skillmd(reparsed)
    # Export reconstructs from preserved body_raw — suffix is exact original body bytes.
    assert exported.encode("utf-8").endswith(original_body.encode("utf-8"))
    exported_source = skillmd.parse_source(exported)
    assert exported_source.body_text.encode("utf-8") == original_body.encode("utf-8")
    assert exported_source.extra_frontmatter == source.extra_frontmatter

    # Writer-canonical 0.2: parse → write → bytes identical when format unchanged.
    path_02 = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    original_02_bytes = path_02.read_bytes()
    parsed_02 = parser_mod.parse_file(path_02, registry_root=toy_registry_dir)
    canonical_02 = writer_mod.render_document(parsed_02.engram)
    roundtrip = parser_mod.parse_text(canonical_02, relpath=parsed_02.engram.path)
    again_02 = writer_mod.render_document(roundtrip.engram, roundtrip.frontmatter_doc)
    assert again_02.encode("utf-8") == canonical_02.encode("utf-8")
    # On-disk archived bytes are never mutated by transform.
    transformed = transform_mod.transform_0_2_to_1_0(parsed_02.engram).engram
    rendered_v1 = writer_mod.render_document_v1(transformed)
    again_v1, _ = parser_mod.load_artifact(rendered_v1, relpath=parsed_02.engram.path)
    assert again_v1.id == parsed_02.engram.id
    assert path_02.read_bytes() == original_02_bytes

    positive = V1_FIXTURES / "positive" / "sample-host-tooling.egr.md"
    art, doc = parser_mod.load_artifact(
        positive.read_text(encoding="utf-8"), relpath="positive/sample-host-tooling.egr.md"
    )
    rerendered = writer_mod.render_document_v1(art, doc)
    art2, _ = parser_mod.load_artifact(rerendered, relpath="positive/sample-host-tooling.egr.md")
    assert art2.id == art.id
    assert art2.frontmatter.relations == art.frontmatter.relations
