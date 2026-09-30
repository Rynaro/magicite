"""Release promotion verdicts (evaluation.md E3 / E4).

Pure decision rules over already-computed intervals and sample counts.
Never mutates the default policy — callers feed outcomes into S07's
reviewed activation / ``retain_simple_incumbent_evidence``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from magicite.eval.metrics import BootstrapInterval, paired_bootstrap_ci
from magicite.eval.validate import EFFICACY_METRICS

VerdictStatus = Literal["pass", "fail", "inconclusive", "unevaluated"]

#: evaluation.md E3 defaults (local release choices, not paper evidence).
DEFAULT_NONINFERIORITY_MARGIN = 0.02
DEFAULT_IMPROVEMENT_POINT_MIN = 0.01
DEFAULT_CRITICAL_SLICE_MARGIN = 0.05
DEFAULT_ABSTENTION_FALSE_SELECTION_UPPER = 0.05
DEFAULT_ABSTENTION_COVERAGE_LOWER = 0.80
#: Empirical slices need ≥30 independent groups for a separately reported
#: interval (evaluation.md E3); below that → insufficient evidence.
MIN_EMPIRICAL_SLICE_GROUPS = 30
#: Floor for overall paired promotion when no archived power plan exists.
#: Never lowered; ``min_groups_required`` may raise it from a power plan.
MIN_OVERALL_GROUPS_FLOOR = 30


def min_groups_required(sample_power_plan: dict[str, Any] | None = None) -> int:
    """Effective independent-group floor for promotion gates.

    Always ``max(MIN_OVERALL_GROUPS_FLOOR, plan_minimum)``. The floor is never
    reduced by an underspecified or smaller power-plan value.
    """
    plan = dict(sample_power_plan or {})
    planned = 0
    for key in ("min_independent_groups", "n_groups_min", "n_groups", "sample_floor"):
        value = plan.get(key)
        if isinstance(value, (int, float)) and int(value) > planned:
            planned = int(value)
    return max(MIN_OVERALL_GROUPS_FLOOR, planned)


@dataclass(frozen=True)
class Verdict:
    """One preregistered gate outcome."""

    gate: str
    status: VerdictStatus
    reason: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "reason": self.reason,
            "details": dict(self.details),
        }


def _status_from_ci(
    *,
    interval: BootstrapInterval,
    pass_predicate: bool,
    fail_when_powered: bool,
    min_groups: int,
    gate: str,
    pass_reason: str,
    fail_reason: str,
) -> Verdict:
    if interval.n_groups < min_groups:
        return Verdict(
            gate=gate,
            status="inconclusive",
            reason=(
                f"insufficient independent groups ({interval.n_groups} < {min_groups}); keep frozen incumbent"
            ),
            details=interval.to_dict(),
        )
    if pass_predicate:
        return Verdict(gate=gate, status="pass", reason=pass_reason, details=interval.to_dict())
    if fail_when_powered:
        return Verdict(gate=gate, status="fail", reason=fail_reason, details=interval.to_dict())
    return Verdict(
        gate=gate,
        status="inconclusive",
        reason="interval does not decide the preregistered hypothesis",
        details=interval.to_dict(),
    )


def noninferiority_verdict(
    interval: BootstrapInterval,
    *,
    margin: float = DEFAULT_NONINFERIORITY_MARGIN,
    min_groups: int = MIN_OVERALL_GROUPS_FLOOR,
) -> Verdict:
    """Pass only if lower 95% CI of candidate−incumbent ≥ −margin."""
    return _status_from_ci(
        interval=interval,
        pass_predicate=interval.low >= -margin,
        fail_when_powered=interval.high < -margin,
        min_groups=min_groups,
        gate="noninferiority_hit_at_1",
        pass_reason=f"lower CI {interval.low:.6f} >= -{margin}",
        fail_reason=f"upper CI {interval.high:.6f} < -{margin} (inferior)",
    )


def improvement_verdict(
    interval: BootstrapInterval,
    *,
    point_min: float = DEFAULT_IMPROVEMENT_POINT_MIN,
    min_groups: int = MIN_OVERALL_GROUPS_FLOOR,
) -> Verdict:
    """Improvement claims require lower CI > 0 and point ≥ point_min."""
    passes = interval.low > 0.0 and interval.point_estimate >= point_min
    clearly_fails = interval.high <= 0.0
    return _status_from_ci(
        interval=interval,
        pass_predicate=passes,
        fail_when_powered=clearly_fails,
        min_groups=min_groups,
        gate="improvement_hit_at_1",
        pass_reason=(
            f"lower CI {interval.low:.6f} > 0 and point {interval.point_estimate:.6f} >= {point_min}"
        ),
        fail_reason=f"upper CI {interval.high:.6f} <= 0 (no improvement)",
    )


def critical_slice_verdict(
    interval: BootstrapInterval,
    *,
    margin: float = DEFAULT_CRITICAL_SLICE_MARGIN,
    min_groups: int = MIN_EMPIRICAL_SLICE_GROUPS,
) -> Verdict:
    """Critical empirical slice: lower paired bound ≥ −0.05 (evaluation.md E3)."""
    return _status_from_ci(
        interval=interval,
        pass_predicate=interval.low >= -margin,
        fail_when_powered=interval.high < -margin,
        min_groups=min_groups,
        gate="critical_slice_noninferiority",
        pass_reason=f"slice lower CI {interval.low:.6f} >= -{margin}",
        fail_reason=f"slice upper CI {interval.high:.6f} < -{margin}",
    )


def holm_adjust_alphas(n_tests: int, *, alpha: float = 0.05) -> list[float]:
    """Holm step-down thresholds for ``n_tests`` ordered hypotheses.

    Returns thresholds for ranks 1..n (most significant first):
    ``alpha / (n - i + 1)``.
    """
    if n_tests < 0:
        raise ValueError("n_tests must be >= 0")
    if n_tests == 0:
        return []
    return [alpha / (n_tests - i + 1) for i in range(1, n_tests + 1)]


def holm_critical_slice_family(
    slice_intervals: list[tuple[str, BootstrapInterval]],
    *,
    margin: float = DEFAULT_CRITICAL_SLICE_MARGIN,
    family_alpha: float = 0.05,
    min_groups: int = MIN_EMPIRICAL_SLICE_GROUPS,
    inferiority_p_values: dict[str, float] | None = None,
) -> Verdict:
    """Holm step-down over preregistered inferiority p-values plus E3 CI gate.

    CI endpoints cannot recover hypothesis p-values. Callers without the
    preregistered test's p-values receive inconclusive, never a Holm PASS.
    """
    gate = "critical_slice_holm"
    names = [name for name, _ in slice_intervals]
    if not names or len(set(names)) != len(names):
        return Verdict(gate, "inconclusive", "missing or duplicate critical slices", {})
    if any(
        i.n_groups < min_groups or not all(math.isfinite(v) for v in (i.low, i.high, i.point_estimate))
        for _, i in slice_intervals
    ):
        return Verdict(gate, "inconclusive", "underpowered or nonfinite critical slice", {})
    if (
        inferiority_p_values is None
        or set(inferiority_p_values) != set(names)
        or any(
            isinstance(p, bool) or not math.isfinite(p) or not 0 <= p <= 1
            for p in inferiority_p_values.values()
        )
    ):
        return Verdict(gate, "inconclusive", "missing or invalid preregistered p-values", {})
    if not 0 < family_alpha < 1:
        raise ValueError("family_alpha must be in (0, 1)")
    ordered = sorted(inferiority_p_values.items(), key=lambda item: (item[1], item[0]))
    thresholds = holm_adjust_alphas(len(ordered), alpha=family_alpha)
    failures: list[dict[str, Any]] = []
    adjusted: dict[str, float] = {}
    running = 0.0
    stopped = False
    for rank, ((name, p_value), threshold) in enumerate(zip(ordered, thresholds, strict=True), 1):
        running = max(running, (len(ordered) - rank + 1) * p_value)
        adjusted[name] = min(1.0, running)
        if not stopped and p_value <= threshold:
            failures.append({"slice": name, "rank": rank, "p_value": p_value, "holm_alpha": threshold})
        else:
            stopped = True
    details = {
        "failures": failures,
        "thresholds": thresholds,
        "adjusted_p_values": adjusted,
        "n_slices": len(names),
    }
    if failures:
        return Verdict(gate, "fail", "Holm-corrected inferiority detected", details)
    if any(interval.low < -margin for _, interval in slice_intervals):
        return Verdict(gate, "inconclusive", "critical-slice lower-bound gate not met", details)
    return Verdict(gate, "pass", "all critical-slice CI and Holm gates met", details)


def abstention_verdict(
    *,
    coverage_wilson_low: float | None,
    false_selection_wilson_high: float | None,
    n_answerable: int,
    n_no_match: int,
    coverage_lower: float = DEFAULT_ABSTENTION_COVERAGE_LOWER,
    false_selection_upper: float = DEFAULT_ABSTENTION_FALSE_SELECTION_UPPER,
    min_answerable: int = MIN_OVERALL_GROUPS_FLOOR,
    min_no_match: int = MIN_OVERALL_GROUPS_FLOOR,
) -> Verdict:
    """Abstention gate (evaluation.md E3)."""
    if n_answerable < min_answerable or n_no_match < min_no_match:
        return Verdict(
            gate="abstention",
            status="inconclusive",
            reason=(f"insufficient abstention sample (answerable={n_answerable}, no_match={n_no_match})"),
            details={
                "n_answerable": n_answerable,
                "n_no_match": n_no_match,
                "min_answerable": min_answerable,
                "min_no_match": min_no_match,
            },
        )
    if coverage_wilson_low is None or false_selection_wilson_high is None:
        return Verdict(
            gate="abstention",
            status="unevaluated",
            reason="missing Wilson bounds (no calibration artifact / empty strata)",
            details={},
        )
    coverage_ok = coverage_wilson_low >= coverage_lower
    false_ok = false_selection_wilson_high <= false_selection_upper
    details = {
        "coverage_wilson_low": coverage_wilson_low,
        "false_selection_wilson_high": false_selection_wilson_high,
        "coverage_lower": coverage_lower,
        "false_selection_upper": false_selection_upper,
    }
    if coverage_ok and false_ok:
        return Verdict(
            gate="abstention",
            status="pass",
            reason="coverage and false-selection Wilson bounds meet preregistered gates",
            details=details,
        )
    return Verdict(
        gate="abstention",
        status="fail",
        reason="abstention Wilson bounds miss preregistered gates",
        details=details,
    )


def usefulness_verdict(
    interval: BootstrapInterval,
    *,
    min_groups: int = MIN_OVERALL_GROUPS_FLOOR,
) -> Verdict:
    """Positive usefulness requires paired 95% lower bound > 0 (evaluation.md E4)."""
    return _status_from_ci(
        interval=interval,
        pass_predicate=interval.low > 0.0,
        fail_when_powered=interval.high <= 0.0,
        min_groups=min_groups,
        gate="host_task_usefulness",
        pass_reason=f"paired usefulness lower CI {interval.low:.6f} > 0",
        fail_reason=f"paired usefulness upper CI {interval.high:.6f} <= 0",
    )


def reject_structural_efficacy_substitution(
    *,
    evidence_class: str,
    metric: str,
    has_paired_host_verifier_outcomes: bool,
) -> Verdict:
    """AC-S14-04: end-task usefulness may not rest on structural corpus alone."""
    efficacy_like = metric in EFFICACY_METRICS
    if evidence_class == "structural" and efficacy_like:
        return Verdict(
            gate="no_structural_efficacy_substitution",
            status="fail",
            reason="structural evidence cannot support end-task / retrieval efficacy claims",
            details={
                "evidence_class": evidence_class,
                "metric": metric,
                "has_paired_host_verifier_outcomes": has_paired_host_verifier_outcomes,
            },
        )
    if metric in {"task_pass_rate", "end_task_usefulness", "usefulness_delta"}:
        if evidence_class != "host-task" or not has_paired_host_verifier_outcomes:
            return Verdict(
                gate="no_structural_efficacy_substitution",
                status="fail",
                reason="end-task usefulness requires paired host-verifier outcomes",
                details={
                    "evidence_class": evidence_class,
                    "metric": metric,
                    "has_paired_host_verifier_outcomes": has_paired_host_verifier_outcomes,
                },
            )
    return Verdict(
        gate="no_structural_efficacy_substitution",
        status="pass",
        reason="evidence class and metric pairing is admissible",
        details={
            "evidence_class": evidence_class,
            "metric": metric,
            "has_paired_host_verifier_outcomes": has_paired_host_verifier_outcomes,
        },
    )


def overall_promotion_verdict(
    *,
    noninferiority: Verdict,
    critical_slices: Verdict,
    abstention: Verdict,
    hybrid_vs_incumbent_evaluated: bool,
) -> Verdict:
    """Conjunction gate for hybrid promotion (evaluation.md E3).

    Failed/inconclusive/unevaluated keeps the frozen simple incumbent.
    """
    if not hybrid_vs_incumbent_evaluated:
        return Verdict(
            gate="hybrid_promotion",
            status="unevaluated",
            reason="hybrid vs dense-v1 paired comparison not run; retain frozen incumbent",
            details={
                "noninferiority": noninferiority.to_dict(),
                "critical_slices": critical_slices.to_dict(),
                "abstention": abstention.to_dict(),
            },
        )
    parts = (noninferiority, critical_slices, abstention)
    if any(v.status == "fail" for v in parts):
        return Verdict(
            gate="hybrid_promotion",
            status="fail",
            reason="one or more preregistered gates failed; retain frozen incumbent",
            details={v.gate: v.to_dict() for v in parts},
        )
    if any(v.status in {"inconclusive", "unevaluated"} for v in parts):
        return Verdict(
            gate="hybrid_promotion",
            status="inconclusive",
            reason="preregistered gates inconclusive/unevaluated; retain frozen incumbent",
            details={v.gate: v.to_dict() for v in parts},
        )
    if all(v.status == "pass" for v in parts):
        return Verdict(
            gate="hybrid_promotion",
            status="pass",
            reason="all preregistered gates passed (activation still requires S07 review)",
            details={v.gate: v.to_dict() for v in parts},
        )
    return Verdict(
        gate="hybrid_promotion",
        status="inconclusive",
        reason="unexpected gate mixture",
        details={v.gate: v.to_dict() for v in parts},
    )


def paired_hit_at_1_interval(
    group_ids: list[str],
    candidate_hits: list[float],
    incumbent_hits: list[float],
    *,
    n_resamples: int = 10_000,
    seed: int = 0,
) -> BootstrapInterval:
    """Convenience wrapper matching evaluation.md E3 defaults."""
    return paired_bootstrap_ci(
        group_ids,
        candidate_hits,
        incumbent_hits,
        n_resamples=n_resamples,
        seed=seed,
    )


__all__ = [
    "DEFAULT_ABSTENTION_COVERAGE_LOWER",
    "DEFAULT_ABSTENTION_FALSE_SELECTION_UPPER",
    "DEFAULT_CRITICAL_SLICE_MARGIN",
    "DEFAULT_IMPROVEMENT_POINT_MIN",
    "DEFAULT_NONINFERIORITY_MARGIN",
    "MIN_EMPIRICAL_SLICE_GROUPS",
    "MIN_OVERALL_GROUPS_FLOOR",
    "Verdict",
    "VerdictStatus",
    "abstention_verdict",
    "critical_slice_verdict",
    "holm_adjust_alphas",
    "holm_critical_slice_family",
    "improvement_verdict",
    "min_groups_required",
    "noninferiority_verdict",
    "overall_promotion_verdict",
    "paired_hit_at_1_interval",
    "reject_structural_efficacy_substitution",
    "usefulness_verdict",
]
