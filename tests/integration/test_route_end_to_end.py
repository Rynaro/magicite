"""AC-012's proving integration test, against the real toy registry
(register() -> route()), plus a couple of M2 end-to-end sanity checks that
exercise sync()'s new steps 8-9 (derived similar_to edges + community
detection) feeding back into route()'s community rerank. AC-037/AC-038
(DECLARED-EDGES-AMENDED, 2026-08-15) are the amended plan_confidence's
proving units."""

from __future__ import annotations

from datetime import UTC, datetime

from magicite.core import registry as registry_mod
from magicite.core import router as router_mod


def test_topological_plan_order(cfg, db_conn, embedder) -> None:
    """AC-012: GIVEN an engram declaring needs: [steam-prefix-access] WHEN
    that engram wins routing THEN composition_plan SHALL list
    steam-prefix-access before the winner."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)

    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"
    assert "steam-prefix-access" in outcome.composition_plan
    assert outcome.composition_plan.index("steam-prefix-access") < outcome.composition_plan.index(
        "proton-ge-proton-downgrade"
    )


def test_route_after_sync_uses_derived_communities(cfg, db_conn, embedder) -> None:
    """sync() (unlike register()) runs spec §2.6 steps 8-9: route() should
    still return sane, non-empty results once communities/similar_to
    edges exist -- the community rerank step must not accidentally starve
    a small registry."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    sync_outcome = registry_mod.sync(cfg, db_conn, embedder)
    assert sync_outcome.detector in {"leiden", "label_propagation"}

    communities = db_conn.execute("SELECT COUNT(*) AS n FROM engram_community").fetchone()["n"]
    assert communities == 7  # every registered engram gets a community assignment

    similar_to = db_conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE type = 'similar_to' AND provenance = 'derived'"
    ).fetchone()["n"]
    assert similar_to > 0

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)
    assert outcome.candidates
    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"


def test_sync_similar_to_edges_are_symmetric_neighbors_only(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    registry_mod.sync(cfg, db_conn, embedder)

    rows = db_conn.execute(
        "SELECT src_id, dst_id FROM edge WHERE type = 'similar_to' AND provenance = 'derived'"
    ).fetchall()
    assert rows
    for row in rows:
        assert row["dst_id"] is not None
        assert row["src_id"] != row["dst_id"]


def test_plan_confidence_is_one_when_fully_resolved(cfg, db_conn, embedder) -> None:
    """AC-037: GIVEN a winning engram ingested through register() whose
    declared needs/composes targets all resolve to registered engrams
    (proton-ge-proton-downgrade's needs: [steam-prefix-access], which is
    itself registered in the toy fixture) WHEN route() returns a
    composition_plan of more than one node THEN plan_confidence SHALL
    equal 1.0."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)

    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"
    assert len(outcome.composition_plan) > 1
    assert outcome.plan_confidence == 1.0


def test_plan_confidence_reports_the_unresolved_share(cfg, db_conn, embedder) -> None:
    """AC-038: GIVEN a winning engram declaring exactly two needs targets
    of which exactly one is registered WHEN route() returns its
    composition_plan THEN plan_confidence SHALL equal 0.5.

    proton-ge-proton-downgrade already declares needs: [steam-prefix-
    access] (registered, spec §2.6-ingested via register()); one
    additional depends_on edge naming a target no .egr.md declares is
    inserted directly so the winner has exactly two needs targets, one
    resolved."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    winner_id = db_conn.execute(
        "SELECT id FROM engram WHERE name = 'proton-ge-proton-downgrade'"
    ).fetchone()["id"]
    now = datetime.now(UTC).isoformat()
    db_conn.execute(
        """
        INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                          evidence_count, provenance, first_observed, dangling)
        VALUES (?, 'nonexistent-skill', NULL, 'depends_on', 0.0, ?, 0, 'declared', ?, 1)
        """,
        (winner_id, now, now),
    )

    outcome = router_mod.route(cfg, db_conn, embedder, query="rollback proton for a steam game", k=5)

    assert outcome.candidates[0].name == "proton-ge-proton-downgrade"
    assert outcome.plan_confidence == 0.5
