from __future__ import annotations

import numpy as np

from magicite.core import activation as activation_mod


def test_build_graph_row_normalizes_per_source() -> None:
    node_ids = ["a", "b", "c"]
    edges = [("a", "b", 2.0), ("a", "c", 2.0), ("b", "c", 1.0)]
    graph = activation_mod.build_graph(node_ids, edges)
    total_from_a = graph.weight[graph.src_idx == graph.node_index["a"]].sum()
    assert total_from_a == 1.0


def test_build_graph_drops_edges_outside_universe_and_nonpositive_weight() -> None:
    node_ids = ["a", "b"]
    edges = [("a", "z", 5.0), ("a", "b", 0.0), ("a", "b", -1.0)]
    graph = activation_mod.build_graph(node_ids, edges)
    assert graph.src_idx.size == 0


def test_softmax_personalization_sums_to_one_over_seeds() -> None:
    node_ids = ["a", "b", "c"]
    p = activation_mod.softmax_personalization(node_ids, {"a": 0.9, "b": 0.1}, temperature=0.07)
    assert abs(p.sum() - 1.0) < 1e-9
    assert p[node_ids.index("c")] == 0.0
    assert p[node_ids.index("a")] > p[node_ids.index("b")]


def test_softmax_personalization_empty_seeds_returns_zero_vector() -> None:
    p = activation_mod.softmax_personalization(["a", "b"], {}, temperature=0.07)
    assert np.all(p == 0.0)


def test_personalized_pagerank_propagates_along_edges() -> None:
    node_ids = ["seed", "neighbor", "unrelated"]
    # `seed` fully activated, strongly connected only to `neighbor`.
    p = activation_mod.softmax_personalization(node_ids, {"seed": 1.0}, temperature=0.07)
    graph = activation_mod.build_graph(node_ids, [("seed", "neighbor", 1.0)])
    a = activation_mod.personalized_pagerank(graph, p, restart=0.15, max_iter=20, tol=1e-6)
    idx = {n: i for i, n in enumerate(node_ids)}
    assert a[idx["neighbor"]] > a[idx["unrelated"]]
    assert a[idx["seed"]] > 0  # restart mass keeps the seed itself active


def test_personalized_pagerank_no_edges_reduces_to_personalization() -> None:
    node_ids = ["a", "b"]
    p = activation_mod.softmax_personalization(node_ids, {"a": 1.0}, temperature=0.07)
    graph = activation_mod.build_graph(node_ids, [])
    a = activation_mod.personalized_pagerank(graph, p, restart=0.15, max_iter=20, tol=1e-6)
    assert np.allclose(a, p)


def test_apply_inhibition_lowers_target_only_when_inhibitor_is_active() -> None:
    node_ids = ["inhibitor", "target", "bystander"]
    a = np.array([1.0, 1.0, 1.0])
    out = activation_mod.apply_inhibition(
        a, node_ids, [("inhibitor", "target", 0.8)], inhib_gain=0.7
    )
    idx = {n: i for i, n in enumerate(node_ids)}
    assert out[idx["target"]] < a[idx["target"]]
    assert out[idx["target"]] == a[idx["target"]] * (1 - 0.8 * 0.7)
    assert out[idx["bystander"]] == a[idx["bystander"]]


def test_apply_inhibition_noop_when_inhibitor_inactive() -> None:
    node_ids = ["inhibitor", "target"]
    a = np.array([0.0, 1.0])
    out = activation_mod.apply_inhibition(a, node_ids, [("inhibitor", "target", 0.9)], inhib_gain=0.7)
    assert out[1] == a[1]


def test_reciprocal_equal_strength_inhibition() -> None:
    node_ids = ["a", "b"]
    activation = np.array([0.6, 0.6])
    out = activation_mod.apply_inhibition(
        activation,
        node_ids,
        [("a", "b", 0.8), ("b", "a", 0.8)],
        inhib_gain=0.245,
    )
    assert out[0] / activation[0] == out[1] / activation[1]


def test_page_rank_ranks_hub_above_leaf() -> None:
    # star graph: four leaves all point at "hub".
    node_ids = ["hub", "l1", "l2", "l3", "l4"]
    edges = [("l1", "hub", 1.0), ("l2", "hub", 1.0), ("l3", "hub", 1.0), ("l4", "hub", 1.0)]
    graph = activation_mod.build_graph(node_ids, edges)
    rank = activation_mod.page_rank(graph)
    idx = {n: i for i, n in enumerate(node_ids)}
    assert rank[idx["hub"]] > rank[idx["l1"]]


def test_combine_scores_matches_weighted_sum() -> None:
    inputs = activation_mod.ScoreInputs(
        node_ids=["a"],
        activation=np.array([0.5]),
        cosine=np.array([0.8]),
        retrieval=np.array([0.2]),
        excitability=np.array([0.05]),
    )
    score = activation_mod.combine_scores(
        inputs, w_activation=0.45, w_similarity=0.30, w_retrieval=0.15, w_excitability=0.10
    )
    expected = 0.45 * 0.5 + 0.30 * 0.8 + 0.15 * 0.2 + 0.10 * 0.05
    assert abs(score[0] - expected) < 1e-9


def test_apply_hub_penalty_dampens_only_above_percentile() -> None:
    score = np.array([1.0, 1.0, 1.0, 1.0, 100.0])
    usage_pagerank = np.array([1.0, 1.0, 1.0, 1.0, 1000.0])
    out = activation_mod.apply_hub_penalty(score, usage_pagerank, hub_penalty=0.15, percentile=75.0)
    assert out[-1] == score[-1] * (1 - 0.15)
    assert out[0] == score[0]


def test_apply_hub_penalty_empty_scores_is_noop() -> None:
    out = activation_mod.apply_hub_penalty(np.zeros(0), np.zeros(0), hub_penalty=0.15)
    assert out.size == 0
