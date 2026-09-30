"""Release promotion verdicts (AC-S14-03, AC-S14-04).

Hand-computed boundary cases follow evaluation.md E3/E4 exactly. No
threshold fudge: intervals are constructed with explicit lows/highs.
"""

from __future__ import annotations

from magicite.eval.host_tasks import HostTaskArmResult, evaluate_paired_host_tasks
from magicite.eval.metrics import BootstrapInterval
from magicite.eval.validate import claim_eligible_for_new_run_gate, validate_claim_data
from magicite.eval.verdicts import (
    DEFAULT_NONINFERIORITY_MARGIN,
    MIN_OVERALL_GROUPS_FLOOR,
    abstention_verdict,
    critical_slice_verdict,
    holm_adjust_alphas,
    holm_critical_slice_family,
    improvement_verdict,
    noninferiority_verdict,
    overall_promotion_verdict,
    reject_structural_efficacy_substitution,
    usefulness_verdict,
)


def _interval(
    *,
    point: float,
    low: float,
    high: float,
    n_groups: int = MIN_OVERALL_GROUPS_FLOOR,
) -> BootstrapInterval:
    return BootstrapInterval(
        point_estimate=point,
        low=low,
        high=high,
        n_groups=n_groups,
        n_resamples=10_000,
        seed=0,
    )


def test_noninferiority_boundary_pass_at_exact_margin() -> None:
    """Lower CI exactly −0.02 passes noninferiority (evaluation.md E3)."""
    interval = _interval(point=0.0, low=-DEFAULT_NONINFERIORITY_MARGIN, high=0.02)
    verdict = noninferiority_verdict(interval)
    assert verdict.status == "pass"
    assert verdict.details["low"] == -0.02


def test_noninferiority_boundary_fail_when_upper_below_margin() -> None:
    interval = _interval(point=-0.05, low=-0.08, high=-0.021)
    verdict = noninferiority_verdict(interval)
    assert verdict.status == "fail"


def test_noninferiority_inconclusive_when_ci_straddles_margin() -> None:
    interval = _interval(point=-0.01, low=-0.03, high=0.01)
    verdict = noninferiority_verdict(interval)
    assert verdict.status == "inconclusive"


def test_noninferiority_insufficient_groups_is_inconclusive() -> None:
    interval = _interval(point=0.05, low=0.01, high=0.09, n_groups=10)
    verdict = noninferiority_verdict(interval)
    assert verdict.status == "inconclusive"
    assert "insufficient independent groups" in verdict.reason


def test_improvement_requires_lower_ci_gt_zero_and_point_ge_001() -> None:
    pass_case = _interval(point=0.01, low=0.001, high=0.03)
    assert improvement_verdict(pass_case).status == "pass"

    # lower CI == 0 is not > 0
    boundary = _interval(point=0.02, low=0.0, high=0.04)
    assert improvement_verdict(boundary).status == "inconclusive"

    # point below 0.01
    small_point = _interval(point=0.005, low=0.001, high=0.02)
    assert improvement_verdict(small_point).status == "inconclusive"

    clearly_worse = _interval(point=-0.02, low=-0.05, high=-0.001)
    assert improvement_verdict(clearly_worse).status == "fail"


def test_critical_slice_margin_boundary() -> None:
    pass_case = _interval(point=0.0, low=-0.05, high=0.02)
    assert critical_slice_verdict(pass_case).status == "pass"

    fail_case = _interval(point=-0.08, low=-0.12, high=-0.06)
    assert critical_slice_verdict(fail_case).status == "fail"


def test_holm_adjust_alphas_hand_computed() -> None:
    # m=3, alpha=0.05 → 0.05/3, 0.05/2, 0.05/1
    assert holm_adjust_alphas(3, alpha=0.05) == [0.05 / 3, 0.05 / 2, 0.05 / 1]
    assert holm_adjust_alphas(0) == []


def test_holm_critical_slice_family_detects_ordered_failure() -> None:
    slices = [
        ("lang", _interval(point=0.0, low=-0.01, high=0.02)),
        ("rare", _interval(point=-0.1, low=-0.2, high=-0.08)),
    ]
    verdict = holm_critical_slice_family(slices)
    assert verdict.status == "fail"
    assert verdict.details["failures"][0]["slice"] == "rare"


def test_holm_critical_slice_family_passes_when_all_clear() -> None:
    slices = [
        ("lang", _interval(point=0.0, low=-0.01, high=0.02)),
        ("rare", _interval(point=0.01, low=-0.02, high=0.04)),
    ]
    assert holm_critical_slice_family(slices).status == "pass"


