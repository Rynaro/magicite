"""``eval/bench.py``: baselines a-d, at the unit level."""

from __future__ import annotations

from pathlib import Path

from magicite.core import registry as registry_mod
from magicite.eval import bench as bench_mod
from magicite.eval.envelopes import completeness_errors, validate_envelope
from magicite.eval.profiles import (
    PROFILE_CI_SMOKE,
    PROFILE_SUPPORTED_10K,
    REQUIRED_CACHE_STATES,
    REQUIRED_FINGERPRINT_FIELDS,
    build_profile_result_skeleton,
    profile_manifest_errors,
)

TOY_QUERIES_PATH = Path("tests/fixtures/toy-registry/queries.jsonl")


def test_load_queries_reads_the_toy_fixture() -> None:
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)
    assert len(queries) >= 40
    assert all(q.query and q.expected_top1 for q in queries)


def test_profile_manifest() -> None:
    """AC-S14-01: declared profile results identify every cache state + fingerprint."""
    result = build_profile_result_skeleton(
        PROFILE_CI_SMOKE,
        provider="hashing",
        model_name="hashing-v1",
        model_digest="synthetic-digest",
        runner_label="unit-test",
        project_root=Path("."),
    )
    # Skeleton already names every required cache state and fingerprint field.
    assert set(result["cache_states"]) == set(REQUIRED_CACHE_STATES)
    for state in REQUIRED_CACHE_STATES:
        assert result["cache_states"][state]["identified"] is True
    for field_name in REQUIRED_FINGERPRINT_FIELDS:
        assert field_name in result["fingerprint"]
        assert result["fingerprint"][field_name] not in (None, "")

    assert profile_manifest_errors(result) == []

    # Incomplete fingerprint must fail the shared completeness gate.
    broken = dict(result)
    broken["fingerprint"] = dict(result["fingerprint"])
    del broken["fingerprint"]["model_digest"]
    assert any("model_digest" in err for err in profile_manifest_errors(broken))

    missing_cache = dict(result)
    missing_cache["cache_states"] = dict(result["cache_states"])
    del missing_cache["cache_states"]["hot_query_cache"]
    assert any("hot_query_cache" in err for err in profile_manifest_errors(missing_cache))


def test_envelope_completeness_vs_budget_modes() -> None:
    """AC-S14-02: shared CI completeness ≠ dedicated budget validation."""
    result = build_profile_result_skeleton(
        PROFILE_SUPPORTED_10K,
        provider="hashing",
        model_name="hashing-v1",
        model_digest="synthetic",
        runner_label="unit-test",
        project_root=Path("."),
    )
    result["measurements"] = {
        "warm_route_p50_ms": 10.0,
        "warm_route_p95_ms": 20.0,
        "warm_route_p99_ms": 30.0,
        "process_rss_gib": 0.2,
        "index_gib": 0.1,
        "cold_ready_s": 1.0,
        "index_build_s": 2.0,
        "index_build_peak_rss_gib": 0.3,
        "payload_tokens": None,
        "cache_hit_rates": {},
        "truncations_fallbacks": [],
    }
    assert completeness_errors(result) == []

    # Hashing provider cannot satisfy production budget mode.
    budget_hashing = validate_envelope(result, PROFILE_SUPPORTED_10K, mode="budget")
    assert budget_hashing.ok is False
    assert any("production" in e for e in budget_hashing.errors)

    # Production provider over budget fails dedicated gate.
    prod = dict(result)
    prod["fingerprint"] = dict(result["fingerprint"])
    prod["fingerprint"]["provider"] = "production"
    prod["measurements"] = dict(result["measurements"])
    prod["measurements"]["warm_route_p95_ms"] = 5000.0  # > 1000 ms budget
    budget_prod = validate_envelope(prod, PROFILE_SUPPORTED_10K, mode="budget")
    assert budget_prod.ok is False
    assert any("warm_route_p95_ms" in e for e in budget_prod.errors)

    # Completeness still passes for the over-budget production result.
    assert validate_envelope(prod, PROFILE_SUPPORTED_10K, mode="completeness").ok is True


