"""docs/07 gate functions (spec §5.1 FSM guard column) -- pure, side-effect
free (spec §1: ``core/fitness.py``, "docs/07 gate functions (pure,
side-effect free)").

**Scope note (read before extending):** this ships the evidence-bar gate
functions AC-016 needs as a P0/R1 defense-in-depth check (a poisoned or
merely optimistic caller cannot promote an engram past a hard, mechanical
bar no matter how many Tier-1/2 signals accumulated first). The **rest** of
the lifecycle FSM -- approvals wiring, the ``nascent→probation`` rubric/
injection-scan guard's actual invocation, autonomous-mode bypass, and every
``promote``/``sharpen``/``archive`` **tool** body -- is explicitly M5's job
(spec Stories M5; ``mcp/bind_lifecycle.py``'s own docstring: "the lifecycle
FSM and approval machinery ... land in M5"). Nothing in this module reads a
live DB row or calls ``storage.durable``; callers build an :class:`Evidence`
snapshot themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from magicite.config import Config


@dataclass(frozen=True)
class Evidence:
    """The evidence a lifecycle guard evaluates against (spec §5.1's guard
    column). Deliberately plain data -- no DB handle, no I/O -- so every
    gate function here is trivially unit-testable at boundary values
    (spec §7.1's own unit-test table row for this module)."""

    storage_strength: float
    pass_rate: float
    distinct_sessions: int
    recent_valences: tuple[float, ...] = field(default_factory=tuple)


def nascent_to_probation_gate(
    *, rubric_score: int, injection_scan_clean: bool, rubric_min: int = 8
) -> list[str]:
    """spec §5.1: "reconstruction_ok∨n/a ∧ rubric ≥ 8/12 ∧
    injection_scan_clean". Reconstruction is "ships for distilled only"
    (spec §7.3) -- not a concern for the authored/imported engrams this
    milestone's tests exercise, so it is not modelled here; a future
    caller that needs it passes its own pre-computed boolean the same way
    ``injection_scan_clean`` is passed. Returns the unmet guard names;
    ``[]`` means the guard passes."""
    unmet: list[str] = []
    if rubric_score < rubric_min:
        unmet.append(f"rubric_score {rubric_score} < {rubric_min}")
    if not injection_scan_clean:
        unmet.append("injection_scan_clean is False")
    return unmet


def probation_to_consolidated_gate(evidence: Evidence, cfg: Config) -> list[str]:
    """spec §5.1: "S ≥ 0.6 ∧ pass_rate ≥ 0.9 ∧ sessions ≥ 3 ∧ no valence <
    −0.7 in last 5". AC-016's own example (S=0.4, pass_rate=0.8) is this
    gate: both the S and pass_rate bars are unmet, and both are named."""
    unmet: list[str] = []
    if evidence.storage_strength < cfg.theta_consolidate_status:
        unmet.append(
            f"storage_strength {evidence.storage_strength} < {cfg.theta_consolidate_status}"
        )
    if evidence.pass_rate < 0.9:
        unmet.append(f"pass_rate {evidence.pass_rate} < 0.9")
    if evidence.distinct_sessions < 3:
        unmet.append(f"distinct_sessions {evidence.distinct_sessions} < 3")
    last5 = evidence.recent_valences[-5:]
    if any(v < -0.7 for v in last5):
        unmet.append("a valence < -0.7 exists in the last 5 outcomes")
    return unmet


def consolidated_to_promoted_gate(
    evidence: Evidence, *, no_evidence_decay: bool = True
) -> list[str]:
    """spec §5.1: "S ≥ 0.85 ∧ pass_rate ≥ 0.98 ∧ distinct_sessions ≥ 10 ∧
    no evidence decay"."""
    unmet: list[str] = []
    if evidence.storage_strength < 0.85:
        unmet.append(f"storage_strength {evidence.storage_strength} < 0.85")
    if evidence.pass_rate < 0.98:
        unmet.append(f"pass_rate {evidence.pass_rate} < 0.98")
    if evidence.distinct_sessions < 10:
        unmet.append(f"distinct_sessions {evidence.distinct_sessions} < 10")
    if not no_evidence_decay:
        unmet.append("evidence has decayed since the last checkpoint")
    return unmet
