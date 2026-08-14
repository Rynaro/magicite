from __future__ import annotations

from pathlib import Path

from magicite.engram import parser, writer


def test_atomic_write_produces_final_file_only(tmp_path: Path) -> None:
    target = tmp_path / "sample.egr.md"
    writer.atomic_write(target, "hello\n")
    assert target.read_text() == "hello\n"
    assert not (tmp_path / "sample.egr.md.tmp").exists()
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_overwrites_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "sample.egr.md"
    writer.atomic_write(target, "first\n")
    writer.atomic_write(target, "second\n")
    assert target.read_text() == "second\n"


def test_render_document_round_trips(toy_registry_dir) -> None:
    path = toy_registry_dir / "engrams" / "steam-prefix-access.egr.md"
    parsed = parser.parse_file(path, registry_root=toy_registry_dir)

    rendered = writer.render_document(parsed.engram, parsed.frontmatter_doc)
    reparsed = parser.parse_text(rendered, relpath="x.egr.md")

    assert reparsed.engram.frontmatter.name == parsed.engram.frontmatter.name
    assert reparsed.engram.frontmatter.id == parsed.engram.frontmatter.id
    assert reparsed.engram.frontmatter.triggers.positive == parsed.engram.frontmatter.triggers.positive
    assert [s.text for s in reparsed.engram.body.procedure] == [
        s.text for s in parsed.engram.body.procedure
    ]
