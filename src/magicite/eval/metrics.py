"""Ranking + composition metrics (spec §1: ``eval/metrics.py``; docs/07
§Metrics; spec §7.1 unit-test table: "Hit@k, MRR, Plan F1 against
hand-computed fixtures"). Pure functions, no I/O, no DB handle -- callers
(``eval/bench.py``) feed in plain ranked-name lists and labels.

V1 (S01 / evaluation.md E3): paired group bootstrap intervals and
abstention coverage helpers. Plan F1 against production-derived gold is a
**deprecated diagnostic** — independent structural corpora supply gold
via ``magicite.eval.gold``.
"""

from __future__ import annotations

import math
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
    dcg = 0.0
    for i, name in enumerate(ranked_names[:k]):
        rel = relevant.get(name, 0.0)
        if rel:
            dcg += rel / math.log2(i + 2)
    ideal_order = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal_order) if rel)
    return (dcg / idcg) if idcg > 0 else 0.0


#: Status string for Plan F1 scored against production ``expand()`` gold.
#: Independent corpus annotations use :data:`PLAN_F1_INDEPENDENT`.
PLAN_F1_DEPRECATED_DIAGNOSTIC = "deprecated_diagnostic_circular_gold"
PLAN_F1_INDEPENDENT = "independent_corpus"


@dataclass(frozen=True)
class BootstrapInterval:
    """Percentile CI over paired group-level deltas (evaluation.md E3)."""

    point_estimate: float
    low: float
    high: float
    n_groups: int
    n_resamples: int
    seed: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "point_estimate": round(self.point_estimate, 6),
            "low": round(self.low, 6),
            "high": round(self.high, 6),
            "n_groups": self.n_groups,
            "n_resamples": self.n_resamples,
            "seed": self.seed,
        }


def _group_means(
    group_ids: list[str],
    candidate_scores: list[float],
    incumbent_scores: list[float],
) -> list[float]:
    if not (len(group_ids) == len(candidate_scores) == len(incumbent_scores)):
        raise ValueError("group_ids and score vectors must have equal length")
    buckets: dict[str, list[float]] = {}
    for group, cand, inc in zip(group_ids, candidate_scores, incumbent_scores, strict=True):
        buckets.setdefault(group, []).append(cand - inc)
    means: list[float] = []
    for group in sorted(buckets):
        values = buckets[group]
        means.append(sum(values) / len(values))
    return means


def paired_bootstrap_ci(
    group_ids: list[str],
    candidate_scores: list[float],
    incumbent_scores: list[float],
    *,
    n_resamples: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapInterval:
    """Paired query-group bootstrap on candidate-minus-incumbent.

    Resamples the original grouping unit (never correlated individual
    variants). Default ``n_resamples=10000`` matches evaluation.md E3;
    tests may use a smaller count for speed.

    Percentile CI uses ``numpy.quantile(..., method="linear")`` (NumPy's
    default, Hyndman & Fan type 7): the ``alpha/2`` and ``1 - alpha/2``
    sample quantiles of the resampled group-mean deltas. This is the
    ordinary percentile bootstrap interval, not BCa.
    """
    import numpy as np

    if n_resamples < 1:
        raise ValueError("n_resamples must be >= 1")
    group_means = _group_means(group_ids, candidate_scores, incumbent_scores)
    n_groups = len(group_means)
    if n_groups == 0:
        return BootstrapInterval(
            point_estimate=0.0,
            low=0.0,
            high=0.0,
            n_groups=0,
            n_resamples=n_resamples,
            seed=seed,
        )
    point = float(sum(group_means) / n_groups)
    rng = np.random.default_rng(seed)
    samples = np.empty(n_resamples, dtype=np.float64)
    means_arr = np.asarray(group_means, dtype=np.float64)
    for i in range(n_resamples):
        draw = rng.integers(0, n_groups, size=n_groups)
        samples[i] = float(means_arr[draw].mean())
    low = float(np.quantile(samples, alpha / 2, method="linear"))
    high = float(np.quantile(samples, 1 - alpha / 2, method="linear"))
    return BootstrapInterval(
        point_estimate=point,
        low=low,
        high=high,
        n_groups=n_groups,
        n_resamples=n_resamples,
        seed=seed,
    )


@dataclass(frozen=True)
class AbstentionReport:
    """Abstention coverage / false-selection summary (evaluation.md E3)."""

    n_answerable: int
    n_no_match: int
    coverage: float | None
    false_selection_rate: float | None
    coverage_wilson_low: float | None
    false_selection_wilson_high: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        def _round(value: float | None) -> float | None:
            return None if value is None else round(value, 6)

        return {
            "n_answerable": self.n_answerable,
            "n_no_match": self.n_no_match,
            "coverage": _round(self.coverage),
            "false_selection_rate": _round(self.false_selection_rate),
            "coverage_wilson_low": _round(self.coverage_wilson_low),
            "false_selection_wilson_high": _round(self.false_selection_wilson_high),
        }


def wilson_interval(successes: int, n: int, *, z: float = 1.959963984540054) -> tuple[float, float]:
    """Two-sided Wilson score interval; also used for one-sided bounds."""
    if n <= 0:
        return (0.0, 1.0)
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2 * n)
    spread = z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)
    low = max(0.0, (centre - spread) / denom)
    high = min(1.0, (centre + spread) / denom)
    return (low, high)


def abstention_report(
    *,
    answerable_selected: list[bool],
    no_match_selected: list[bool],
) -> AbstentionReport:
    """Coverage on answerable queries; false-selection on no-match queries.

    ``answerable_selected[i]`` is True when the system selected (did not
    abstain) on an answerable query. ``no_match_selected[i]`` is True when
    the system falsely selected on a no-match / should-abstain query.
    """
    n_answerable = len(answerable_selected)
    n_no_match = len(no_match_selected)
    coverage: float | None
    false_rate: float | None
    coverage_low: float | None
    false_high: float | None
    if n_answerable == 0:
        coverage = None
        coverage_low = None
    else:
        covered = sum(1 for selected in answerable_selected if selected)
        coverage = covered / n_answerable
        coverage_low, _ = wilson_interval(covered, n_answerable)
    if n_no_match == 0:
        false_rate = None
        false_high = None
    else:
        false_pos = sum(1 for selected in no_match_selected if selected)
        false_rate = false_pos / n_no_match
        _, false_high = wilson_interval(false_pos, n_no_match)
    return AbstentionReport(
        n_answerable=n_answerable,
        n_no_match=n_no_match,
        coverage=coverage,
        false_selection_rate=false_rate,
        coverage_wilson_low=coverage_low,
        false_selection_wilson_high=false_high,
    )
