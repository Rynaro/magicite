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
from magicite.engram.lint import InjectionScanResult
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


# ── initial_verification_status (M5 security fix #1) ─────────────────────

_CLEAN_SCAN = InjectionScanResult(has_exec_blocks=False, over_broad_triggers=False, suspicious_pitfalls=[])
_DIRTY_SCAN = InjectionScanResult(has_exec_blocks=True, over_broad_triggers=False, suspicious_pitfalls=[])


def test_verification_status_quarantine_outranks_everything() -> None:
    """"any -> quarantined" is unconditional (spec §5.1) -- even an
    authored, lint-clean engram is quarantined if the scan flags it."""
    assert (
        lifecycle_mod.initial_verification_status(origin="authored", lint_ok=True, scan=_DIRTY_SCAN)
        == "quarantined"
    )


def test_verification_status_authored_lint_clean_scan_clean_is_verified() -> None:
    assert (
        lifecycle_mod.initial_verification_status(origin="authored", lint_ok=True, scan=_CLEAN_SCAN)
        == "verified"
    )


@pytest.mark.parametrize("origin", ["imported", "distilled"])
def test_verification_status_imported_or_distilled_is_always_pending(origin: str) -> None:
    """Even lint-clean, scan-clean content from these origins starts
    pending -- docs/06 tiers 3-4 require the explicit manual review path,
    never an automatic 'verified' (spec §5.1's own "(imports, distilled)"
    qualifier on the pending -> verified FSM row)."""
    assert (
        lifecycle_mod.initial_verification_status(origin=origin, lint_ok=True, scan=_CLEAN_SCAN)
        == "pending"
    )


def test_verification_status_never_reads_a_caller_supplied_value() -> None:
    """The security property, stated directly: the function's signature
    has no parameter through which a caller-authored `trust.
    verification_status` value could even be passed in -- it is
    structurally impossible for this function to echo one back."""
    import inspect

    sig = inspect.signature(lifecycle_mod.initial_verification_status)
    assert set(sig.parameters) == {"origin", "lint_ok", "scan"}
