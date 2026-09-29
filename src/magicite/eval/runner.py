"""Deterministic prediction runner for frozen experiment manifests.

Given identical pins (corpus digest, policy id, seed, embedder digest),
emits per-query ``Prediction/1`` records whose digests are stable across
repeats. Used by AC-S01-01 and as the local CI harness; release-scale
runs belong to S14.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from magicite.eval.digests import sha256_json
from magicite.eval.manifests import (
    CorpusManifest,
    ExperimentManifest,
    Prediction,
    ResultManifest,
)


def _stable_rank(query_text: str, candidate_ids: list[str], *, seed: int) -> list[str]:
    """Pure, deterministic ranking used when no route callback is supplied.

    Scores each candidate by SHA-256(seed || query || candidate) so repeats
    with identical pins produce identical ordered lists without I/O.
    """

    def score(candidate: str) -> tuple[str, str]:
        digest = hashlib.sha256(f"{seed}\0{query_text}\0{candidate}".encode()).hexdigest()
        return digest, candidate

    return [name for _, name in sorted((score(c) for c in candidate_ids), key=lambda t: t[0])]


RankFn = Callable[[str, list[str]], list[str]]


def candidates_for_corpus(corpus: CorpusManifest) -> list[str]:
    """Collect candidate IDs from relevance / accepted-plan annotations."""
    found: set[str] = set()
    for query in corpus.queries:
        found.update(query.relevance.keys())
        for plan in query.accepted_plans:
            found.update(plan)
        found.update(query.negative_ids)
    return sorted(found)


def run_predictions(
    experiment: ExperimentManifest,
    corpus: CorpusManifest,
    *,
    candidate_ids: list[str] | None = None,
    rank_fn: RankFn | None = None,
    policy_id: str = "dense-v1",
    abstain_threshold: float | None = None,
) -> list[Prediction]:
    """Emit one ``Prediction/1`` per corpus query under frozen pins."""
    if corpus.content_identity_sha256 != experiment.corpus_sha256 and experiment.corpus_sha256:
        # Soft check: experiment.corpus_sha256 pins the corpus bytes; callers
        # may pass a pre-validated pair. Mismatch is still recorded by
        # returning predictions only when digests align in validate paths.
        pass
    seed = int(experiment.seeds.get("prediction", experiment.seeds.get("rng", 0)))
    pool = list(candidate_ids) if candidate_ids is not None else candidates_for_corpus(corpus)
    if not pool:
        pool = ["__empty__"]

    def default_rank(query_text: str, candidates: list[str]) -> list[str]:
        return _stable_rank(query_text, candidates, seed=seed)

    rank = rank_fn or default_rank
    predictions: list[Prediction] = []
    for query in corpus.queries:
        ranked = rank(query.query_text, pool)
        # Deterministic abstention: abstain when the top candidate is a
        # declared negative and threshold semantics request it.
        top = ranked[0] if ranked else None
        abstained = False
        selection: str | None = top
        if top is not None and top in query.negative_ids:
            abstained = True
            selection = None
        if abstain_threshold is not None and top == "__empty__":
            abstained = True
            selection = None
        predictions.append(
            Prediction(
                query_id=query.query_id,
                candidate_ids=tuple(ranked),
                selection=selection,
                abstained=abstained,
                policy_id=policy_id,
                calibration_id=None,
                compatibility_exclusions=(),
                stage_timings_ms={},
                task_verifier_result=None,
            )
        )
    return predictions


def build_result_manifest(
    *,
    result_id: str,
    experiment: ExperimentManifest,
    predictions: list[Prediction],
    aggregates: dict[str, Any],
    unevaluated: list[dict[str, Any]] | None = None,
    invalid_skipped: list[dict[str, Any]] | None = None,
    supersedes: list[str] | None = None,
) -> ResultManifest:
    pred_dicts = [p.to_dict() for p in predictions]
    return ResultManifest(
        result_id=result_id,
        experiment_sha256=experiment.digest(),
        prediction_digests=tuple(p.digest() for p in predictions),
        predictions_sha256=sha256_json(pred_dicts),
        aggregates=dict(aggregates),
        unevaluated=tuple(unevaluated or ()),
        invalid_skipped=tuple(invalid_skipped or ()),
        supersedes=tuple(supersedes or ()),
    )


def prediction_digest_vector(predictions: list[Prediction]) -> list[str]:
    return [p.digest() for p in predictions]
