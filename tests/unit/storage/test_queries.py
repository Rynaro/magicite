"""``storage/queries.py``: read models for ``introspect``/``flag_dead``
(spec §1 layout). AC-041's proving unit lives here."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.storage import queries as queries_mod


def test_edge_rows_report_effective_strength(cfg, db_conn, embedder) -> None:
    """AC-041: GIVEN a declared edge that no Dream run has ever
    potentiated WHEN introspect(skill_id=...) returns that engram's
    outbound edges THEN each returned edge row SHALL carry an
    effective_strength field equal to declared_edge_strength -- additive
    alongside the still-raw, still-learned-only storage_strength (spec
    §3.3.1 call site 6)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    detail = queries_mod.skill_detail(
        db_conn, "proton-ge-proton-downgrade", declared_edge_strength=cfg.declared_edge_strength
    )
    assert detail is not None
    outbound = {e["target"]: e for e in detail["outbound_edges"]}

    assert outbound["steam-prefix-access"]["storage_strength"] == 0.0
    assert outbound["steam-prefix-access"]["effective_strength"] == cfg.declared_edge_strength
    assert outbound["proton-clean-install"]["storage_strength"] == 0.0
    assert outbound["proton-clean-install"]["effective_strength"] == cfg.declared_edge_strength


def test_skill_detail_defaults_declared_edge_strength_when_omitted(cfg, db_conn, embedder) -> None:
    """Backward-compatible default (AC-006's frozen
    tests/acceptance/test_walking_skeleton.py calls skill_detail without
    this kwarg): omitting it still reports the spec default (1.0)."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")

    detail = queries_mod.skill_detail(db_conn, "proton-ge-proton-downgrade")
    assert detail is not None
    outbound = {e["target"]: e for e in detail["outbound_edges"]}
    assert outbound["steam-prefix-access"]["effective_strength"] == 1.0