def test_baseline_a_never_touches_embeddings(cfg, db_conn, embedder) -> None:
    """Baseline (a) is lexical-only (CR-3, module docstring): it must
    resolve candidates even for an engram with no eph_embedding row at
    all (register() always embeds in this codebase, but the point of (a)
    is that it *doesn't need to*)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    db_conn.execute("DELETE FROM eph_embedding")
    ranked = bench_mod._baseline_a_rank(db_conn, "rollback proton for a steam game")
    assert "proton-ge-proton-downgrade" in ranked


def test_baseline_a_ranks_the_toy_registry_query(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ranked = bench_mod._baseline_a_rank(db_conn, "rollback proton for a steam game")
    assert ranked[0] == "proton-ge-proton-downgrade"


def test_baseline_b_ranks_by_cosine(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ranked = bench_mod._baseline_b_rank(db_conn, embedder, "rollback proton for a steam game")
    assert len(ranked) == 7


def test_baseline_c_uses_graph_structure(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    ranked = bench_mod._baseline_c_rank(cfg, db_conn, embedder, "rollback proton for a steam game")
    assert len(ranked) == 7


def test_baseline_c_seed_parity(cfg, db_conn, embedder, monkeypatch) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    calls: list[int] = []
    original = bench_mod.activation_mod.select_seed_cosines

    def recording_select(node_ids, cosine, *, k):
        calls.append(k)
        return original(node_ids, cosine, k=k)

    monkeypatch.setattr(bench_mod.activation_mod, "select_seed_cosines", recording_select)
    from magicite.core import router as router_mod
    from magicite.core import routing_policy as policy_mod

    # S00 call-site: seed selection is part of experimental adaptive-blend.
    cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    router_mod.route(cfg, db_conn, embedder, query="rollback proton", k=3)
    bench_mod._baseline_c_rank(cfg, db_conn, embedder, "rollback proton", k=3)
    assert calls == [3, 3]


def test_baseline_c_inhibition_parity(cfg, db_conn, embedder, monkeypatch) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    captured: list[list[tuple[str, str, float]]] = []
    original = bench_mod.activation_mod.spread_activation

    def recording_spread(node_ids, seeds, positive_edges, inhibition_edges, **kwargs):
        captured.append(inhibition_edges)
        return original(node_ids, seeds, positive_edges, inhibition_edges, **kwargs)

    monkeypatch.setattr(bench_mod.activation_mod, "spread_activation", recording_spread)
    bench_mod._baseline_c_rank(cfg, db_conn, embedder, "rollback proton")
    assert captured
    assert captured[0]
    assert all(weight == cfg.declared_edge_strength for _, _, weight in captured[0])


def test_baseline_d_is_the_real_route(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = [
        bench_mod.LabelledQuery(
            query="rollback proton for a steam game", expected_top1="proton-ge-proton-downgrade"
        )
    ]
    report = bench_mod.run_baseline(
        cfg, db_conn, embedder, "d", queries, allow_circular_diagnostic_gold=True
    )
    assert report.ranking.hit_at_1 == 1.0


def test_run_baseline_rejects_unknown_name(cfg, db_conn, embedder) -> None:
    import pytest

    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    with pytest.raises(ValueError):
        bench_mod.run_baseline(cfg, db_conn, embedder, "z", [], allow_circular_diagnostic_gold=True)


def test_plan_f1_reflects_the_declared_needs_edge(cfg, db_conn, embedder) -> None:
    """proton-ge-proton-downgrade declares `needs: [steam-prefix-access]`
    (AC-012's own fixture) -- the derived ground-truth plan for any query
    labelled to it must be the two-step closure, and baseline (d) (which
    resolves the same skill) should match it."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    expected_plan = bench_mod._expand_plan(cfg, db_conn, "proton-ge-proton-downgrade")
    assert expected_plan == ["steam-prefix-access", "proton-ge-proton-downgrade"]


def test_run_bench_defaults_to_all_four_baselines(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)
    queries = bench_mod.load_queries(TOY_QUERIES_PATH)[:5]
    report = bench_mod.run_bench(
        cfg, db_conn, embedder, queries=queries, allow_circular_diagnostic_gold=True
    )
    assert set(report.baselines) == set(bench_mod.BASELINE_NAMES)


def test_bench_against_empty_registry_does_not_report_a_vacuous_perfect_plan_f1(
    cfg, db_conn, embedder
) -> None:
    """M7 close-out item #3 (bug, reproduced end-to-end): running
    ``magicite-bench`` against an *empty* registry must not report
    plan_f1={precision:1.0, recall:1.0, f1:1.0} -- a perfect composition
    score with nothing to plan is indistinguishable from real success and
    would silently flatter the scale benchmark. hit_at_k is correctly 0.0
    here (nothing to ever be a hit); plan_f1 must now be equally honest:
    null/None, with an explicit n_evaluated=0 count, not a fabricated 1.0.
    Registry is deliberately left unregistered/unsynced -- zero engrams.
    """
    queries = [
        bench_mod.LabelledQuery(
            query="rollback proton for a steam game", expected_top1="anything-at-all"
        ),
        bench_mod.LabelledQuery(query="fix wine prefix", expected_top1="also-does-not-exist"),
    ]
    report = bench_mod.run_baseline(
        cfg, db_conn, embedder, "d", queries, allow_circular_diagnostic_gold=True
    )

    assert report.ranking.hit_at_1 == 0.0
    assert report.ranking.hit_at_3 == 0.0
    assert report.ranking.hit_at_5 == 0.0
    assert report.ranking.mrr == 0.0

    # The bug: this used to be precision=recall=f1=1.0, order_correct=True.
    assert report.plan_f1.precision is None
    assert report.plan_f1.recall is None
    assert report.plan_f1.f1 is None
    assert report.plan_f1.n_evaluated == 0
    assert report.plan_f1.n_total == len(queries)

    d = report.to_dict()
    assert d["plan_f1"] == {
        "precision": None,
        "recall": None,
        "f1": None,
        "order_correct": False,
    }
    assert d["plan_f1_n_evaluated"] == 0
    assert d["plan_f1_n_total"] == len(queries)
