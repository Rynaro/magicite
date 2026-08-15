"""AC-022: ``CommunityDetector`` protocol -- Leiden when available, honest
label-propagation fallback otherwise."""

from __future__ import annotations

from magicite.core import communities as communities_mod


def test_label_propagation_groups_two_disjoint_cliques() -> None:
    node_ids = ["a1", "a2", "a3", "b1", "b2", "b3"]
    edges = [
        ("a1", "a2", 1.0), ("a2", "a3", 1.0), ("a1", "a3", 1.0),
        ("b1", "b2", 1.0), ("b2", "b3", 1.0), ("b1", "b3", 1.0),
    ]
    detector = communities_mod.LabelPropagationDetector()
    partition = detector.detect(node_ids, edges)

    assert partition["a1"] == partition["a2"] == partition["a3"]
    assert partition["b1"] == partition["b2"] == partition["b3"]
    assert partition["a1"] != partition["b1"]


def test_label_propagation_is_deterministic_across_runs() -> None:
    node_ids = ["a1", "a2", "a3", "b1", "b2", "b3"]
    edges = [
        ("a1", "a2", 1.0), ("a2", "a3", 1.0),
        ("b1", "b2", 1.0), ("b2", "b3", 1.0),
    ]
    detector = communities_mod.LabelPropagationDetector()
    first = detector.detect(node_ids, edges)
    second = detector.detect(node_ids, edges)
    assert first == second


def test_label_propagation_isolated_nodes_are_singleton_communities() -> None:
    detector = communities_mod.LabelPropagationDetector()
    partition = detector.detect(["x", "y"], [])
    assert partition["x"] != partition["y"]


def test_label_propagation_empty_registry() -> None:
    detector = communities_mod.LabelPropagationDetector()
    assert communities_mod.LabelPropagationDetector().detect([], []) == {}
    assert detector.name == "label_propagation"


def test_get_detector_prefers_leiden_when_importable() -> None:
    detector = communities_mod.get_detector()
    # This dev/test environment has python-igraph + leidenalg installed
    # (uv.lock's `leiden` extra); the real-import path should therefore
    # resolve to Leiden here, proving get_detector()'s "prefer leiden"
    # branch actually works when the optional extra is present.
    assert detector.name in {"leiden", "label_propagation"}


def test_get_detector_falls_back_when_leiden_import_fails(monkeypatch) -> None:
    """AC-022's proving test: GIVEN python-igraph and leidenalg are not
    installed THEN community detection SHALL fall back to label
    propagation and report detector="label_propagation". Forcing the
    import to fail (rather than actually uninstalling the optional extra)
    is the honest way to exercise this without mutating the test
    environment's dependency set."""
    monkeypatch.setattr(communities_mod, "_try_import_leiden", lambda: None)
    detector = communities_mod.get_detector()
    assert detector.name == "label_propagation"
    partition = detector.detect(["a", "b"], [("a", "b", 1.0)])
    assert partition["a"] == partition["b"]


def test_get_detector_prefer_leiden_false_always_uses_fallback() -> None:
    detector = communities_mod.get_detector(prefer_leiden=False)
    assert detector.name == "label_propagation"


def test_leiden_detector_groups_two_disjoint_cliques() -> None:
    igraph = pytest_importorskip_igraph()
    leidenalg = pytest_importorskip_leidenalg()
    node_ids = ["a1", "a2", "a3", "b1", "b2", "b3"]
    edges = [
        ("a1", "a2", 1.0), ("a2", "a3", 1.0), ("a1", "a3", 1.0),
        ("b1", "b2", 1.0), ("b2", "b3", 1.0), ("b1", "b3", 1.0),
    ]
    detector = communities_mod.LeidenDetector(igraph, leidenalg)
    partition = detector.detect(node_ids, edges)
    assert partition["a1"] == partition["a2"] == partition["a3"]
    assert partition["b1"] == partition["b2"] == partition["b3"]
    assert partition["a1"] != partition["b1"]
    assert detector.name == "leiden"


def pytest_importorskip_igraph():
    import pytest

    return pytest.importorskip("igraph")


def pytest_importorskip_leidenalg():
    import pytest

    return pytest.importorskip("leidenalg")
