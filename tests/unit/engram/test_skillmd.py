"""``engram/skillmd.py``: SKILL.md import conversion + export rendering
(spec §5.3 step 3b, §5.4; CR-4, CR-7, CR-8)."""

from __future__ import annotations

import pytest

from magicite.engram import parser as parser_mod
from magicite.engram import skillmd
from magicite.engram import writer as writer_mod


def test_parse_source_extracts_name_and_description(toy_registry_dir) -> None:
    raw = (toy_registry_dir / "skills" / "wine-dxvk-cache-clear" / "SKILL.md").read_text()
    source = skillmd.parse_source(raw)
    assert source.name == "wine-dxvk-cache-clear"
    assert "DXVK shader cache" in source.description
    assert "## Procedure" in source.body_text


def test_parse_source_rejects_invalid_name() -> None:
    raw = "---\nname: Not Valid Name!\ndescription: x\n---\nbody\n"
    try:
        skillmd.parse_source(raw)
        raised = False
    except skillmd.SkillMdParseError:
        raised = True
    assert raised


def test_extract_intent_reads_use_when_and_not_when_lines() -> None:
    description = (
        "Fix the widget.\nUse when: the widget is broken.\nNot when: the widget was never installed.\n"
    )
    does, use_when, not_when = skillmd._extract_intent(description)
    assert does == "Fix the widget."
    assert use_when == "the widget is broken."
    assert not_when == "the widget was never installed."


def test_extract_intent_falls_back_when_not_when_absent() -> None:
    """AC-008's GIVEN: a corpus lacking not_when."""
    description = "Fix the widget.\nUse when: the widget is broken.\n"
    does, use_when, not_when = skillmd._extract_intent(description)
    assert not_when == "unspecified — requires review"


def test_extract_intent_falls_back_to_last_line_when_use_when_absent() -> None:
    """No explicit 'Use when:' line, but >=2 description lines: the last
    line becomes the tail fallback (spec §5.3: "else the description tail")."""
    description = "Fix the widget.\nIt happens when the gadget overheats.\n"
    does, use_when, _not_when = skillmd._extract_intent(description)
    assert does == "Fix the widget."
    assert use_when == "It happens when the gadget overheats."


def test_extract_intent_falls_back_to_general_purpose_when_single_sentence() -> None:
    description = "Fix the widget.\n"
    _does, use_when, _not_when = skillmd._extract_intent(description)
    assert use_when == "general purpose"


def test_extract_intent_finds_use_when_marker_mid_line(toy_registry_dir) -> None:
    """Regression: a YAML ``|`` block scalar wraps prose at an arbitrary
    column width, so "Use when:" routinely lands *mid-line*, not just at
    a line start -- steam-download-region-fix's fixture is real-world
    evidence of exactly this ("...changing its\\ndownload region. Use
    when: Steam downloads are far below the local\\ninternet speed...")."""
    raw = (toy_registry_dir / "skills" / "steam-download-region-fix" / "SKILL.md").read_text()
    source = skillmd.parse_source(raw)
    does, use_when, _not_when = skillmd._extract_intent(source.description)

    assert does == ("Fix a Steam client stuck at very low download speed by changing its download region.")
    assert use_when == (
        "Steam downloads are far below the local internet speed for every game, not just one."
    )
    assert "use when" not in does.lower()


def test_extract_intent_truncates_does_to_200_chars() -> None:
    long_sentence = "x" * 250 + "."
    does, _use_when, _not_when = skillmd._extract_intent(long_sentence)
    assert len(does) <= 200


def test_derive_positive_triggers_dedups_and_targets_at_least_three() -> None:
    triggers = skillmd._derive_positive_triggers(
        "proton-battleye-eac-toggle",
        "Toggle BattlEye or Easy Anti-Cheat runtime support for a Steam Proton game.",
        "a multiplayer game refuses to launch with an anti-cheat error under Proton.",
    )
    assert len(triggers) >= 3
    assert len(triggers) == len(set(t.lower() for t in triggers))


