"""``eval/metrics.py``: Hit@k, MRR, Plan F1 against hand-computed fixtures
(spec §7.1 unit-test table)."""

from __future__ import annotations

import json
from pathlib import Path

from magicite.eval import metrics as metrics_mod

ROOT = Path(__file__).resolve().parents[3]
COMPOSITION_CORPUS = ROOT / "docs/evaluation/composition-v0.3.json"


def test_hit_at_k_true_within_k() -> None:
    assert metrics_mod.hit_at_k(["a", "b", "c"], "b", 3) is True


def test_hit_at_k_false_outside_k() -> None:
    assert metrics_mod.hit_at_k(["a", "b", "c"], "c", 2) is False


def test_reciprocal_rank_first_place() -> None:
    assert metrics_mod.reciprocal_rank(["a", "b"], "a") == 1.0


def test_reciprocal_rank_second_place() -> None:
    assert metrics_mod.reciprocal_rank(["a", "b"], "b") == 0.5


def test_reciprocal_rank_absent_is_zero() -> None:
    assert metrics_mod.reciprocal_rank(["a", "b"], "z") == 0.0


def test_aggregate_ranking_hand_computed() -> None:
    # query 1: expected 'a' at rank 1 -> hit@1/3/5=1, rr=1.0
    # query 2: expected 'b' at rank 2 -> hit@1=0, hit@3/5=1, rr=0.5
    # query 3: expected 'z' absent    -> hit@*=0, rr=0.0
    per_query = [
        (["a", "x", "y"], "a"),
        (["x", "b", "y"], "b"),
        (["x", "y", "w"], "z"),
    ]
    report = metrics_mod.aggregate_ranking(per_query)
    assert report.n_queries == 3
    assert report.hit_at_1 == 1 / 3
    assert report.hit_at_3 == 2 / 3
    assert report.hit_at_5 == 2 / 3
    assert report.mrr == (1.0 + 0.5 + 0.0) / 3


def test_aggregate_ranking_empty_is_zero() -> None:
    report = metrics_mod.aggregate_ranking([])
    assert report.n_queries == 0
    assert report.hit_at_1 == 0.0
    assert report.mrr == 0.0


def test_plan_f1_perfect_match() -> None:
    result = metrics_mod.plan_f1(["a", "b"], ["a", "b"])
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.order_correct is True


def test_plan_f1_partial_overlap() -> None:
    # predicted {a,c}, expected {a,b}: tp=1, precision=1/2, recall=1/2, f1=1/2
    result = metrics_mod.plan_f1(["a", "c"], ["a", "b"])
    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.f1 == 0.5


def test_plan_f1_wrong_order_still_full_membership() -> None:
    result = metrics_mod.plan_f1(["b", "a"], ["a", "b"])
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.order_correct is False


def test_plan_f1_no_overlap_is_zero() -> None:
    result = metrics_mod.plan_f1(["c", "d"], ["a", "b"])
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0


def test_plan_f1_both_empty_is_perfect() -> None:
    result = metrics_mod.plan_f1([], [])
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0


def test_aggregate_plan_f1_hand_computed() -> None:
    per_query = [
        (["a", "b"], ["a", "b"]),  # perfect: p=1,r=1,f1=1
        (["c", "d"], ["a", "b"]),  # no overlap: p=0,r=0,f1=0
    ]
    result = metrics_mod.aggregate_plan_f1(per_query)
    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.f1 == 0.5
    assert result.n_evaluated == 2
    assert result.n_total == 2


def test_aggregate_plan_f1_vacuous_pairs_excluded_from_the_average() -> None:
    """M7 close-out item #3 (bug): a (predicted=[], expected=[]) pair means
    nothing was evaluated (see eval/metrics.py::aggregate_plan_f1's
    docstring -- a real engram's expected plan is never empty), not a
    correctly-predicted trivial plan. It must not silently drag a real
    result toward "perfect"."""
    per_query = [
        (["a", "b"], ["a", "b"]),  # perfect: p=1,r=1,f1=1
        ([], []),  # vacuous: nothing to plan against
    ]
    result = metrics_mod.aggregate_plan_f1(per_query)
    assert result.precision == 1.0  # the one real pair, not diluted by the vacuous one
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.n_evaluated == 1
    assert result.n_total == 2


def test_aggregate_plan_f1_all_vacuous_reports_none_not_a_perfect_score() -> None:
    """The exact bug scenario: an empty registry (or every query's winner
    unresolved) yields nothing but vacuous pairs. The old behaviour
    reported precision=recall=f1=1.0 here -- indistinguishable from a
    genuinely perfect run. The fix reports None (JSON null) plus an
    explicit n_evaluated=0 so the vacancy is never mistaken for success."""
    per_query = [([], []), ([], []), ([], [])]
    result = metrics_mod.aggregate_plan_f1(per_query)
    assert result.precision is None
    assert result.recall is None
    assert result.f1 is None
    assert result.order_correct is False
    assert result.n_evaluated == 0
    assert result.n_total == 3
    # to_dict() must not crash on the None values, and must not silently
    # coerce them into a misleading 0.0 or 1.0.
    d = result.to_dict()
    assert d["precision"] is None
    assert d["recall"] is None
    assert d["f1"] is None


def test_aggregate_plan_f1_empty_input_reports_none() -> None:
    result = metrics_mod.aggregate_plan_f1([])
    assert result.precision is None
    assert result.n_evaluated == 0
    assert result.n_total == 0


def test_ndcg_at_k_perfect_order_is_one() -> None:
    ranked = ["a", "b", "c"]
    relevant = {"a": 3.0, "b": 2.0, "c": 1.0}
    assert metrics_mod.ndcg_at_k(ranked, relevant, 3) == 1.0


def test_ndcg_at_k_worst_order_is_less_than_one() -> None:
    ranked = ["c", "b", "a"]
    relevant = {"a": 3.0, "b": 2.0, "c": 1.0}
    assert metrics_mod.ndcg_at_k(ranked, relevant, 3) < 1.0


def test_ndcg_at_k_no_relevant_items_is_zero() -> None:
    assert metrics_mod.ndcg_at_k(["a", "b"], {}, 2) == 0.0


def test_composition_corpus_is_independent_and_sufficient() -> None:
    """AC-012/AC-038: independent labels, >=20 plans, cycle + dangling coverage."""
    corpus = json.loads(COMPOSITION_CORPUS.read_text(encoding="utf-8"))
    assert corpus["schema"] == "magicite-composition-corpus/1"
    assert corpus["label_policy"]["production_expansion_used"] is False
    assert corpus["label_policy"]["method"] == "manual_topological_reasoning"

    cases = corpus["cases"]
    assert len(cases) >= 20
    assert len({case["id"] for case in cases}) == len(cases)
    assert any("cycle" in case["features"] for case in cases)
    assert any("dangling" in case["features"] for case in cases)
    for case in cases:
        assert case["expected_plan"]
        assert len(case["expected_plan"]) == len(set(case["expected_plan"]))
        assert case["label_provenance"]["production_expansion_used"] is False
        assert case["label_provenance"]["method"] == "manual_topological_reasoning"
        assert case["label_provenance"]["reviewed"] is True
