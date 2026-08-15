"""``CommunityDetector`` protocol: Leiden (optional extra) with a
pure-Python label-propagation fallback (spec §3.3 step 8/9, AC-022, Risk
R5: "Leiden dependency fragility -- igraph/leidenalg wheels absent on some
platforms").

``get_detector()`` is the only place that decides which implementation
runs, and it is the only place that ever needs to change if R5 actually
bites in some deployment: everything else in this module (and every
caller) only ever sees the :class:`CommunityDetector` protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

#: node id -> community id.
Partition = dict[str, int]


@runtime_checkable
class CommunityDetector(Protocol):
    """Framework-free (INV-1): no MCP, no DB handle -- plain node/edge data
    in, a partition out. ``core/registry.py::sync()`` is the only caller
    that persists the result (through ``storage.durable``, per G2)."""

    name: str

    def detect(self, node_ids: list[str], edges: list[tuple[str, str, float]]) -> Partition: ...


class LabelPropagationDetector:
    """Asynchronous label propagation (Raghavan, Albert & Kumara 2007),
    always available (pure Python, no optional dependency) -- the AC-022
    fallback and the detector every environment can run.

    Deterministic by construction (unlike the paper's randomized node
    order): each round visits nodes in a fixed, sorted order and breaks
    label-weight ties by the smallest label id, so the same graph always
    yields the same partition -- required for this codebase's "two
    checkpoints of identical state produce byte-identical output"
    discipline (spec §2.5) to extend cleanly to communities, and simply
    makes this detector testable without seeding an RNG.
    """

    name = "label_propagation"

    def __init__(self, *, max_iter: int = 20) -> None:
        self._max_iter = max_iter

    def detect(self, node_ids: list[str], edges: list[tuple[str, str, float]]) -> Partition:
        if not node_ids:
            return {}
        order = sorted(node_ids)
        index = {nid: i for i, nid in enumerate(order)}
        neighbors: list[dict[int, float]] = [{} for _ in order]
        for src, dst, weight in edges:
            if src not in index or dst not in index or weight <= 0 or src == dst:
                continue
            si, di = index[src], index[dst]
            neighbors[si][di] = neighbors[si].get(di, 0.0) + weight
            neighbors[di][si] = neighbors[di].get(si, 0.0) + weight  # undirected for grouping

        labels = list(range(len(order)))  # each node starts as its own label
        for _ in range(self._max_iter):
            changed = False
            for i in range(len(order)):
                nbrs = neighbors[i]
                if not nbrs:
                    continue
                weight_by_label: dict[int, float] = {}
                for j, w in nbrs.items():
                    lbl = labels[j]
                    weight_by_label[lbl] = weight_by_label.get(lbl, 0.0) + w
                best_weight = max(weight_by_label.values())
                # deterministic tie-break: smallest label id among the max-weight labels.
                best_label = min(lbl for lbl, w in weight_by_label.items() if w == best_weight)
                if best_label != labels[i]:
                    labels[i] = best_label
                    changed = True
            if not changed:
                break

        # Relabel to dense 0..k-1 community ids in first-seen (sorted-node)
        # order, so output is stable and independent of the raw label ints.
        remap: dict[int, int] = {}
        result: Partition = {}
        for node_name, lbl in zip(order, labels, strict=True):
            community_id = remap.setdefault(lbl, len(remap))
            result[node_name] = community_id
        return result


class LeidenDetector:
    """Leiden community detection via ``python-igraph``/``leidenalg``
    (spec §1 optional-dependency ``leiden`` extra). Constructed only by
    :func:`get_detector` after a successful optional import -- importing
    this module itself never requires ``igraph``/``leidenalg`` to be
    installed (R5)."""

    name = "leiden"

    def __init__(self, igraph_module: Any, leidenalg_module: Any) -> None:
        self._igraph = igraph_module
        self._leidenalg = leidenalg_module

    def detect(self, node_ids: list[str], edges: list[tuple[str, str, float]]) -> Partition:
        if not node_ids:
            return {}
        order = sorted(node_ids)
        index = {nid: i for i, nid in enumerate(order)}
        edge_list: list[tuple[int, int]] = []
        weights: list[float] = []
        seen: set[tuple[int, int]] = set()
        for src, dst, weight in edges:
            if src not in index or dst not in index or weight <= 0 or src == dst:
                continue
            i, j = index[src], index[dst]
            key = (i, j) if i < j else (j, i)
            if key in seen:
                continue
            seen.add(key)
            edge_list.append(key)
            weights.append(weight)

        graph = self._igraph.Graph(n=len(order), edges=edge_list, directed=False)
        if weights:
            graph.es["weight"] = weights
        partition = self._leidenalg.find_partition(
            graph,
            self._leidenalg.RBConfigurationVertexPartition,
            weights="weight" if weights else None,
        )
        membership = list(partition.membership)
        return {node_name: membership[index[node_name]] for node_name in order}


def _try_import_leiden() -> tuple[Any, Any] | None:
    """Isolated so :func:`get_detector` is trivially unit-testable for the
    AC-022 fallback path (monkeypatch this function to return ``None``)
    without needing to actually uninstall ``igraph``/``leidenalg``."""
    try:
        import igraph
        import leidenalg
    except ImportError:
        return None
    return igraph, leidenalg


def get_detector(*, prefer_leiden: bool = True) -> CommunityDetector:
    """spec §2.6 step 9: "leiden if available, else label_propagation"."""
    if prefer_leiden:
        modules = _try_import_leiden()
        if modules is not None:
            igraph_module, leidenalg_module = modules
            return LeidenDetector(igraph_module, leidenalg_module)
    return LabelPropagationDetector()