def test_to_engram_lands_draft_status_for_a_stock_skillmd(toy_registry_dir) -> None:
    """AC-008/CR-4: no not_when, no negative triggers -> import-profile lint
    always warns (triggers.negative is always []) -> status='draft'."""
    raw = (toy_registry_dir / "skills" / "steam-download-region-fix" / "SKILL.md").read_text()
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/steam-download-region-fix.egr.md")

    assert engram.frontmatter.provenance == "imported"
    assert engram.frontmatter.plasticity is not None
    assert engram.frontmatter.plasticity.status == "draft"
    assert engram.frontmatter.trust is not None
    assert engram.frontmatter.trust.verification_status == "pending"
    assert not engram.routable
    assert engram.frontmatter.triggers.negative == []
    assert len(engram.frontmatter.triggers.positive) >= 3
    assert engram.frontmatter.provenance_journal[-1].event == "imported"
    assert engram.content_sha256 and engram.body_sha256


def test_to_engram_id_is_deterministic_given_identical_source() -> None:
    raw = (
        "---\nname: sample-skill\ndescription: |\n  Does the thing.\n  Use when: it breaks.\n---\n\n"
        "## Procedure\n1. Do it.\n"
    )
    source = skillmd.parse_source(raw)
    a = skillmd.to_engram(source, target_relpath="engrams/sample-skill.egr.md")
    b = skillmd.to_engram(source, target_relpath="engrams/sample-skill.egr.md")
    assert a.id == b.id  # CR-8: content-hash of identity+routing, stable across imports


def test_to_engram_unmatched_body_lands_as_procedure_verbatim() -> None:
    raw = (
        "---\nname: freeform-skill\ndescription: |\n  Does the thing.\n  Use when: it breaks.\n---\n\n"
        "Just some free-form prose with no recognized section headings at all.\n"
    )
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/freeform-skill.egr.md")
    assert engram.body.procedure == []
    assert "free-form prose" in engram.body.procedure_raw


def test_render_skillmd_round_trips_intent_fields() -> None:
    raw = (
        "---\nname: sample-skill\ndescription: |\n  Does the thing.\n  Use when: it breaks.\n"
        "  Not when: it was never installed.\n---\n\n## Procedure\n1. Do it.\n\n"
        "## Pitfalls\n- Watch out.\n\n## Examples\n+ good example\n"
    )
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/sample-skill.egr.md")

    rendered = skillmd.render_skillmd(engram)
    assert rendered.startswith("---\n")
    assert "stats" not in rendered  # no [ok/total] annotations, no (xN) pitfall counts
    assert "[" not in rendered.split("## Procedure")[1].split("## Pitfalls")[0]

    reparsed_source = skillmd.parse_source(rendered)
    does2, use_when2, not_when2 = skillmd._extract_intent(reparsed_source.description)
    assert does2 == engram.frontmatter.intent.does
    assert use_when2 == engram.frontmatter.intent.use_when
    assert not_when2 == engram.frontmatter.intent.not_when

    reimported = skillmd.to_engram(reparsed_source, target_relpath="engrams/sample-skill.egr.md")
    assert reimported.id == engram.id  # AC-018's round-trip precondition


def test_extract_intent_accepts_electionbuddy_markers_without_colons() -> None:
    description = (
        "Operate background jobs. Use when creating or debugging a worker. NOT for recurring schedules."
    )
    assert skillmd._extract_intent(description) == (
        "Operate background jobs.",
        "creating or debugging a worker.",
        "recurring schedules.",
    )


