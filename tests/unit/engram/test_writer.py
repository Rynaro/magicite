from __future__ import annotations

from pathlib import Path

from magicite.engram import parser, writer
from magicite.storage import lease


def test_atomic_write_produces_final_file_only(tmp_path: Path) -> None:
    target = tmp_path / "sample.egr.md"
    with lease.writer_lease():
        writer.atomic_write(target, "hello\n")
    assert target.read_text() == "hello\n"
    assert not (tmp_path / "sample.egr.md.tmp").exists()
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_overwrites_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "sample.egr.md"
    with lease.writer_lease():
        writer.atomic_write(target, "first\n")
        writer.atomic_write(target, "second\n")
    assert target.read_text() == "second\n"


def test_atomic_write_requires_writer_lease(tmp_path: Path) -> None:
    """G2 (spec §6.2): a caller that never acquired the writer lease is
    denied before any byte is touched -- the guard is real, not a no-op."""
    target = tmp_path / "sample.egr.md"
    assert not lease.held_by_me()
    try:
        writer.atomic_write(target, "hello\n")
        raised = False
    except lease.WriterLeaseError:
        raised = True
    assert raised
    assert not target.exists()
    assert not (tmp_path / "sample.egr.md.tmp").exists()


def test_atomic_replace_never_partial(tmp_path: Path) -> None:
    """AC-007: a ``.egr.md`` file is only ever replaced atomically via a
    temp file plus ``os.replace`` -- an interrupt mid-write (simulated by
    making ``os.fsync`` raise) leaves the target file *and* the tmp file
    both absent/untouched, never a half-written target."""
    import os

    target = tmp_path / "sample.egr.md"
    target.write_text("original\n")

    real_fsync = os.fsync
    calls = {"n": 0}

    def _boom(fd: int) -> None:
        calls["n"] += 1
        raise OSError("simulated interrupt mid-write")

    with lease.writer_lease():
        os.fsync = _boom  # type: ignore[assignment]
        try:
            raised = False
            try:
                writer.atomic_write(target, "corrupted\n")
            except OSError:
                raised = True
        finally:
            os.fsync = real_fsync  # type: ignore[assignment]

    assert raised, "expected the simulated fsync failure to propagate"
    assert calls["n"] >= 1
    # The pre-existing file is untouched (os.replace never ran) and no
    # dangling temp file was left behind (atomic_write cleans up on failure).
    assert target.read_text() == "original\n"
    assert not (tmp_path / "sample.egr.md.tmp").exists()
    assert list(tmp_path.iterdir()) == [target]


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


def test_render_is_deterministic(toy_registry_dir) -> None:
    """AC-021: rendering identical durable state twice produces byte-identical
    output -- this is what makes checkpoint diffs reviewable (spec §2.5)."""
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    parsed = parser.parse_file(path, registry_root=toy_registry_dir)

    first = writer.render_document(parsed.engram, parsed.frontmatter_doc)
    second = writer.render_document(parsed.engram, parsed.frontmatter_doc)
    assert first == second

    # Re-parsing and re-rendering from scratch (a fresh frontmatter_doc, as
    # sync() would do after a DB rebuild) is also byte-identical -- the
    # renderer's determinism does not depend on ruamel's round-trip carrier.
    reparsed = parser.parse_text(first, relpath=parsed.engram.path)
    third = writer.render_document(reparsed.engram, reparsed.frontmatter_doc)
    assert first == third

    for line in first.split("\n"):
        assert line == line.rstrip(), f"trailing whitespace in rendered line: {line!r}"
    assert "\r" not in first
