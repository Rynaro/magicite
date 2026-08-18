from __future__ import annotations

import pytest

from magicite.engram import parser


def test_round_trip_sample_engram(toy_registry_dir) -> None:
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    parsed = parser.parse_file(path, registry_root=toy_registry_dir)
    engram = parsed.engram

    assert engram.name == "proton-ge-proton-downgrade"
    assert engram.id == "egr_b5320dfd"
    assert len(engram.frontmatter.triggers.positive) >= 3
    assert len(engram.frontmatter.triggers.negative) >= 1
    assert engram.frontmatter.intent.not_when
    assert [s.step_no for s in engram.body.procedure] == [1, 2, 3, 4]
    assert engram.body.procedure_raw == ""
    assert engram.routable is True


def test_missing_frontmatter_fence_raises() -> None:
    with pytest.raises(parser.EngramParseError):
        parser.parse_text("no frontmatter here", relpath="x.egr.md")


def test_malformed_yaml_raises() -> None:
    text = "---\nname: [unterminated\n---\nbody\n"
    with pytest.raises(parser.EngramParseError):
        parser.parse_text(text, relpath="x.egr.md")


def test_exec_blocks_captured_as_inert_text() -> None:
    text = (
        "---\n"
        "spec: engram/0.2\n"
        "name: sample-exec\n"
        "id: egr_00000001\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: does something\n"
        "  use_when: when needed\n"
        "  not_when: never\n"
        "triggers:\n"
        "  positive: [a, b, c]\n"
        "  negative: [d]\n"
        "---\n"
        "## Procedure\n"
        "1. Do the thing.\n"
        "\n"
        "```bash\n"
        "rm -rf /\n"
        "```\n"
    )
    parsed = parser.parse_text(text, relpath="x.egr.md")
    assert len(parsed.engram.body.exec_blocks) == 1
    assert parsed.engram.body.exec_blocks[0].language == "bash"
    assert "rm -rf /" in parsed.engram.body.exec_blocks[0].text


def test_procedure_fault_class_is_parsed_from_canonical_marker() -> None:
    body = parser.parse_body("## Procedure\n1. [fault: OOMKilled] Reduce the batch.\n")
    assert body.procedure[0].fault_class == "OOMKilled"
    assert body.procedure[0].text == "Reduce the batch."
