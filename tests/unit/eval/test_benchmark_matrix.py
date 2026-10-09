"""Local quality attachment preserves actual matrix provenance and nonqualification."""

from copy import deepcopy

import pytest
from tests.unit.eval import test_protected_benchmark as fixtures

from magicite.eval import protected_benchmark as benchmark
from magicite.eval.digests import sha256_json


def test_bound_quality_attachment_rejects_incomplete_or_incompatible_results(
    tmp_path, cfg, db_conn, embedder
):
    run = fixtures.run.__wrapped__(tmp_path, cfg, db_conn, embedder)
    _, actual, cache, root, commitment, _ = fixtures.complete.__wrapped__(run)
    report = benchmark.final(commitment, root / "result.json", actual, model_cache=cache)
    reference = benchmark.quality_reference(
        report, report["profile"], observed_model=report["model_manifest"]
    )
    assert reference["sha256"] == sha256_json(report) and reference["qualifying"] is False
    for mutation in ("empty", "missing", "foreign", "profile", "environment", "model", "source"):
        altered = deepcopy(report)
        matrix = deepcopy(report["profile"])
        model = deepcopy(report["model_manifest"])
        if mutation == "empty":
            altered["arms"] = {}
            altered["trace_digest"] = sha256_json(altered["arms"])
        elif mutation == "missing":
            altered["arms"].pop("dense-v1")
            altered["trace_digest"] = sha256_json(altered["arms"])
        elif mutation == "foreign":
            altered["arms"]["dense-v1"][0]["query_id"] = "foreign"
            altered["trace_digest"] = sha256_json(altered["arms"])
        elif mutation == "profile":
            matrix["profile"]["profile_id"] = "small-100"
        elif mutation == "environment":
            matrix["fingerprint"]["platform"] = "other"
        elif mutation == "model":
            model["files"]["model_optimized.onnx"] = "a" * 64
        else:
            altered["source_commit"] = "1" * 40
        with pytest.raises(ValueError):
            benchmark.quality_reference(altered, matrix, observed_model=model)
    assert report["E6"] == report["GA"] == "UNEVALUATED"