def test_abstention_wilson_gates() -> None:
    pass_v = abstention_verdict(
        coverage_wilson_low=0.81,
        false_selection_wilson_high=0.04,
        n_answerable=40,
        n_no_match=40,
    )
    assert pass_v.status == "pass"

    fail_v = abstention_verdict(
        coverage_wilson_low=0.70,
        false_selection_wilson_high=0.10,
        n_answerable=40,
        n_no_match=40,
    )
    assert fail_v.status == "fail"

    underpowered = abstention_verdict(
        coverage_wilson_low=0.99,
        false_selection_wilson_high=0.01,
        n_answerable=5,
        n_no_match=5,
    )
    assert underpowered.status == "inconclusive"


def test_usefulness_requires_lower_bound_gt_zero() -> None:
    assert usefulness_verdict(_interval(point=0.1, low=0.01, high=0.2)).status == "pass"
    assert usefulness_verdict(_interval(point=0.0, low=-0.05, high=0.05)).status == "inconclusive"
    assert usefulness_verdict(_interval(point=-0.1, low=-0.2, high=-0.01)).status == "fail"


def test_overall_promotion_unevaluated_keeps_incumbent() -> None:
    ni = noninferiority_verdict(_interval(point=0.0, low=-0.01, high=0.02))
    slices = holm_critical_slice_family(
        [("lang", _interval(point=0.0, low=-0.01, high=0.02))]
    )
    abst = abstention_verdict(
        coverage_wilson_low=0.85,
        false_selection_wilson_high=0.03,
        n_answerable=40,
        n_no_match=40,
    )
    overall = overall_promotion_verdict(
        noninferiority=ni,
        critical_slices=slices,
        abstention=abst,
        hybrid_vs_incumbent_evaluated=False,
    )
    assert overall.status == "unevaluated"
    assert "retain frozen incumbent" in overall.reason


def test_no_structural_efficacy_substitution() -> None:
    """AC-S14-04: structural corpus alone cannot support end-task usefulness."""
    rejected = reject_structural_efficacy_substitution(
        evidence_class="structural",
        metric="end_task_usefulness",
        has_paired_host_verifier_outcomes=False,
    )
    assert rejected.status == "fail"

    rejected_hit = reject_structural_efficacy_substitution(
        evidence_class="structural",
        metric="hit_at_1",
        has_paired_host_verifier_outcomes=False,
    )
    assert rejected_hit.status == "fail"

    missing_pairs = reject_structural_efficacy_substitution(
        evidence_class="host-task",
        metric="usefulness_delta",
        has_paired_host_verifier_outcomes=False,
    )
    assert missing_pairs.status == "fail"

    ok = reject_structural_efficacy_substitution(
        evidence_class="host-task",
        metric="usefulness_delta",
        has_paired_host_verifier_outcomes=True,
    )
    assert ok.status == "pass"

    # Validator path mirrors the same rule for Claim/1.
    claim = {
        "schema": "magicite-claim/1",
        "claim_id": "structural-usefulness-illegal",
        "text_location": "docs/evaluation/v1/README.md",
        "metric": "end_task_usefulness",
        "value": 0.9,
        "unit": "fraction",
        "population_split": "structural",
        "result_digest": "a" * 64,
        "confidence_interval": None,
        "evidence_class": "structural",
        "status": "supported",
        "limitations": "none",
    }
    errors = validate_claim_data(claim)
    assert any("structural evidence cannot support" in e for e in errors)
    assert claim_eligible_for_new_run_gate(claim)


def test_paired_host_tasks_report_never_marks_structural() -> None:
    arms = [
        HostTaskArmResult(
            task_id="t1",
            group_id="g1",
            arm="no_skill",
            outcome="fail",
            verifier_id="echo",
            verifier_artifact_digest="d" * 64,
        ),
        HostTaskArmResult(
            task_id="t1",
            group_id="g1",
            arm="composed_plan",
            outcome="pass",
            verifier_id="echo",
            verifier_artifact_digest="d" * 64,
        ),
    ]
    # Underpowered by design → inconclusive/unevaluated, never fabricated pass.
    report = evaluate_paired_host_tasks(arms, n_resamples=50, seed=0, min_groups=30)
    assert report.evidence_class == "host-task"
    assert report.to_dict()["has_paired_host_verifier_outcomes"] is True
    assert report.usefulness_status in {"inconclusive", "unevaluated", "fail"}
    assert report.usefulness_status != "pass"
