"""Hand-computed decision metrics and prospective sampling; no model calls."""

import math

import pytest

from magicite.eval import quality


def outcome(qid="q", *, status="selected", ranks=None, selected=None):
    ranks = ["gold"] if ranks is None else ranks
    selected = ([ranks[0]] if ranks and status == "selected" else []) if selected is None else selected
    return {
        "query_id": qid,
        "status": status,
        "candidate_ids": ranks,
        "scores": [0.5] * len(ranks),
        "selected_ids": selected,
        "abstained": status == "abstained",
        "confidence": None,
        "exclusions": [],
    }


def labels(qid="q", grades=None):
    return {"query_id": qid, "runtime_relevance": {"gold": 1} if grades is None else grades}


def test_relevant_rank_one_abstention_is_decision_miss():
    result = quality.metrics([outcome(status="abstained")], [labels()])
    assert result["decision_hit_at_1"] == 0
    assert result["rank_hit_at_1"] == 1
    assert result["selection_coverage"] == 0
    assert result["selective_accuracy"] is None


def test_actual_wrong_selection_and_graded_multiple_relevance():
    row = outcome(ranks=["wrong", "gold", "other"], selected=["wrong"])
    result = quality.metrics([row], [labels(grades={"gold": 2, "other": 1})])
    assert result["decision_hit_at_1"] == 0
    assert result["recall_at_5"] == 1
    assert result["mrr_at_10"] == 0.5
    expected = (2 / math.log2(3) + 1 / math.log2(4)) / (2 + 1 / math.log2(3))
    assert result["ndcg_at_10_linear_gain"] == pytest.approx(expected)
    assert result["calibrated_ece"] is None


@pytest.mark.parametrize("status", ["error", "timeout"])
def test_error_is_zero_in_full_denominator(status):
    result = quality.metrics(
        [outcome("a"), outcome("b", status=status, ranks=[])], [labels("a"), labels("b")]
    )
    assert result["requested_queries"] == 2 and result["decision_hit_at_1"] == 0.5
    assert result["errors"] == 1


def test_missing_duplicate_and_reordered_predictions():
    rows = [outcome("a"), outcome("b")]
    oracle = [labels("a"), labels("b")]
    assert quality.metrics(rows, oracle) == quality.metrics(list(reversed(rows)), oracle)
    with pytest.raises(ValueError, match="duplicate"):
        quality.metrics(rows + rows, oracle)
    with pytest.raises(ValueError, match="missing"):
        quality.metrics(rows[:1], oracle)
    assert quality.metrics(rows[:1], oracle, allow_incomplete=True)["decision_hit_at_1"] == 0.5


def test_completed_timeout_cannot_be_retried_or_reordered():
    done = [outcome("a", status="timeout", ranks=[])]
    assert quality.resume_rows(done, ["a", "b"]) == {"b"}
    with pytest.raises(ValueError):
        quality.resume_rows(done + done, ["a", "b"])
    with pytest.raises(ValueError):
        quality.resume_rows(done, ["b", "a"])


def test_group_sample_fixed_before_labels_and_keeps_variants():
    runtime = [
        {"query_id": str(i), "query_text": "original text " + str(i), "compatibility_context": {}}
        for i in range(8)
    ]
    scoring = [
        {
            "query_id": str(i),
            "upstream_query": {"original_id": "g" + str(i // 2), "query": runtime[i]["query_text"]},
            "runtime_relevance": {"gold": 1},
        }
        for i in range(8)
    ]
    first = quality.smoke_sample(runtime, scoring, count=2)
    for row in scoring:
        row["runtime_relevance"] = {"different": 5}
    assert first == quality.smoke_sample(list(reversed(runtime)), list(reversed(scoring)), count=2)
    assert len(first["query_ids"]) == 2
    assert all(len(ids) == 2 for ids in first["selected_original_id_groups"].values())


def test_exclusion_violation_is_visible():
    row = outcome()
    row["exclusions"] = [{"engram_id": "gold", "reason_codes": ["denied"]}]
    assert quality.metrics([row], [labels()])["safety_exclusion_violations"] == 1


def test_unselected_forbidden_returned_candidate_is_visible():
    row = outcome(ranks=["gold", "forbidden"], selected=["gold"])
    row["exclusions"] = [{"engram_id": "forbidden", "reason_codes": ["denied"]}]
    assert quality.metrics([row], [labels()])["safety_exclusion_violations"] == 1
