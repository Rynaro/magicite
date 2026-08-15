"""G3 (spec §6.2): "learned state (plasticity.*, synapses.*) has exactly
one writer in the codebase -- Dream's checkpoint."

Two checks, mirroring ``tests/unit/test_p0_enforcement.py``'s AST-based
style for G1/G2:

1. Static: ``storage.lease.dream_context()`` -- the only function that
   *sets* the G3 marker -- is called from exactly one source file,
   ``core/dream.py``.
2. Behavioural: ``engram.writer.write_plasticity()``/``write_synapses()``
   raise outside Dream's checkpoint phase and succeed inside it -- the
   mechanical assertion the static check alone cannot prove (a grep can
   show *who* calls the setter, not that the gate actually blocks writes).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from magicite.engram import parser as parser_mod
from magicite.engram import writer as writer_mod
from magicite.storage.lease import DreamContextError, dream_context

SRC = Path(__file__).parents[1] / "src" / "magicite"

#: Matches a call to storage.lease.dream_context(), however it is
#: imported/qualified (``lease_mod.dream_context()``, ``dream_context()``
#: after a ``from ... import dream_context``, etc.) -- a regex is
#: sufficient here (unlike the import-based P0 check) because the thing
#: being controlled is a *call expression*, not an import statement.
_DREAM_CONTEXT_CALL_RE = re.compile(r"\bdream_context\s*\(")

#: The only source file allowed to call the G3 setter.
_ALLOWED_CALLER = "core/dream.py"


def test_only_core_dream_calls_the_g3_setter() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        relpath = str(path.relative_to(SRC))
        text = path.read_text(encoding="utf-8")
        if _DREAM_CONTEXT_CALL_RE.search(text) and relpath != _ALLOWED_CALLER:
            # storage/lease.py defines dream_context() itself (the `def
            # dream_context(...):` line matches the regex too) -- that is
            # the definition, not a call site, and is expected.
            if relpath == "storage/lease.py":
                continue
            offenders.append(relpath)
    assert not offenders, f"only {_ALLOWED_CALLER} may call dream_context(): {offenders}"


def _toy_engram(toy_registry_dir: Path):
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    return parser_mod.parse_file(path, registry_root=toy_registry_dir).engram


def test_write_plasticity_denied_outside_dream_context(toy_registry_dir: Path) -> None:
    engram = _toy_engram(toy_registry_dir)
    with pytest.raises(DreamContextError):
        writer_mod.write_plasticity(engram)


def test_write_synapses_denied_outside_dream_context(toy_registry_dir: Path) -> None:
    engram = _toy_engram(toy_registry_dir)
    with pytest.raises(DreamContextError):
        writer_mod.write_synapses(engram)


def test_write_plasticity_and_synapses_succeed_inside_checkpoint_phase(toy_registry_dir: Path) -> None:
    from magicite.core.dream import checkpoint_phase

    engram = _toy_engram(toy_registry_dir)
    with checkpoint_phase():
        rendered_plasticity = writer_mod.write_plasticity(engram)
        rendered_synapses = writer_mod.write_synapses(engram)
    assert rendered_plasticity["storage_strength"] == 0.0
    assert list(rendered_synapses) == []


def test_atomic_write_itself_is_not_g3_gated(toy_registry_dir: Path, tmp_path: Path) -> None:
    """G3 is scoped to write_plasticity()/write_synapses(), not the
    generic atomic_write() primitive register()/sharpen() use to write
    *authored* state without ever being in a Dream checkpoint context (see
    engram/writer.py's module docstring)."""
    from magicite.storage.lease import writer_lease

    target = tmp_path / "authored.egr.md"
    with writer_lease():
        writer_mod.atomic_write(target, "hello")  # must not raise DreamContextError
    assert target.read_text() == "hello"


def test_dream_context_active_during_checkpoint_phase() -> None:
    from magicite.core.dream import checkpoint_phase
    from magicite.storage.lease import in_dream_context

    assert not in_dream_context()
    with checkpoint_phase():
        assert in_dream_context()
    assert not in_dream_context()


def test_dream_context_import_alias_is_the_real_one() -> None:
    # sanity: the symbol this test file imports is exactly the one
    # engram.writer asserts against (no accidental shadow/duplicate).
    from magicite.core import dream as dream_mod

    with dream_context():
        dream_mod.assert_dream_context()  # does not raise
