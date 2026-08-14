from __future__ import annotations

from magicite.engram import lint, parser


def test_strict_lint_passes_on_toy_fixture(toy_registry_dir) -> None:
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    parsed = parser.parse_file(path, registry_root=toy_registry_dir)
    result = lint.lint_strict(parsed.engram)
    assert result.ok, result.errors


def _minimal_engram_text(
    *, positive: list[str], negative: list[str], not_when: str | None, steps: str
) -> str:
    pos = ", ".join(f'"{p}"' for p in positive)
    neg = ", ".join(f'"{n}"' for n in negative)
    not_when_line = f"  not_when: {not_when!r}\n" if not_when else ""
    return (
        "---\n"
        "spec: engram/0.2\n"
        "name: sample\n"
        "id: egr_00000001\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: does something\n"
        "  use_when: when needed\n"
        f"{not_when_line}"
        "triggers:\n"
        f"  positive: [{pos}]\n"
        f"  negative: [{neg}]\n"
        "---\n"
        f"{steps}\n"
    )


def test_strict_lint_flags_too_few_positive_triggers() -> None:
    text = _minimal_engram_text(
        positive=["only one"],
        negative=["neg"],
        not_when="never",
        steps="## Procedure\n1. Do it.\n",
    )
    parsed = parser.parse_text(text, relpath="x.egr.md")
    result = lint.lint_strict(parsed.engram)
    assert not result.ok
    assert any(i.rule == "positive_triggers" for i in result.errors)


def test_strict_lint_flags_missing_not_when() -> None:
    text = _minimal_engram_text(
        positive=["a", "b", "c"], negative=["neg"], not_when=None, steps="## Procedure\n1. Do it.\n"
    )
    parsed = parser.parse_text(text, relpath="x.egr.md")
    result = lint.lint_strict(parsed.engram)
    assert not result.ok
    assert any(i.rule == "not_when" for i in result.errors)


def test_strict_lint_flags_unnumbered_procedure() -> None:
    text = _minimal_engram_text(
        positive=["a", "b", "c"],
        negative=["neg"],
        not_when="never",
        steps="## Procedure\nJust do it, no numbers.\n",
    )
    parsed = parser.parse_text(text, relpath="x.egr.md")
    result = lint.lint_strict(parsed.engram)
    assert not result.ok
    assert any(i.rule == "procedure_numbered" for i in result.errors)


def test_import_profile_downgrades_errors_to_warnings() -> None:
    text = _minimal_engram_text(
        positive=["only one"], negative=[], not_when=None, steps="## Procedure\nunnumbered\n"
    )
    parsed = parser.parse_text(text, relpath="x.egr.md")
    result = lint.lint_import(parsed.engram)
    assert result.ok  # import profile never hard-fails (CR-4)
    assert result.errors == []
    assert len(result.warnings) >= 3
