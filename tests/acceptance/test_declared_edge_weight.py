"""AC-034: the reachable-in-production counterpart to AC-023
(tests/unit/core/test_router.py::test_inhibition_lowers_score, which
proves the arithmetic is live by inserting an ``inhibits`` row directly
by SQL). AC-023's own GIVEN says nothing about provenance or how the edge
came to exist, so it is silent on whether that state is reachable by any
production ingestion path -- it was not, before DECLARED-EDGES-AMENDED
(decisions/DECLARED-EDGES-AMENDED.md §6.1). This suite's GIVEN names
``register()`` explicitly, closing that coverage gap without touching
frozen AC-023."""

from __future__ import annotations

import pytest

from magicite.core import registry as registry_mod
from magicite.core import router as router_mod

pytestmark = pytest.mark.acceptance


def test_inhibition_is_reachable_from_register(cfg, db_conn, embedder) -> None:
    """AC-034: GIVEN a registry whose engrams were ingested only through
    register() and one of whose .egr.md frontmatters declares inhibits:
    [<competitor>] (the toy registry's proton-ge-proton-downgrade ->
    proton-clean-install) WHEN route() scores a query that activates both
    the inhibitor and the competitor THEN the competitor's score SHALL be
    strictly lower than its score in an otherwise identical run with
    declared_edge_strength = 0.0.
    """
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    query = "rollback proton for a steam game"

    cfg.declared_edge_strength = 1.0
    live = router_mod.route(cfg, db_conn, embedder, query=query, k=7)
    live_score = next(c.score for c in live.candidates if c.name == "proton-clean-install")
    assert any(c.name == "proton-ge-proton-downgrade" for c in live.candidates), (
        "the inhibitor must itself be activated for this to prove anything"
    )

    cfg.declared_edge_strength = 0.0
    reverted = router_mod.route(cfg, db_conn, embedder, query=query, k=7)
    reverted_score = next(c.score for c in reverted.candidates if c.name == "proton-clean-install")

    assert live_score < reverted_score
