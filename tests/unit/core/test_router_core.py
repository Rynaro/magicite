from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.core import router as router_mod
from magicite.core import routing_policy as policy_mod
from tests.conftest import TOY_ENGRAM_NAMES


def test_route_mints_session_id_when_omitted(cfg, db_conn, embedder, review_fixture_artifacts) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(cfg, db_conn, embedder, query="steam wont open", k=3)
    assert outcome.session_id
    row = db_conn.execute(
        "SELECT session_id FROM eph_session WHERE session_id = ?", (outcome.session_id,)
    ).fetchone()
    assert row is not None


def test_route_reuses_explicit_session_id(cfg, db_conn, embedder, review_fixture_artifacts) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(cfg, db_conn, embedder, query="steam wont open", k=3, session_id="my-session")
    assert outcome.session_id == "my-session"


def test_route_on_empty_registry_returns_no_candidates(
    cfg, db_conn, embedder, review_fixture_artifacts
) -> None:
    outcome = router_mod.route(cfg, db_conn, embedder, query="anything at all", k=5)
    assert outcome.candidates == []
    assert outcome.composition_plan == []
    assert outcome.registry_size == 0
    assert outcome.policy_id == policy_mod.POLICY_DENSE_V1


def test_route_respects_k(cfg, db_conn, embedder, review_fixture_artifacts) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(cfg, db_conn, embedder, query="proton", k=2)
    assert len(outcome.candidates) == 2


def test_route_context_strings_are_echoed_as_unresolved(
    cfg, db_conn, embedder, review_fixture_artifacts
) -> None:
    # Context unresolved echo is part of the experimental adaptive path;
    # stable dense-v1 does not apply soft context conditioning (S06 owns eligibility).
    cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(
        cfg,
        db_conn,
        embedder,
        query="steam wont open",
        context={"project_tag": "steam-gaming", "recent_failures": ["OOMKilled"]},
        k=3,
    )
    assert "steam-gaming" in outcome.unresolved_context
    assert "OOMKilled" in outcome.unresolved_context
