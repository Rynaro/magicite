"""docs/07 gate functions (spec §5.1 FSM guard column) -- pure, side-effect
free (spec §1: ``core/fitness.py``, "docs/07 gate functions (pure,
side-effect free)").

**M4 shipped** the evidence-bar gate functions AC-016 needs as a P0/R1
defense-in-depth check (a poisoned or merely optimistic caller cannot
promote an engram past a hard, mechanical bar no matter how many Tier-1/2
signals accumulated first). **M5 adds** :func:`structural_rubric_score`
(the deterministic 12-point rubric ``nascent_to_probation_gate`` consumes,
CR-3) -- the approvals wiring, evidence-gathering-from-a-live-connection,
autonomous-mode bypass, and every ``promote``/``sharpen``/``archive``
**tool** body itself live in ``core/lifecycle.py``/``core/approvals.py``/
``mcp/bind_lifecycle.py``, not here: this module stays pure. Nothing here
reads a live DB row or calls ``storage.durable``; callers build an
:class:`Evidence` snapshot (or an :class:`~magicite.engram.model.Engram`,
for the rubric) themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from magicite.config import Config
from magicite.engram.model import Engram


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


# ── the docs/07 structural rubric (M5; CR-3's "deterministic 12-point
# structural rubric", the ≥8/12 bar nascent_to_probation_gate consumes) ──

#: docs/07 §Gate requirement: "Rubric score: Triggers (>=3 positive, >=1
#: negative, distinct), Procedure (clear steps, no circular dependencies),
#: Pitfalls (grounded in observed failures), Examples (positive + negative
#: cases present)." Four categories, three points each -- deterministic,
#: no LLM (CR-3: the v1 server ships no generative model; `rubric_provider
#: =host` reserves the LLM-judge path for a future, out-of-scope provider).
#: This is intentionally a *structural* proxy for each category (the same
#: kind of check `engram/lint.py` already runs), not a semantic-quality
#: judgement -- "grounded in observed failures" in particular cannot be
#: verified mechanically, so the pitfalls category scores document
#: *presence and volume* of pitfall entries, not their factual accuracy.
RUBRIC_MAX = 12


def structural_rubric_score(engram: Engram) -> int:
    """docs/07's deterministic rubric, 0-12. Each of the four categories
    below contributes up to 3 points; ``nascent_to_probation_gate`` (this
    module) checks the result against the >=8/12 bar (docs/07 §Gate
    requirement: "Rubric score >= 8/12 (two-thirds pass)")."""
    fm = engram.frontmatter
    body = engram.body
    score = 0

    # Triggers: >=3 positive, >=1 negative, all distinct (case-insensitive).
    if len(fm.triggers.positive) >= 3:
        score += 1
    if len(fm.triggers.negative) >= 1:
        score += 1
    all_triggers = [t.lower() for t in (*fm.triggers.positive, *fm.triggers.negative)]
    if all_triggers and len(all_triggers) == len(set(all_triggers)):
        score += 1

    # Procedure: has numbered steps, no leftover unstructured text (engram/
    # lint.py's own `procedure_numbered` rule), and does not declare itself
    # as a dependency of itself (the one circularity a single engram's own
    # frontmatter can express; cross-engram cycles are `core/composition
    # .py`'s concern at routing time, not this file's).
    if body.procedure:
        score += 1
    if body.procedure and not body.procedure_raw.strip():
        score += 1
    self_referential = fm.name in (*fm.needs, *fm.composes, *fm.inhibits)
    if not self_referential:
        score += 1

    # Pitfalls: present, non-trivial text, more than one documented.
    if body.pitfalls:
        score += 1
    if any(len(p.text.strip()) >= 10 for p in body.pitfalls):
        score += 1
    if len(body.pitfalls) >= 2:
        score += 1

    # Examples: at least one positive case, at least one negative case,
    # and at least two total (docs/07: "positive + negative cases present").
    if any(e.positive for e in body.examples):
        score += 1
    if any(not e.positive for e in body.examples):
        score += 1
    if len(body.examples) >= 2:
        score += 1

    return score
