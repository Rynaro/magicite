"""AC-012's proving unit -- a synthetic, direct-SQL DAG so composition.py's
Kahn's-algorithm/cycle-guard logic is tested in isolation from
register()/embed() plumbing (mirrors the toy-registry integration coverage
in tests/integration/test_route_end_to_end.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from magicite.core import composition as composition_mod


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_engram(conn, engram_id: str, name: str) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, storage_strength, s_decayed_at, excitability,
          identity_sha256, content_sha256, body_sha256, file_mtime_ns, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?, 0.0, ?, 0.05, ?,?,?, 0, ?, ?)
        """,
        (engram_id, name, f"{name}.egr.md", "engram/0.2", 1, "authored", "verified", "nascent",
         "does", "use_when", now, engram_id, engram_id, engram_id, now, now),
    )


def _insert_edge(
    conn, src_id: str, dst_name: str, dst_id: str | None, edge_type: str, strength: float
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                          evidence_count, provenance, first_observed, dangling)
        VALUES (?,?,?,?,?,?, 3, 'declared', ?, ?)
        """,
        (src_id, dst_name, dst_id, edge_type, strength, now, now, 0 if dst_id else 1),
    )


@pytest.fixture
def synthetic_conn(db_conn):
    return db_conn


def test_expand_orders_dependency_before_winner(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a-winner")
    _insert_engram(synthetic_conn, "egr_b", "b-dep")
    _insert_edge(synthetic_conn, "egr_a", "b-dep", "egr_b", "depends_on", 0.5)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a-winner")
    assert plan.order.index("b-dep") < plan.order.index("a-winner")
    assert not plan.cycle_broken


def test_expand_transitive_chain_is_fully_ordered(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a")
    _insert_engram(synthetic_conn, "egr_b", "b")
    _insert_engram(synthetic_conn, "egr_c", "c")
    _insert_edge(synthetic_conn, "egr_a", "b", "egr_b", "depends_on", 0.5)
    _insert_edge(synthetic_conn, "egr_b", "c", "egr_c", "composes", 0.5)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a")
    assert plan.order == ["c", "b", "a"]


def test_expand_dangling_dependency_excluded_from_order(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a")
    _insert_edge(synthetic_conn, "egr_a", "missing", None, "depends_on", 0.0)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a")
    assert plan.order == ["a"]
    assert not plan.cycle_broken


def test_expand_breaks_cycle_and_warns(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a")
    _insert_engram(synthetic_conn, "egr_b", "b")
    _insert_edge(synthetic_conn, "egr_a", "b", "egr_b", "depends_on", 0.2)
    _insert_edge(synthetic_conn, "egr_b", "a", "egr_a", "depends_on", 0.9)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a")
    assert plan.cycle_broken is True
    assert plan.warning is not None
    assert set(plan.order) == {"a", "b"}


def test_expand_respects_max_depth(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a")
    _insert_engram(synthetic_conn, "egr_b", "b")
    _insert_engram(synthetic_conn, "egr_c", "c")
    _insert_edge(synthetic_conn, "egr_a", "b", "egr_b", "depends_on", 0.5)
    _insert_edge(synthetic_conn, "egr_b", "c", "egr_c", "depends_on", 0.5)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a", max_depth=1)
    assert "c" not in plan.order
    assert "b" in plan.order


def test_expand_respects_max_size(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a")
    for i in range(10):
        _insert_engram(synthetic_conn, f"egr_dep{i}", f"dep{i}")
        _insert_edge(synthetic_conn, "egr_a", f"dep{i}", f"egr_dep{i}", "composes", 0.5)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a", max_size=4)
    assert len(plan.order) <= 4


def test_plan_confidence_single_node_plan_is_one(synthetic_conn) -> None:
    _insert_engram(synthetic_conn, "egr_a", "a")
    plan = composition_mod.expand(synthetic_conn, "egr_a", "a")
    assert composition_mod.plan_confidence(plan) == 1.0


def test_plan_confidence_reflects_dangling_ratio(synthetic_conn) -> None:
    """[DECLARED-EDGES-AMENDED 2026-08-15] plan_confidence is now
    structural (spec §3.3 step 10, decisions/DECLARED-EDGES-AMENDED.md
    §5): |E_sat| / |E|, never Hebbian. E = the winner's two declared
    depends_on edges (a->b resolved, a->missing dangling); E_sat = {a->b}
    (it resolves, appears in order, and order respects it) -> 1/2 = 0.5.
    Was 0.4 under the pre-amendment mean(S_edge) * (resolved/declared)
    formula -- that formula is gone (S_edge over always-declared plan
    edges was a structural constant, not real information); this test is
    not one of the frozen AC-001..033 anchors, so it is updated to the
    amended, spec-mandated formula rather than left proving stale
    behaviour."""
    _insert_engram(synthetic_conn, "egr_a", "a")
    _insert_engram(synthetic_conn, "egr_b", "b")
    _insert_edge(synthetic_conn, "egr_a", "b", "egr_b", "depends_on", 0.8)
    _insert_edge(synthetic_conn, "egr_a", "missing", None, "depends_on", 0.0)

    plan = composition_mod.expand(synthetic_conn, "egr_a", "a")
    confidence = composition_mod.plan_confidence(plan)
    assert confidence == 0.5


def test_cycle_break_is_deterministic(synthetic_conn) -> None:
    """AC-042: GIVEN a composition cycle whose candidate edges all share
    the same effective strength (every declared edge ties at
    declared_edge_strength=1.0 by default, spec §3.3.1 -- S_edge=0.0 here
    for all three, same as production before any Dream potentiation)
    WHEN the plan is expanded twice over the same registry THEN the two
    composition_plan orders SHALL be identical. The (S_eff, dep_name,
    dependent_name) total order (spec §3.3 step 9), not dict-iteration
    order, decides the break -- before this amendment every candidate
    tied at 0.0 and "weakest" degenerated to iteration order."""
    _insert_engram(synthetic_conn, "egr_a", "a")
    _insert_engram(synthetic_conn, "egr_b", "b")
    _insert_engram(synthetic_conn, "egr_c", "c")
    _insert_edge(synthetic_conn, "egr_a", "b", "egr_b", "depends_on", 0.0)
    _insert_edge(synthetic_conn, "egr_b", "c", "egr_c", "depends_on", 0.0)
    _insert_edge(synthetic_conn, "egr_c", "a", "egr_a", "depends_on", 0.0)

    first = composition_mod.expand(synthetic_conn, "egr_a", "a")
    second = composition_mod.expand(synthetic_conn, "egr_a", "a")

    assert first.cycle_broken is True
    assert first.order == second.order