def test_arbitrary_markdown_body_survives_engram_persistence_and_export() -> None:
    raw = (
        "---\nname: background-jobs\ndescription: |\n"
        "  Operate background jobs. Use when changing a worker. NOT for schedules.\n"
        "---\n\n# Background Jobs\n\nShared contract.\n\n"
        "## Enqueue\n\nUse `perform_async`.\n\n"
        "### Queue selection\n\n```ruby\nJob::Queue::DEFAULT\n```\n"
    )
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/background-jobs.egr.md")
    persisted = writer_mod.render_document(engram)
    reparsed = parser_mod.parse_text(persisted, relpath="engrams/background-jobs.egr.md").engram

    exported = skillmd.render_skillmd(reparsed)
    assert skillmd.parse_source(exported).body_text == source.body_text


def test_export_fails_closed_when_preserved_body_projection_changed() -> None:
    raw = (
        "---\nname: sample-skill\n"
        "description: Does it. Use when needed. NOT for elsewhere.\n"
        "---\n\n## Procedure\n1. Original instruction.\n"
    )
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/sample-skill.egr.md")
    engram.body.procedure[0].text = "Changed instruction."

    with pytest.raises(skillmd.SkillMdLossyExportError, match="refusing a lossy export"):
        skillmd.render_skillmd(engram)


def test_learning_stats_do_not_invalidate_preserved_body() -> None:
    raw = (
        "---\nname: sample-skill\n"
        "description: Does it. Use when needed. NOT for elsewhere.\n"
        "---\n\n## Procedure\n1. Original instruction.\n"
    )
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/sample-skill.egr.md")
    engram.body.procedure[0].ok_count = 3
    engram.body.procedure[0].total_count = 4
    exported = skillmd.render_skillmd(engram)
    assert skillmd.parse_source(exported).body_text == source.body_text


def test_preserves_electionbuddy_frontmatter_semantically() -> None:
    raw = (
        "---\nname: migration-helper\ndescription: >-\n"
        "  Manage migrations. Use when changing schema. NOT for deployments.\n"
        "paths:\n  - 'db/migrate/**/*.rb'\n"
        "metadata:\n  version: 1.0.0\n  agents: [all]\n"
        "  election_critical: true\n"
        "output_schema: >-\n  verified migration evidence\n"
        "disable-model-invocation: true\nuser-invocable: false\n"
        "---\n\n## Commands\n\nRun the migration check.\n"
    )
    source = skillmd.parse_source(raw)
    assert set(source.extra_frontmatter) == {
        "paths",
        "metadata",
        "output_schema",
        "disable-model-invocation",
        "user-invocable",
    }

    engram = skillmd.to_engram(source, target_relpath="engrams/migration-helper.egr.md")
    persisted = writer_mod.render_document(engram)
    reparsed = parser_mod.parse_text(persisted, relpath="engrams/migration-helper.egr.md").engram
    exported_source = skillmd.parse_source(skillmd.render_skillmd(reparsed))

    assert exported_source.extra_frontmatter == source.extra_frontmatter
    assert exported_source.body_text == source.body_text


def test_export_rejects_reserved_frontmatter_collision() -> None:
    raw = (
        "---\nname: sample-skill\n"
        "description: Does it. Use when needed. NOT for elsewhere.\n"
        "paths: ['app/**/*.rb']\n---\n\nBody.\n"
    )
    source = skillmd.parse_source(raw)
    engram = skillmd.to_engram(source, target_relpath="engrams/sample-skill.egr.md")
    assert engram.frontmatter.skill_md_source is not None
    engram.frontmatter.skill_md_source.extra_frontmatter["name"] = "shadow-name"

    with pytest.raises(skillmd.SkillMdLossyExportError, match="reserved key.*name"):
        skillmd.render_skillmd(engram)


def test_parse_source_rejects_non_json_yaml_extension_value() -> None:
    raw = "---\nname: sample-skill\ndescription: Does it.\nmetadata:\n  reviewed_on: 2026-08-18\n---\nBody.\n"
    with pytest.raises(skillmd.SkillMdParseError, match="metadata.reviewed_on.*unsupported"):
        skillmd.parse_source(raw)
