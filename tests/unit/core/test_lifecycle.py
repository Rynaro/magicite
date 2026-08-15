"""AC-016: promotion denied below the evidence bar (spec §5.1's guard
column, ``core/fitness.py``). This is the M4 slice of ``core/lifecycle.py``
-- the pure evidence-gate check, not the full FSM/approvals machinery
(M5); see the module's own docstring for the scope boundary.
"""

from __future__ import annotations

import pytest

from magicite.config import Config
from magicite.core import fitness as fitness_mod
from magicite.core import lifecycle as lifecycle_mod
from magicite.errors import TransitionDeniedError


def test_promote_denied_below_evidence_bar() -> None:
    """AC-016: GIVEN an engram with S=0.4 and pass_rate=0.8 WHEN promote()
    (here, the pure evidence-gate lifecycle.apply()) is called THEN the
    call SHALL return transition_denied naming the unmet guards, leaving
    the status unchanged (apply() is pure -- no mutation ever happens on
    the denial path, so "leaving the status unchanged" is structural, the
    same discipline core/plasticity.py::apply() uses for AC-014)."""
    evidence = fitness_mod.Evidence(storage_strength=0.4, pass_rate=0.8, distinct_sessions=5)

    with pytest.raises(TransitionDeniedError) as excinfo:
        lifecycle_mod.apply(
            from_status="probation", to_status="consolidated", evidence=evidence, cfg=Config()
        )

    unmet = excinfo.value.unmet
    assert any("storage_strength" in u for u in unmet)
    assert any("pass_rate" in u for u in unmet)


def test_promote_allowed_when_evidence_bar_cleared() -> None:
    evidence = fitness_mod.Evidence(storage_strength=0.9, pass_rate=0.95, distinct_sessions=5)
    # No exception -- a passing guard just returns.
    assert (
        lifecycle_mod.apply(
            from_status="probation", to_status="consolidated", evidence=evidence, cfg=Config()
        )
        is None
    )


def test_unknown_transition_raises_value_error() -> None:
    evidence = fitness_mod.Evidence(storage_strength=1.0, pass_rate=1.0, distinct_sessions=99)
    with pytest.raises(ValueError):
        lifecycle_mod.apply(from_status="draft", to_status="promoted", evidence=evidence)


def test_nascent_to_probation_denial_names_guards() -> None:
    evidence = fitness_mod.Evidence(storage_strength=0.0, pass_rate=0.0, distinct_sessions=0)
    with pytest.raises(TransitionDeniedError) as excinfo:
        lifecycle_mod.apply(
            from_status="nascent",
            to_status="probation",
            evidence=evidence,
            rubric_score=5,
            injection_scan_clean=True,
        )
    assert excinfo.value.unmet == ["rubric_score 5 < 8"]
