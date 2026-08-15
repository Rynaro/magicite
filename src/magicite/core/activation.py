"""Sparse personalized-PageRank (PPR) activation (spec §3.3 steps 3-7).

Framework-free (INV-1) and hot-path (AC-024: this module -- transitively
via ``core/router.py`` -- MUST NOT import ``magicite.storage.durable`` or
``magicite.engram.writer``; it takes plain node-id/edge-weight data and
returns plain ``numpy`` vectors, no DB handle).

Represented as **COO sparse** (parallel ``src``/``dst``/``weight`` arrays),
not a dense ``n x n`` matrix: a registry's edge count is O(n) (declared
composition + a bounded top-``m`` kNN, spec §2.6 step 8), never O(n^2), so
a scatter-accumulate (``np.add.at``) power iteration is both the honest
complexity class and comfortably inside the AC-011 latency budget at the
1000-node scale the acceptance test exercises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class SparseGraph:
    """A directed, row-normalized weighted graph over a fixed node universe.

    ``weight`` is already row-normalized per source node (spec §3.3 step 4:
    "W row-normalized, positive edges") so a PPR power iteration is a plain
    scatter-accumulate, not a per-iteration renormalization.
    """

    node_ids: list[str]
    node_index: dict[str, int]
    src_idx: np.ndarray  # int64, one entry per directed edge
    dst_idx: np.ndarray  # int64
    weight: np.ndarray  # float64, row-normalized


def build_graph(node_ids: list[str], edges: list[tuple[str, str, float]]) -> SparseGraph:
    """``edges``: ``(src_id, dst_id, raw_weight)`` triples. ``raw_weight`` is
    whatever the caller wants propagated -- ``core/router.py`` passes
    ``S_eff * type_gain[type]`` for activation (spec step 4, §3.3.1) and a
    structural ``type_gain``-only weight for the hub-penalty PageRank
    (``router.py``'s own docstring explains why it is deliberately a
    structural metric, not a usage-weighted one).

    Edges naming a node outside ``node_ids``, or with non-positive weight,
    are dropped -- ``inhibits`` edges in particular are never fed here
    (spec step 5 applies them as a separate multiplicative pass, not as
    positive graph mass).
    """
    node_index = {nid: i for i, nid in enumerate(node_ids)}
    out_totals: dict[str, float] = {}
    filtered: list[tuple[str, str, float]] = []
    for src, dst, w in edges:
        if src not in node_index or dst not in node_index or w <= 0:
            continue
        filtered.append((src, dst, w))
        out_totals[src] = out_totals.get(src, 0.0) + w

    if not filtered:
        empty = np.zeros(0, dtype=np.int64)
        return SparseGraph(
            node_ids=node_ids,
            node_index=node_index,
            src_idx=empty,
            dst_idx=empty,
            weight=np.zeros(0, dtype=np.float64),
        )

    src_idx = np.fromiter((node_index[s] for s, _, _ in filtered), dtype=np.int64, count=len(filtered))
    dst_idx = np.fromiter((node_index[d] for _, d, _ in filtered), dtype=np.int64, count=len(filtered))
    weight = np.fromiter(
        (w / out_totals[s] for s, _, w in filtered), dtype=np.float64, count=len(filtered)
    )
    return SparseGraph(
        node_ids=node_ids, node_index=node_index, src_idx=src_idx, dst_idx=dst_idx, weight=weight
    )


def softmax_personalization(
    node_ids: list[str], cos_sim: dict[str, float], *, temperature: float = 0.07
) -> np.ndarray:
    """spec §3.3 step 3: ``p = softmax(cos_sim(seeds) / temperature)`` placed
    on the full node-id universe (0 outside the seed set)."""
    n = len(node_ids)
    p = np.zeros(n, dtype=np.float64)
    if not cos_sim:
        return p
    index = {nid: i for i, nid in enumerate(node_ids)}
    seed_ids = [nid for nid in cos_sim if nid in index]
    if not seed_ids:
        return p
    scores = np.array([cos_sim[nid] for nid in seed_ids], dtype=np.float64) / temperature
    scores = scores - scores.max()  # numerically stable softmax
    exp = np.exp(scores)
    probs = exp / exp.sum()
    for nid, prob in zip(seed_ids, probs, strict=True):
        p[index[nid]] = prob
    return p


def personalized_pagerank(
    graph: SparseGraph,
    p: np.ndarray,
    *,
    restart: float = 0.15,
    max_iter: int = 20,
    tol: float = 1e-4,
) -> np.ndarray:
    """spec §3.3 step 4: ``a = PPR(p, W, restart=0.15, max_iter=20, tol=1e-4)``.

    ``a_{t+1} = restart * p + (1 - restart) * (W^T @ a_t + dangling_mass *
    p)``, W already row-normalized (``build_graph``). A node with no
    out-edges ("dangling") redistributes its mass via the personalization
    vector ``p`` rather than dropping it -- the standard personalized-
    PageRank convention (Haveliwala 2002) for keeping the walk a proper
    (sub-)probability distribution; it is also what makes an edgeless
    graph's fixed point exactly ``p`` (every node dangling, all mass
    redistributed through ``p``), the sane degenerate case for a query
    against a registry with no learned/derived structure yet. Converges
    (L1 delta < tol) or stops at ``max_iter`` -- whichever first, per spec.
    """
    n = len(graph.node_ids)
    out_degree = np.zeros(n, dtype=np.float64)
    if graph.src_idx.size:
        np.add.at(out_degree, graph.src_idx, graph.weight)
    dangling = out_degree == 0

    a = p.copy()
    for _ in range(max_iter):
        contrib = np.zeros(n, dtype=np.float64)
        if graph.src_idx.size:
            np.add.at(contrib, graph.dst_idx, a[graph.src_idx] * graph.weight)
        dangling_mass = float(a[dangling].sum())
        next_a = restart * p + (1 - restart) * (contrib + dangling_mass * p)
        delta = float(np.abs(next_a - a).sum())
        a = next_a
        if delta < tol:
            break
    return a


def apply_inhibition(
    a: np.ndarray,
    node_ids: list[str],
    inhibition_edges: list[tuple[str, str, float]],
    *,
    inhib_gain: float = 0.7,
) -> np.ndarray:
    """spec §3.3 step 5: for every ``(j -> i, type='inhibits')`` edge with
    ``a_j > 0``: ``a_i *= (1 - S_eff_ji * inhib_gain)``.

    ``inhibition_edges``: ``(src_j, dst_i, s_eff_ji)`` triples -- the
    caller (``core/router.py::_fetch_inhibition_edges``) has already run
    the edge's ``(storage_strength, provenance)`` through
    ``core/edge_weight.py::effective_strength`` (spec §3.3.1); this
    function is agnostic to where the weight came from, it only applies
    it. src is the *inhibitor*.
    """
    index = {nid: i for i, nid in enumerate(node_ids)}
    out = a.copy()
    for src_j, dst_i, s_eff_ji in inhibition_edges:
        j = index.get(src_j)
        i = index.get(dst_i)
        if j is None or i is None:
            continue
        if a[j] > 0:
            out[i] = out[i] * (1 - s_eff_ji * inhib_gain)
    return out


def page_rank(
    graph: SparseGraph, *, damping: float = 0.85, max_iter: int = 50, tol: float = 1e-6
) -> np.ndarray:
    """Standard PageRank power iteration over ``graph`` -- the structural
    "usage PageRank" proxy ``core/router.py``'s hub-penalty step (spec
    step 7, docs/03 "black-hole hubs") thresholds at p95. Dangling nodes
    (no out-edges) redistribute their mass uniformly, the standard fix for
    a stochastic, irreducible transition matrix."""
    n = len(graph.node_ids)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    out_degree = np.zeros(n, dtype=np.float64)
    if graph.src_idx.size:
        np.add.at(out_degree, graph.src_idx, graph.weight)
    dangling = out_degree == 0
    rank = np.full(n, 1.0 / n, dtype=np.float64)
    base = (1 - damping) / n
    for _ in range(max_iter):
        contrib = np.zeros(n, dtype=np.float64)
        if graph.src_idx.size:
            np.add.at(contrib, graph.dst_idx, rank[graph.src_idx] * graph.weight)
        dangling_mass = float(rank[dangling].sum())
        next_rank = base + damping * (contrib + dangling_mass / n)
        delta = float(np.abs(next_rank - rank).sum())
        rank = next_rank
        if delta < tol:
            break
    return rank


@dataclass
class ScoreInputs:
    """Per-candidate raw signal vectors, aligned to ``node_ids`` order
    (spec §3.3 step 6)."""

    node_ids: list[str] = field(default_factory=list)
    activation: np.ndarray = field(default_factory=lambda: np.zeros(0))
    cosine: np.ndarray = field(default_factory=lambda: np.zeros(0))
    retrieval: np.ndarray = field(default_factory=lambda: np.zeros(0))
    excitability: np.ndarray = field(default_factory=lambda: np.zeros(0))


def combine_scores(
    inputs: ScoreInputs,
    *,
    w_activation: float = 0.45,
    w_similarity: float = 0.30,
    w_retrieval: float = 0.15,
    w_excitability: float = 0.10,
) -> np.ndarray:
    """spec §3.3 step 6: ``score_i = w_a*a_i + w_s*cos_i + w_r*R_i + w_e*excitability_i``."""
    return (
        w_activation * inputs.activation
        + w_similarity * inputs.cosine
        + w_retrieval * inputs.retrieval
        + w_excitability * inputs.excitability
    )


def apply_hub_penalty(
    score: np.ndarray, usage_pagerank: np.ndarray, *, hub_penalty: float = 0.15, percentile: float = 95.0
) -> np.ndarray:
    """spec §3.3 step 7: ``if usage_pagerank_i > p95: score_i *= (1 - hub_penalty)``."""
    if score.size == 0:
        return score
    threshold = float(np.percentile(usage_pagerank, percentile))
    penalized = score.copy()
    mask = usage_pagerank > threshold
    penalized[mask] = penalized[mask] * (1 - hub_penalty)
    return penalized
