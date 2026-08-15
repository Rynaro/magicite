"""Ranking + composition metrics (spec §1: ``eval/metrics.py``; docs/07
§Metrics; spec §7.1 unit-test table: "Hit@k, MRR, Plan F1 against
hand-computed fixtures"). Pure functions, no I/O, no DB handle -- callers
(``eval/bench.py``) feed in plain ranked-name lists and labels.
"""

from __future__ import annotations

from dataclasses import dataclass


def hit_at_k(ranked_names: list[str], expected: str, k: int) -> bool:
    """docs/07: "Is the correct skill in the top k?" -- ``ranked_names``
    is already truncated/untruncated by the caller; this only looks at
    the first ``k`` entries."""
    return expected in ranked_names[:k]


def reciprocal_rank(ranked_names: list[str], expected: str) -> float:
    """``1/rank`` of ``expected`` in ``ranked_names`` (1-indexed), or
    ``0.0`` if it never appears -- the per-query MRR term."""
    for i, name in enumerate(ranked_names):
        if name == expected:
            return 1.0 / (i + 1)
    return 0.0


@dataclass(frozen=True)
class RankingReport:
    n_queries: int
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    mrr: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "n_queries": self.n_queries,
            "hit_at_1": round(self.hit_at_1, 4),
            "hit_at_3": round(self.hit_at_3, 4),
            "hit_at_5": round(self.hit_at_5, 4),
            "mrr": round(self.mrr, 4),
        }


def aggregate_ranking(per_query: list[tuple[list[str], str]]) -> RankingReport:
    """``per_query``: ``[(ranked_names, expected_top1), ...]``. Averages
    Hit@1/3/5 and reciprocal rank across every query -- AC-029's own
    metric set ("Hit@1, Hit@3, Hit@5, MRR ... for both baselines")."""
    n = len(per_query)
    if n == 0:
        return RankingReport(n_queries=0, hit_at_1=0.0, hit_at_3=0.0, hit_at_5=0.0, mrr=0.0)
    h1 = sum(1 for ranked, exp in per_query if hit_at_k(ranked, exp, 1)) / n
    h3 = sum(1 for ranked, exp in per_query if hit_at_k(ranked, exp, 3)) / n
    h5 = sum(1 for ranked, exp in per_query if hit_at_k(ranked, exp, 5)) / n
    mrr = sum(reciprocal_rank(ranked, exp) for ranked, exp in per_query) / n
    return RankingReport(n_queries=n, hit_at_1=h1, hit_at_3=h3, hit_at_5=h5, mrr=mrr)


@dataclass(frozen=True)
class PlanF1Result:
    #: ``None`` iff nothing was actually evaluated (see
    #: :func:`aggregate_plan_f1`'s module note on the vacuous-truth fix) --
    #: a real, per-pair :func:`plan_f1` call always leaves these as floats.
    precision: float | None
    recall: float | None
    f1: float | None
    order_correct: bool
    #: How many ``(predicted, expected)`` pairs actually fed the average
    #: below, vs. how many were offered. ``n_evaluated < n_total`` means
    #: some pairs were vacuous (both sequences empty -- see
    #: :func:`aggregate_plan_f1``) and were excluded rather than silently
    #: scored as a perfect match. Always ``(1, 1)`` for a single non-vacuous
    #: :func:`plan_f1` call.
    n_evaluated: int = 1
    n_total: int = 1

    def to_dict(self) -> dict[str, float | bool | None]:
        return {
            "precision": None if self.precision is None else round(self.precision, 4),
            "recall": None if self.recall is None else round(self.recall, 4),
            "f1": None if self.f1 is None else round(self.f1, 4),
            "order_correct": self.order_correct,
        }


