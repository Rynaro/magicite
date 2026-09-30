"""Operator CLI handlers for S14 release-evidence commands.

Thin wrappers over existing eval harness modules. No new statistics.
Never activates policies; never downloads over the network; never
persists raw runtime queries beyond locked corpus bytes already on disk.
"""

from __future__ import annotations

import json
import re
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magicite.eval.digests import sha256_path
from magicite.eval.external import (
    record_external_download,
    verify_acquired_corpus_manifest,
)
from magicite.eval.host_tasks import HostTaskArmResult, evaluate_paired_host_tasks
from magicite.eval.manifests import CorpusManifest, ExperimentManifest, Prediction, parse_experiment
from magicite.eval.metrics import (
    abstention_report,
    aggregate_ranking,
    hit_at_k,
    paired_bootstrap_ci,
)
from magicite.eval.runner import build_result_manifest, run_predictions
from magicite.eval.validate import (
    validate_experiment_corpus_seal,
    validate_experiment_data,
    validate_prediction_data,
    validate_result_data,
)
from magicite.eval.verdicts import (
    MIN_EMPIRICAL_SLICE_GROUPS,
    Verdict,
    abstention_verdict,
    holm_critical_slice_family,
    min_groups_required,
    noninferiority_verdict,
    overall_promotion_verdict,
    paired_hit_at_1_interval,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_OPERATOR_HARNESS_REASON = (
    "operator harness output is not release evidence; retain frozen incumbent"
)


def _demote_pass(verdict: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a gate ``pass`` to ``unevaluated``; the computed status stays under a non-verdict key."""
    if verdict.get("status") != "pass":
        return verdict
    return {
        **verdict,
        "status": "unevaluated",
        "harness_computed_status": "pass",
        "reason": _OPERATOR_HARNESS_REASON,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_experiment(path: Path) -> ExperimentManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_experiment_data(data)
    if errors:
        raise ValueError("invalid experiment: " + "; ".join(errors))
    return parse_experiment(data)


def _load_corpus(path: Path) -> CorpusManifest:
    corpus, errors = verify_acquired_corpus_manifest(path)
    if errors or corpus is None:
        raise ValueError("invalid corpus: " + "; ".join(errors or ["unknown"]))
    return corpus


_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract only regular files/dirs that stay inside ``dest``; refuse the whole archive otherwise."""
    root = dest.resolve()
    members = tar.getmembers()
    for member in members:
        name = member.name
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"archive member {name!r} is not a regular file or directory")
        if name.startswith(("/", "\\")) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"archive member {name!r} escapes the extraction directory")
        target = (root / name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"archive member {name!r} escapes the extraction directory")
    if sys.version_info >= (3, 12):
        tar.extractall(root, members=members, filter="data")
    else:
        tar.extractall(root, members=members)  # noqa: S202 — members validated above


def cmd_acquire_skillret(
    *,
    archive: Path,
    expected_sha256: str,
    license_name: str,
    revision: str,
    corpus_json: Path,
    output: Path,
    acquired_at: str | None = None,
) -> dict[str, Any]:
    """Record archive digest + verify/write CorpusManifest (offline)."""
    if not _SHA256_HEX.fullmatch(expected_sha256):
        raise ValueError("--expected-sha256 must be 64 hex characters")
    if not archive.is_file():
        raise FileNotFoundError(f"archive not found: {archive}")
    actual = sha256_path(archive)
    if actual != expected_sha256.lower():
        raise ValueError(
            f"archive sha256 mismatch: expected {expected_sha256.lower()}, got {actual}"
        )
    record = record_external_download(
        archive_path=archive,
        license_name=license_name,
        dataset_revision=revision,
        acquired_at=acquired_at or _now(),
    )

    corpus_source = corpus_json
    tmp_hold: tempfile.TemporaryDirectory[str] | None = None
    try:
        if not corpus_source.is_file():
            if not tarfile.is_tarfile(archive):
                raise FileNotFoundError(
                    f"corpus json {corpus_json} not found and archive is not a tar"
                )
            tmp_hold = tempfile.TemporaryDirectory()
            with tarfile.open(archive, "r:*") as tar:
                _safe_extract(tar, Path(tmp_hold.name))
            matches = list(Path(tmp_hold.name).rglob(corpus_json.name))
            if not matches:
                raise FileNotFoundError(
                    f"corpus json {corpus_json.name!r} not found in archive {archive}"
                )
            corpus_source = matches[0]

        corpus, errors = verify_acquired_corpus_manifest(corpus_source)
        if errors or corpus is None:
            raise ValueError("acquired corpus invalid: " + "; ".join(errors or ["unknown"]))
        payload = corpus.to_dict()
        payload["acquisition"] = record.to_dict()
        payload["evidence_status"] = "UNEVALUATED"
        _write_json(output, payload)
        return {"status": "UNEVALUATED", "output": str(output), "archive_sha256": actual}
    finally:
        if tmp_hold is not None:
            tmp_hold.cleanup()


