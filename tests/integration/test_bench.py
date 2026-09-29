"""AC-029: GIVEN the labelled toy benchmark WHEN
``magicite-bench --baseline b --baseline d --allow-circular-diagnostic-gold``
runs THEN it SHALL emit Hit@1, Hit@3, Hit@5, MRR and Plan F1 for both
baselines. Circular expand()-as-gold requires the explicit switch; omit it
only when independent ``expected_plans`` are supplied via the Python API."""

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
    ``magicite-bench --baseline b --baseline d --allow-circular-diagnostic-gold``
    invocation)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    from pathlib import Path

    queries = bench_mod.load_queries(Path(TOY_QUERIES_PATH))
    assert len(queries) >= 40  # spec §7.1's "40 labelled queries" fixture

    report = bench_mod.run_bench(
        cfg,
        db_conn,
        embedder,
        queries=queries,
        baselines=["b", "d"],
        allow_circular_diagnostic_gold=True,
    )

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
    """AC-029 CLI shape with explicit diagnostic gold:
    ``magicite-bench --baseline b --baseline d --allow-circular-diagnostic-gold``.
    """
    from magicite.storage import db as db_mod

    conn = db_mod.connect(cfg.db_path)
    registry_mod.register(cfg, conn, embedder, path=".magicite/engrams")
    conn.close()

    runner = CliRunner()
    result = runner.invoke(
        bench_mod.cli,
        [
            "--project-root",
            str(cfg.project_root),
            "--queries",
            TOY_QUERIES_PATH,
            "--baseline",
            "b",
            "--baseline",
            "d",
            "--allow-circular-diagnostic-gold",
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
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    from pathlib import Path

    queries = bench_mod.load_queries(Path(TOY_QUERIES_PATH))

    report = bench_mod.run_bench(cfg, db_conn, embedder, queries=queries, allow_circular_diagnostic_gold=True)

    assert set(report.baselines) == {"a", "b", "c", "d"}
    assert report.registry_size == 7
    for baseline in report.baselines.values():
        assert baseline.ranking.n_queries == len(queries)
        # Legacy expand()-as-gold Plan F1 remains available but labeled.
        assert baseline.plan_f1_status == "deprecated_diagnostic_circular_gold"
        assert baseline.to_dict()["plan_f1_status"] == "deprecated_diagnostic_circular_gold"


def test_structural_gold_loading_rejects_production_planner(monkeypatch) -> None:
    """AC-S01-04: expected plans originate only from corpus annotations.

    A production-planner sentinel must not be invoked while gold labels
    are loaded from an independent structural corpus.
    """
    from magicite.core import composition as composition_mod
    from magicite.eval import gold as gold_mod

    def _boom(*_args, **_kwargs):
        raise AssertionError("production planner must not run during gold loading")

    monkeypatch.setattr(composition_mod, "expand", _boom)
    # Even if something imports expand into gold (it must not), fail loudly.
    monkeypatch.setattr(gold_mod, "expand", _boom, raising=False)

    gold = gold_mod.load_structural_gold("docs/evaluation/composition-v0.3.json")
    plans = gold.expected_plans_by_case_id()
    assert plans["compose-001"][0] == "build-package"
    assert gold.label_policy["production_expansion_used"] is False
    for case in gold.cases:
        assert case.label_provenance.get("production_expansion_used") is False