def plan_f1(predicted: list[str], expected: list[str]) -> PlanF1Result:
    """docs/07 §Composition Metrics: "Plan F1: Precision & recall of the
    skill sequence (correct skills in correct order)." Set-based
    precision/recall over the two sequences' *membership*, plus a
    separate ``order_correct`` flag (the predicted sequence, restricted to
    the names both sequences share, is in the exact same relative order
    as ``expected``) -- "correct skills" (membership, P/R/F1) and "in
    correct order" (a boolean) are kept as two honestly distinct readings
    of that one sentence rather than conflated into a single ad hoc
    order-weighted score spec does not define.
    """
    pred_set, exp_set = set(predicted), set(expected)
    if not pred_set and not exp_set:
        return PlanF1Result(precision=1.0, recall=1.0, f1=1.0, order_correct=True)
    tp = len(pred_set & exp_set)
    precision = tp / len(pred_set) if pred_set else 0.0
    recall = tp / len(exp_set) if exp_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    shared = exp_set & pred_set
    pred_order = [n for n in predicted if n in shared]
    exp_order = [n for n in expected if n in shared]
    order_correct = pred_order == exp_order

    return PlanF1Result(precision=precision, recall=recall, f1=f1, order_correct=order_correct)


def aggregate_plan_f1(per_query: list[tuple[list[str], list[str]]]) -> PlanF1Result:
    """Mean precision/recall/F1 across every compositional query;
    ``order_correct`` is the fraction of queries whose order matched,
    reported as a 0/1-averaged float cast back through the same
    dataclass shape for a single, uniform return type.

    **Vacuous-truth fix (M7 close-out item #3).** A ``(predicted=[],
    expected=[])`` pair is not "a plan that was correctly predicted empty"
    -- ``core/composition.py::expand()`` always seeds its closure with the
    winning engram itself, so a *real*, registered engram's expected plan
    is never empty (it is at minimum ``[winner_name]``). The only way both
    sides come back empty is that there was nothing to route against at
    all (an empty registry, or a query labelled to a name that does not
    resolve to any engram) -- ``eval/bench.py::run_baseline`` hits this
    exact path when ``ranked`` (and therefore ``predicted_plan``) is empty
    because the registry has nothing routable. Scoring that as a *perfect*
    plan_f1 (the pre-fix behaviour: precision/recall/f1 all 1.0) silently
    flatters a run that evaluated nothing -- indistinguishable, in the
    emitted numbers, from a run that correctly predicted every plan. Such
    pairs are therefore excluded from the average rather than counted as a
    trivial match; ``n_evaluated``/``n_total`` on the returned
    :class:`PlanF1Result` make the exclusion visible, and precision/recall/
    f1 come back ``None`` (JSON ``null``) -- not ``0.0``, which would look
    like "evaluated and wrong" -- when nothing was evaluated at all.
    """
    n_total = len(per_query)
    evaluable = [(pred, exp) for pred, exp in per_query if pred or exp]
    n_evaluated = len(evaluable)
    if n_evaluated == 0:
        return PlanF1Result(
            precision=None,
            recall=None,
            f1=None,
            order_correct=False,
            n_evaluated=0,
            n_total=n_total,
        )
    results = [plan_f1(pred, exp) for pred, exp in evaluable]
    precision = sum(r.precision for r in results if r.precision is not None) / n_evaluated
    recall = sum(r.recall for r in results if r.recall is not None) / n_evaluated
    f1 = sum(r.f1 for r in results if r.f1 is not None) / n_evaluated
    order_correct = sum(1 for r in results if r.order_correct) / n_evaluated == 1.0
    return PlanF1Result(
        precision=precision,
        recall=recall,
        f1=f1,
        order_correct=order_correct,
        n_evaluated=n_evaluated,
        n_total=n_total,
    )


def ndcg_at_k(ranked_names: list[str], relevant: dict[str, float], k: int) -> float:
    """docs/07 §Ranking Metrics / spec §7.3: "NDCG | stub | metric
    function only, unwired". A real, correct DCG/IDCG implementation
    (kept genuinely correct rather than a placeholder that always returns
    0 or 1, since a stub that lies is worse than an honest
    NotImplementedError) -- just never called from ``eval/bench.py``'s
    baseline runner in v1 (spec's own ship/stub table), because v1 has no
    graded-relevance benchmark labels (``queries.jsonl`` is single-label
    ``expected_top1``, not a relevance grade per candidate) to feed it
    with. ``relevant``: ``{name: graded_relevance}``, absent names score 0.
    """
    import math

    dcg = 0.0
    for i, name in enumerate(ranked_names[:k]):
        rel = relevant.get(name, 0.0)
        if rel:
            dcg += rel / math.log2(i + 2)
    ideal_order = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal_order) if rel)
    return (dcg / idcg) if idcg > 0 else 0.0