def cmd_run_retrieval(
    *,
    experiment_path: Path,
    corpus_path: Path,
    split: str,
    provider: str,
    output: Path,
    policy_id: str = "dense-v1",
) -> dict[str, Any]:
    experiment = _load_experiment(experiment_path)
    corpus = _load_corpus(corpus_path)
    seal_errors = validate_experiment_corpus_seal(experiment, corpus)
    if seal_errors:
        raise ValueError("unsealed final/holdout: " + "; ".join(seal_errors))
    # Digest mismatch is also enforced inside run_predictions (hard fail).
    if corpus.content_identity_sha256 != experiment.corpus_sha256:
        raise ValueError(
            "corpus content_identity_sha256 does not match experiment.corpus_sha256 "
            f"({corpus.content_identity_sha256} != {experiment.corpus_sha256})"
        )

    all_predictions = run_predictions(experiment, corpus, policy_id=policy_id)
    split_ids = {q.query_id for q in corpus.queries if q.split == split}
    if not split_ids:
        raise ValueError(f"no queries in split {split!r}")
    predictions = [p for p in all_predictions if p.query_id in split_ids]

    for pred in predictions:
        perr = validate_prediction_data(pred.to_dict())
        if perr:
            raise ValueError("prediction invalid: " + "; ".join(perr))

    per_query: list[tuple[list[str], str]] = []
    for pred in predictions:
        q = next(x for x in corpus.queries if x.query_id == pred.query_id)
        expected = ""
        if q.relevance:
            expected = max(q.relevance.items(), key=lambda kv: kv[1])[0]
        ranked = list(pred.candidate_ids) if not pred.abstained else []
        per_query.append((ranked, expected or "__none__"))
    ranking = aggregate_ranking(per_query)
    hit_scores = [
        1.0 if expected != "__none__" and hit_at_k(ranked, expected, 1) else 0.0
        for ranked, expected in per_query
    ]
    group_ids = [
        next(x.group_id for x in corpus.queries if x.query_id == p.query_id) for p in predictions
    ]
    bootstrap = paired_bootstrap_ci(
        group_ids,
        hit_scores,
        [0.0] * len(hit_scores),
        n_resamples=min(200, 10_000),
        seed=int(experiment.seeds.get("bootstrap", 0)),
    )
    aggregates: dict[str, Any] = {
        **ranking.to_dict(),
        "provider": provider,
        "split": split,
        "policy_id": policy_id,
        "evidence_status": "UNEVALUATED",
        "bootstrap_vs_zero": bootstrap.to_dict(),
        "min_groups_required": min_groups_required(experiment.sample_power_plan),
    }
    # ResultManifest must bind the full prediction set for the experiment pin
    # (all queries) so digests remain recomputable; split aggregates are metadata.
    result = build_result_manifest(
        result_id=f"retrieval-{experiment.experiment_id}",
        experiment=experiment,
        predictions=all_predictions,
        aggregates=aggregates,
        unevaluated=[{"item": "fixture_or_offline_retrieval", "status": "UNEVALUATED"}],
    )
    rerr = validate_result_data(result.to_dict())
    if rerr:
        raise ValueError("result invalid: " + "; ".join(rerr))
    out_dir = output
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "predictions.json", [p.to_dict() for p in all_predictions])
    _write_json(out_dir / "result_manifest.json", result.to_dict())
    _write_json(
        out_dir / "split_summary.json",
        {"split": split, "n_split_predictions": len(predictions), "aggregates": aggregates},
    )
    return {
        "status": "UNEVALUATED",
        "output": str(out_dir),
        "n_predictions": len(all_predictions),
        "aggregates": aggregates,
    }


