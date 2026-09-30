"""AC-S07-03: pinned semantic inputs → identical decision fields across rebuild."""

from __future__ import annotations

from magicite.core import fingerprint_key as fk
from magicite.core import registry as registry_mod
from magicite.core import router as router_mod


def test_rebuild_determinism(cfg, db_conn, embedder) -> None:
    """GIVEN pinned semantic inputs
    WHEN routing repeats across rebuild
    THEN semantic decision fields SHALL match.
    """
    cfg.ensure_dirs()
    fk.set_fingerprint_key_override(b"\x33" * fk.KEY_BYTES)
    try:
        registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
        query = "rollback proton for a steam game"

        first = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
        assert first.decision is not None
        semantic_first = router_mod.semantic_decision_fields(first.decision)

        # Rebuild registry from the same pinned files/embeddings inputs.
        sync = registry_mod.sync(cfg, db_conn, embedder)
        assert sync is not None

        second = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
        assert second.decision is not None
        semantic_second = router_mod.semantic_decision_fields(second.decision)

        assert semantic_first == semantic_second
        # Opaque decision ids must differ (excluded from semantic equality).
        assert first.decision.decision_id != second.decision.decision_id
        # Raw query text must not appear in the decision payload.
        blob = str(semantic_second)
        assert query not in blob
    finally:
        fk.set_fingerprint_key_override(None)
