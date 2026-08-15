"""``core/fitness.py``: docs/07 gate functions at boundary values (spec
§7.1 unit-test table row: "each docs/07 gate at boundary values
(0.6/0.9/3, 0.85/0.98/10)")."""

from __future__ import annotations

from magicite.config import Config
from magicite.core import fitness as fitness_mod


def _evidence(**kw: object) -> fitness_mod.Evidence:
    base = {"storage_strength": 1.0, "pass_rate": 1.0, "distinct_sessions": 10, "recent_valences": ()}
    base.update(kw)
    return fitness_mod.Evidence(**base)  # type: ignore[arg-type]


# ── nascent -> probation ─────────────────────────────────────────────────


def test_nascent_to_probation_passes_at_exact_boundary() -> None:
    assert fitness_mod.nascent_to_probation_gate(rubric_score=8, injection_scan_clean=True) == []


def test_nascent_to_probation_fails_just_below_boundary() -> None:
    unmet = fitness_mod.nascent_to_probation_gate(rubric_score=7, injection_scan_clean=True)
    assert unmet == ["rubric_score 7 < 8"]


def test_nascent_to_probation_fails_on_dirty_injection_scan() -> None:
    unmet = fitness_mod.nascent_to_probation_gate(rubric_score=12, injection_scan_clean=False)
    assert "injection_scan_clean is False" in unmet


# ── probation -> consolidated (0.6 / 0.9 / 3) ────────────────────────────


def test_probation_to_consolidated_passes_at_exact_boundary() -> None:
    cfg = Config()
    ev = _evidence(storage_strength=0.6, pass_rate=0.9, distinct_sessions=3, recent_valences=(0.1, 0.2))
    assert fitness_mod.probation_to_consolidated_gate(ev, cfg) == []


def test_probation_to_consolidated_names_every_unmet_guard() -> None:
    """AC-016's own scenario: S=0.4, pass_rate=0.8."""
    cfg = Config()
    ev = _evidence(storage_strength=0.4, pass_rate=0.8, distinct_sessions=1)
    unmet = fitness_mod.probation_to_consolidated_gate(ev, cfg)
    assert any("storage_strength" in u for u in unmet)
    assert any("pass_rate" in u for u in unmet)
    assert any("distinct_sessions" in u for u in unmet)


def test_probation_to_consolidated_fails_on_recent_bad_valence() -> None:
    cfg = Config()
    ev = _evidence(
        storage_strength=0.9, pass_rate=0.95, distinct_sessions=5, recent_valences=(0.9, 0.9, 0.9, 0.9, -0.71)
    )
    unmet = fitness_mod.probation_to_consolidated_gate(ev, cfg)
    assert any("valence" in u for u in unmet)


def test_probation_to_consolidated_ignores_valence_outside_last_five() -> None:
    cfg = Config()
    ev = _evidence(
        storage_strength=0.9,
        pass_rate=0.95,
        distinct_sessions=5,
        recent_valences=(-1.0, 0.9, 0.9, 0.9, 0.9, 0.9),  # the -1.0 is 6th-from-last
    )
    assert fitness_mod.probation_to_consolidated_gate(ev, cfg) == []


# ── consolidated -> promoted (0.85 / 0.98 / 10) ─────────────────────────


def test_consolidated_to_promoted_passes_at_exact_boundary() -> None:
    ev = _evidence(storage_strength=0.85, pass_rate=0.98, distinct_sessions=10)
    assert fitness_mod.consolidated_to_promoted_gate(ev) == []


def test_consolidated_to_promoted_fails_just_below_every_boundary() -> None:
    ev = _evidence(storage_strength=0.84, pass_rate=0.97, distinct_sessions=9)
    unmet = fitness_mod.consolidated_to_promoted_gate(ev)
    assert len(unmet) == 3


def test_consolidated_to_promoted_fails_on_evidence_decay() -> None:
    ev = _evidence(storage_strength=0.9, pass_rate=0.99, distinct_sessions=20)
    unmet = fitness_mod.consolidated_to_promoted_gate(ev, no_evidence_decay=False)
    assert unmet == ["evidence has decayed since the last checkpoint"]
