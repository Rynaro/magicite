"""``eval/bench.py``: baselines a-d, at the unit level."""

from __future__ import annotations

from pathlib import Path

from magicite.core import registry as registry_mod
from magicite.eval import bench as bench_mod

TOY_QUERIES_PATH = Path("tests/fixtures/toy-registry/queries.jsonl")


def test_load_queries_reads_the_toy_fixture() -> None:
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)
    assert len(queries) >= 40
    assert all(q.query and q.expected_top1 for q in queries)


def test_baseline_a_never_touches_embeddings(cfg, db_conn, embedder) -> None:
    """Baseline (a) is lexical-only (CR-3, module docstring): it must
    resolve candidates even for an engram with no eph_embedding row at
    all (register() always embeds in this codebase, but the point of (a)
    is that it *doesn't need to*)."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    db_conn.execute("DELETE FROM eph_embedding")
    ranked = bench_mod._baseline_a_rank(db_conn, "rollback proton for a steam game")
    assert "proton-ge-proton-downgrade" in ranked


def test_baseline_a_ranks_the_toy_registry_query(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ranked = bench_mod._baseline_a_rank(db_conn, "rollback proton for a steam game")
    assert ranked[0] == "proton-ge-proton-downgrade"


def test_baseline_b_ranks_by_cosine(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ranked = bench_mod._baseline_b_rank(db_conn, embedder, "rollback proton for a steam game")
    assert len(ranked) == 7


def test_baseline_c_uses_graph_structure(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    ranked = bench_mod._baseline_c_rank(cfg, db_conn, embedder, "rollback proton for a steam game")
    assert len(ranked) == 7


def test_baseline_d_is_the_real_route(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = [
        bench_mod.LabelledQuery(
            query="rollback proton for a steam game", expected_top1="proton-ge-proton-downgrade"
        )
    ]
    report = bench_mod.run_baseline(cfg, db_conn, embedder, "d", queries)
    assert report.ranking.hit_at_1 == 1.0


def test_run_baseline_rejects_unknown_name(cfg, db_conn, embedder) -> None:
    import pytest

    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    with pytest.raises(ValueError):
        bench_mod.run_baseline(cfg, db_conn, embedder, "z", [])


def test_plan_f1_reflects_the_declared_needs_edge(cfg, db_conn, embedder) -> None:
    """proton-ge-proton-downgrade declares `needs: [steam-prefix-access]`
    (AC-012's own fixture) -- the derived ground-truth plan for any query
    labelled to it must be the two-step closure, and baseline (d) (which
    resolves the same skill) should match it."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    expected_plan = bench_mod._expand_plan(cfg, db_conn, "proton-ge-proton-downgrade")
    assert expected_plan == ["steam-prefix-access", "proton-ge-proton-downgrade"]


def test_run_bench_defaults_to_all_four_baselines(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)[:5]
    report = bench_mod.run_bench(cfg, db_conn, embedder, queries=queries)
    assert set(report.baselines) == set(bench_mod.BASELINE_NAMES)
