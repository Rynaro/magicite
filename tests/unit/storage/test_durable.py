"""``storage/durable.py``'s declared-edge write path (spec §2.6 step 4,
§3.3.1). AC-009's rebuild-invariant coverage lives in
tests/acceptance/test_rebuild_invariant.py; AC-036 is this module's own
unit coverage of the authored channel's persisted-column contract."""

from __future__ import annotations

from magicite.core import registry as registry_mod


def test_authored_weight_is_never_persisted(cfg, db_conn, embedder) -> None:
    """AC-036: GIVEN a declared edge that no Dream run has ever
    potentiated THEN its persisted edge.storage_strength SHALL still be
    exactly 0.0 -- the guard that keeps §3.3.1's authored channel
    computed-at-read, never stored, so AC-009/AC-010's rebuild invariant
    is unmoved. ``wire_declared_edges`` (this module) always writes the
    literal 0.0; nothing in v1 can raise it except Dream potentiating a
    ``co_activation`` edge, which a declared depends_on/composes/inhibits
    edge is never tagged as (decisions/DECLARED-EDGES-AMENDED.md §4.1)."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")

    rows = db_conn.execute(
        "SELECT storage_strength FROM edge WHERE provenance = 'declared'"
    ).fetchall()
    assert rows, "expected at least one declared edge in the toy registry fixture"
    assert all(r["storage_strength"] == 0.0 for r in rows)