def _hit_vector(
    predictions: list[Prediction],
    corpus: CorpusManifest,
) -> tuple[list[str], list[float]]:
    group_ids: list[str] = []
    hits: list[float] = []
    by_id = {q.query_id: q for q in corpus.queries}
    for pred in predictions:
        q = by_id[pred.query_id]
        expected = ""
        if q.relevance:
            expected = max(q.relevance.items(), key=lambda kv: kv[1])[0]
        ranked = list(pred.candidate_ids) if not pred.abstained else []
        group_ids.append(q.group_id)
        hits.append(1.0 if expected and hit_at_k(ranked, expected, 1) else 0.0)
    return group_ids, hits


def cmd_run_paired_policies(
    *,
    incumbent: str,
    candidate: str,
    experiment_path: Path,
    corpus_path: Path,
    n_resamples: int,
    seed: int,
    output: Path,
) -> dict[str, Any]:
    """Paired Hit@1 verdicts over one sealed corpus.

    The candidate arm is the offline ranker re-run with a perturbed prediction
    seed (``candidate_arm_kind="harness_seed_perturbation"``), not a real
    policy rank function, so every gate ``pass`` is demoted to ``unevaluated``.
    """
    experiment = _load_experiment(experiment_path)
    corpus = _load_corpus(corpus_path)
    seal_errors = validate_experiment_corpus_seal(experiment, corpus)
    if seal_errors:
        raise ValueError("unsealed final/holdout: " + "; ".join(seal_errors))
    if corpus.content_identity_sha256 != experiment.corpus_sha256:
        raise ValueError("corpus digest does not match experiment.corpus_sha256")

    inc_preds = run_predictions(experiment, corpus, policy_id=incumbent)
    cand_seed = int(experiment.seeds.get("prediction", 0)) + 1
    cand_experiment = parse_experiment(
        {
            **experiment.to_dict(),
            "seeds": {**experiment.seeds, "prediction": cand_seed},
        }
    )
    cand_preds = run_predictions(cand_experiment, corpus, policy_id=candidate)
    group_ids, inc_hits = _hit_vector(inc_preds, corpus)
    _, cand_hits = _hit_vector(cand_preds, corpus)
    interval = paired_hit_at_1_interval(
        group_ids,
        cand_hits,
        inc_hits,
        n_resamples=n_resamples,
        seed=seed,
    )
    min_groups = min_groups_required(experiment.sample_power_plan)
    ni = noninferiority_verdict(interval, min_groups=min_groups)
    if interval.n_groups < MIN_EMPIRICAL_SLICE_GROUPS:
        slices = Verdict(
            gate="critical_slice_holm",
            status="inconclusive",
            reason=(
                f"insufficient groups for critical slices "
                f"({interval.n_groups} < {MIN_EMPIRICAL_SLICE_GROUPS})"
            ),
            details={"n_groups": interval.n_groups},
        )
    else:
        slices = holm_critical_slice_family([("all", interval)])
    abst = abstention_verdict(
        coverage_wilson_low=None,
        false_selection_wilson_high=None,
        n_answerable=0,
        n_no_match=0,
    )
    overall = overall_promotion_verdict(
        noninferiority=ni,
        critical_slices=slices,
        abstention=abst,
        hybrid_vs_incumbent_evaluated=True,
    )
    payload: dict[str, Any] = {
        "status": "UNEVALUATED",
        "evidence_status": "UNEVALUATED",
        "incumbent": incumbent,
        "candidate": candidate,
        "candidate_arm_kind": "harness_seed_perturbation",
        "interval": interval.to_dict(),
        "noninferiority": _demote_pass(ni.to_dict()),
        "critical_slices": _demote_pass(slices.to_dict()),
        "abstention": _demote_pass(abst.to_dict()),
        "overall_promotion": _demote_pass(overall.to_dict()),
        "policy_activation": "not_called",
        "default_remains_simple_incumbent": True,
        "n_groups": interval.n_groups,
        "min_groups_required": min_groups,
        "note": (
            "Offline deterministic ranking arms are harness wiring only; "
            "official hybrid-vs-dense evidence remains UNEVALUATED until a "
            "locked final corpus is run on the reference runner. "
            "policy_store.activate() is never called."
        ),
    }
    _write_json(output, payload)
    return payload


