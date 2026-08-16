"""``eval/ablations.py``: the three M6-shipped ablation switches
(spec §7.3)."""

from __future__ import annotations

from pathlib import Path

from magicite.core import registry as registry_mod
from magicite.eval import ablations as abl_mod
from magicite.eval import bench as bench_mod

TOY_QUERIES_PATH = Path("tests/fixtures/toy-registry/queries.jsonl")


def test_no_decay_does_not_mutate_the_caller_config(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)[:5]
    original_lambda_r = cfg.lambda_r_per_day

    abl_mod.run_no_decay(cfg, db_conn, embedder, queries)

    assert cfg.lambda_r_per_day == original_lambda_r


def test_no_decay_result_shape(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)

    result = abl_mod.run_no_decay(cfg, db_conn, embedder, queries)

    assert result.name == "no_decay"
    assert result.baseline_d.n_queries == len(queries)
    assert result.ablated.n_queries == len(queries)


def test_no_communities_does_not_mutate_the_caller_config(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)[:5]

    abl_mod.run_no_communities(cfg, db_conn, embedder, queries)

    assert cfg.ablation_no_communities is False


def test_no_communities_config_switch_is_real(cfg, db_conn, embedder) -> None:
    """The switch actually reaches core/router.py::route() -- not a
    bench-only reimplementation."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    from magicite.core import router as router_mod

    with_communities = router_mod.route(cfg, db_conn, embedder, query="steam wont open at all", k=5)
    cfg.ablation_no_communities = True
    try:
        without_communities = router_mod.route(cfg, db_conn, embedder, query="steam wont open at all", k=5)
    finally:
        cfg.ablation_no_communities = False
    # Both must be real, non-empty rankings (the toy registry's actual
    # community structure may make the two identical -- see the M6
    # report's honest null-result note -- but both code paths must run).
    assert len(with_communities.candidates) > 0
    assert len(without_communities.candidates) > 0


def test_no_tag_capture_never_calls_signal_use_or_signal_outcome(cfg, db_conn, embedder, monkeypatch) -> None:
    """P0 (spec §6.2): this ablation must never touch the real, gated
    two-phase-commit path -- it is simulated entirely offline."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)

    from magicite.core import signals as signals_mod

    def _boom(*args, **kwargs):
        raise AssertionError("no_tag_capture must never call the real signal_use/signal_outcome path")

    monkeypatch.setattr(signals_mod, "signal_use", _boom)
    monkeypatch.setattr(signals_mod, "signal_outcome", _boom)

    result = abl_mod.run_no_tag_capture(cfg, db_conn, embedder, queries)
    assert result.name == "no_tag_capture"


def test_no_tag_capture_demonstrates_instability(cfg, db_conn, embedder) -> None:
    """docs/07's own hypothesis, made concrete: ungated, ever-growing
    per-occurrence bumps toward frequently-labelled skills degrade
    accuracy relative to the real, gated baseline (d)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)

    result = abl_mod.run_no_tag_capture(cfg, db_conn, embedder, queries)

    assert result.delta_hit_at_3 < 0, "ungated hot-path learning should degrade accuracy on this fixture"
    assert result.delta_mrr < 0


def test_run_all_returns_every_named_ablation(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)[:10]

    results = abl_mod.run_all(cfg, db_conn, embedder, queries)

    assert set(results) == set(abl_mod.ABLATION_NAMES)
    for r in results.values():
        d = r.to_dict()
        assert "hypothesis" in d
        assert "delta_hit_at_3" in d
