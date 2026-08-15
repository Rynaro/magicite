"""``core/edge_weight.py`` -- the effective edge weight ``S_eff`` (spec
§3.3.1, DECLARED-EDGES-AMENDED). AC-035 and AC-039's proving units live
here; the rest is direct coverage of the pure helper itself."""

from __future__ import annotations

from magicite.core import activation as activation_mod
from magicite.core import edge_weight as edge_weight_mod
from magicite.core import registry as registry_mod
from magicite.core import router as router_mod

# ── the pure helper, direct coverage ─────────────────────────────────────


def test_w_authored_is_zero_for_every_non_declared_provenance() -> None:
    for provenance in ("learned", "derived", "distilled"):
        assert edge_weight_mod.w_authored(provenance, 1.0) == 0.0


def test_w_authored_is_declared_edge_strength_for_declared_provenance() -> None:
    assert edge_weight_mod.w_authored("declared", 1.0) == 1.0
    assert edge_weight_mod.w_authored("declared", 0.3) == 0.3


def test_effective_strength_is_a_floor_not_a_replacement() -> None:
    """Learning may exceed an assertion; it may never erase one."""
    # never potentiated: floor wins
    assert edge_weight_mod.effective_strength(0.0, "declared", 1.0) == 1.0
    # potentiated ABOVE the authored floor: the learned value wins
    assert edge_weight_mod.effective_strength(0.95, "declared", 0.5) == 0.95
    # potentiated BELOW the authored floor: the floor still wins
    assert edge_weight_mod.effective_strength(0.2, "declared", 0.5) == 0.5


def test_effective_strength_is_range_preserving() -> None:
    for s in (0.0, 0.3, 1.0):
        for provenance in ("declared", "learned", "derived", "distilled"):
            s_eff = edge_weight_mod.effective_strength(s, provenance, 1.0)
            assert 0.0 <= s_eff <= 1.0


def test_effective_strength_no_learned_suppresses_declared_potentiation() -> None:
    """eval/bench.py baseline (c): a declared edge never contributes its
    accrued Hebbian value, only its authored floor -- unlike the primary
    channel, learning does NOT win here even when it exceeds the floor."""
    assert edge_weight_mod.effective_strength_no_learned(0.95, "declared", 1.0) == 1.0


def test_effective_strength_no_learned_passes_through_derived_cosine() -> None:
    """A `derived` similar_to edge's storage_strength IS its cosine
    similarity, not a Hebbian value -- baseline (c) gives it that instead
    of a flat gain."""
    assert edge_weight_mod.effective_strength_no_learned(0.42, "derived", 1.0) == 0.42


def test_effective_strength_no_learned_is_zero_for_learned_co_activation() -> None:
    assert edge_weight_mod.effective_strength_no_learned(0.9, "learned", 1.0) == 0.0


# ── AC-035 ────────────────────────────────────────────────────────────────


def test_declared_edge_enters_the_activation_graph(cfg, db_conn, embedder) -> None:
    """AC-035: GIVEN an engram ingested through register() whose
    frontmatter declares needs: [<target>] (proton-ge-proton-downgrade ->
    steam-prefix-access, in the toy registry) and whose target is
    registered WHEN the activation graph is built for a route() call THEN
    that declared edge SHALL be present in the graph with raw weight
    declared_edge_strength * type_gain['depends_on'].

    Before this amendment ``build_graph`` dropped it outright (w<=0): a
    freshly-declared depends_on edge's storage_strength is 0.0 forever
    and nothing potentiates it, so it was ABSENT from the graph, not
    merely weak (decisions/DECLARED-EDGES-AMENDED.md §1).
    """
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")

    edge_rows = router_mod._fetch_activation_edges(db_conn)
    row = next(r for r in edge_rows if r["type"] == "depends_on" and r["provenance"] == "declared")

    raw_weight = (
        edge_weight_mod.effective_strength(
            float(row["storage_strength"]), row["provenance"], cfg.declared_edge_strength
        )
        * cfg.type_gain["depends_on"]
    )
    assert raw_weight == cfg.declared_edge_strength * cfg.type_gain["depends_on"]
    assert raw_weight > 0  # would have been dropped by build_graph's w<=0 filter otherwise

    graph = activation_mod.build_graph(
        [row["src_id"], row["dst_id"]], [(row["src_id"], row["dst_id"], raw_weight)]
    )
    assert graph.weight.size == 1  # the edge survived build_graph, present not absent


# ── AC-039 ────────────────────────────────────────────────────────────────


def test_zero_declared_strength_is_an_exact_revert(cfg, db_conn, embedder) -> None:
    """AC-039: GIVEN a registry containing declared edges and a config
    with declared_edge_strength = 0.0 WHEN route() scores a query THEN
    every returned score SHALL equal the score from the same registry
    with its declared edges deleted -- max(S, 0) == S, bit-for-bit
    (decisions/DECLARED-EDGES-AMENDED.md §4.2).

    The hub-penalty structural PageRank (spec §3.3 step 7,
    ``core/router.py:14-25``) is deliberately OUT OF SCOPE for this
    amendment -- it is gated by edge *existence* + ``type_gain`` only, was
    never a function of ``S_edge``/``S_eff``/``declared_edge_strength``
    even pre-amendment, and this record explicitly withholds it (§4.4).
    ``steam-prefix-access`` has two incoming declared ``needs`` edges in
    the toy registry, which trips that *unrelated* existence-based
    mechanism regardless of ``declared_edge_strength`` -- physically
    deleting the edge rows (unlike zeroing the config) also removes that
    structural mass, which is a real but orthogonal confound to what
    AC-039 pins. ``hub_penalty=0.0`` neutralises it so this test isolates
    the one thing §3.3.1 actually changed.
    """
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    cfg.declared_edge_strength = 0.0
    cfg.hub_penalty = 0.0
    query = "rollback proton for a steam game"

    with_zeroed_channel = router_mod.route(cfg, db_conn, embedder, query=query, k=7)
    scores_zeroed = {c.name: c.score for c in with_zeroed_channel.candidates}
    assert scores_zeroed  # sanity: the registry actually routed something

    db_conn.execute("DELETE FROM edge WHERE provenance = 'declared'")
    without_declared_edges = router_mod.route(cfg, db_conn, embedder, query=query, k=7)
    scores_deleted = {c.name: c.score for c in without_declared_edges.candidates}

    assert scores_zeroed == scores_deleted