def cmd_run_abstention_gate(
    *,
    calibration_split: str,
    final_split: str,
    experiment_path: Path,
    corpus_path: Path,
    output: Path,
) -> dict[str, Any]:
    experiment = _load_experiment(experiment_path)
    corpus = _load_corpus(corpus_path)
    seal_errors = validate_experiment_corpus_seal(experiment, corpus)
    if seal_errors:
        raise ValueError("unsealed final/holdout: " + "; ".join(seal_errors))
    if corpus.content_identity_sha256 != experiment.corpus_sha256:
        raise ValueError("corpus digest does not match experiment.corpus_sha256")

    cal_n = sum(1 for q in corpus.queries if q.split == calibration_split)
    final_queries = [q for q in corpus.queries if q.split == final_split]
    if not final_queries:
        raise ValueError(f"no queries in final split {final_split!r}")

    predictions = run_predictions(experiment, corpus, policy_id="dense-v1")
    by_id = {p.query_id: p for p in predictions}
    answerable_selected: list[bool] = []
    no_match_selected: list[bool] = []
    for q in final_queries:
        pred = by_id[q.query_id]
        is_no_match = bool(q.compatibility_context.get("no_match")) or (
            not q.relevance and bool(q.negative_ids)
        )
        selected = not pred.abstained
        if is_no_match:
            no_match_selected.append(selected)
        else:
            answerable_selected.append(selected)

    report = abstention_report(
        answerable_selected=answerable_selected,
        no_match_selected=no_match_selected,
    )
    min_groups = min_groups_required(experiment.sample_power_plan)
    verdict = abstention_verdict(
        coverage_wilson_low=report.coverage_wilson_low,
        false_selection_wilson_high=report.false_selection_wilson_high,
        n_answerable=report.n_answerable,
        n_no_match=report.n_no_match,
        min_answerable=min_groups,
        min_no_match=min_groups,
    )
    payload = {
        "status": "UNEVALUATED",
        "evidence_status": "UNEVALUATED",
        "calibration_split": calibration_split,
        "final_split": final_split,
        "calibration_query_count": cal_n,
        "abstention_report": report.to_dict(),
        "verdict": _demote_pass(verdict.to_dict()),
        "min_groups_required": min_groups,
        "note": (
            "Fixture/offline abstention gate cannot satisfy release promotion; "
            "empirical bounds on a sealed official final split remain UNEVALUATED."
        ),
    }
    _write_json(output, payload)
    return payload


def cmd_run_host_tasks(
    *,
    corpus_path: Path,
    arms: list[str],
    n_resamples: int,
    seed: int,
    output: Path,
) -> dict[str, Any]:
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "arms" not in data:
        raise ValueError("host-task corpus must be an object with an 'arms' array")
    rows: list[HostTaskArmResult] = []
    allowed = set(arms)
    for item in data["arms"]:
        arm = str(item["arm"])
        if arm not in allowed:
            continue
        rows.append(
            HostTaskArmResult(
                task_id=str(item["task_id"]),
                group_id=str(item["group_id"]),
                arm=arm,  # type: ignore[arg-type]
                outcome=str(item["outcome"]),  # type: ignore[arg-type]
                verifier_id=str(item["verifier_id"]),
                verifier_artifact_digest=str(item["verifier_artifact_digest"]),
                details=str(item.get("details") or ""),
            )
        )
    if not rows:
        raise ValueError("no host-task arms matched requested --arms filter")
    report = evaluate_paired_host_tasks(rows, n_resamples=n_resamples, seed=seed)
    payload = report.to_dict()
    if payload.get("usefulness_status") == "pass":
        payload["usefulness_status"] = "unevaluated"
        payload["harness_computed_usefulness_status"] = "pass"
        payload["usefulness_reason"] = _OPERATOR_HARNESS_REASON
    payload["status"] = "UNEVALUATED"
    payload["evidence_status"] = "UNEVALUATED"
    payload["note"] = (
        "Ingests precomputed host-verifier outcomes only; Magicite does not "
        "execute host tasks. GA usefulness remains UNEVALUATED without an "
        "independently authored ≥30-group corpus."
    )
    _write_json(output, payload)
    return payload


__all__ = [
    "cmd_acquire_skillret",
    "cmd_run_abstention_gate",
    "cmd_run_host_tasks",
    "cmd_run_paired_policies",
    "cmd_run_retrieval",
]
