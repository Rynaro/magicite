"""AC-029: GIVEN the labelled toy benchmark WHEN
``magicite-bench --baseline b --baseline d`` runs THEN it SHALL emit
Hit@1, Hit@3, Hit@5, MRR and Plan F1 for both baselines."""

from __future__ import annotations

import json

from click.testing import CliRunner

from magicite.core import registry as registry_mod
from magicite.eval import bench as bench_mod

TOY_QUERIES_PATH = "tests/fixtures/toy-registry/queries.jsonl"


def test_baseline_metrics_emitted(cfg, db_conn, embedder) -> None:
    """AC-029, exercised at the function level (the same code the
    ``magicite-bench`` CLI calls -- see
    ``test_baseline_metrics_emitted_via_cli`` below for the literal
    ``magicite-bench --baseline b --baseline d`` invocation)."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    from pathlib import Path

    queries = bench_mod.load_queries(Path(TOY_QUERIES_PATH))
    assert len(queries) >= 40  # spec §7.1's "40 labelled queries" fixture

    report = bench_mod.run_bench(cfg, db_conn, embedder, queries=queries, baselines=["b", "d"])

    assert set(report.baselines) == {"b", "d"}
    for name in ("b", "d"):
        baseline = report.baselines[name]
        d = baseline.to_dict()
        for metric in ("hit_at_1", "hit_at_3", "hit_at_5", "mrr"):
            assert metric in d
            assert 0.0 <= d[metric] <= 1.0
        assert "plan_f1" in d
        assert set(d["plan_f1"]) == {"precision", "recall", "f1", "order_correct"}
        assert baseline.ranking.n_queries == len(queries)


def test_baseline_metrics_emitted_via_cli(cfg, embedder) -> None:
    """The literal AC-029 invocation shape: ``magicite-bench --baseline b
    --baseline d``, run in-process through Click's CliRunner (no
    subprocess) against a project root the fixture prepared."""
    from magicite.storage import db as db_mod

    conn = db_mod.connect(cfg.db_path)
    registry_mod.register(cfg, conn, embedder, path=".spectra/engrams")
    conn.close()

    runner = CliRunner()
    result = runner.invoke(
        bench_mod.cli,
        [
            "--project-root", str(cfg.project_root),
            "--queries", TOY_QUERIES_PATH,
            "--baseline", "b",
            "--baseline", "d",
        ],
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload["baselines"]) == {"b", "d"}
    for name in ("b", "d"):
        d = payload["baselines"][name]
        for metric in ("hit_at_1", "hit_at_3", "hit_at_5", "mrr"):
            assert metric in d


def test_all_four_baselines_run_and_report_real_numbers(cfg, db_conn, embedder) -> None:
    """The falsifiability bar (R10, mission directive): every one of the
    four docs/07 baselines (a-d) must actually run and emit a distinct,
    real report -- not a stub, not a hard-coded pass/fail."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    from pathlib import Path

    queries = bench_mod.load_queries(Path(TOY_QUERIES_PATH))

    report = bench_mod.run_bench(cfg, db_conn, embedder, queries=queries)

    assert set(report.baselines) == {"a", "b", "c", "d"}
    assert report.registry_size == 7
    for baseline in report.baselines.values():
        assert baseline.ranking.n_queries == len(queries)
