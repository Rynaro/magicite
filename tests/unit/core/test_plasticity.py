"""AC-014: Tier-0 (inferred) evidence can never move storage strength (S).

``core/plasticity.py::apply()`` is the mechanical Tier gate spec §6.2 names
directly, independent of G1 (the SQLite authorizer): even inside Dream's own
(M4) writer-lease-holding context, a Tier-0 delta must never be *computed*
as a legitimate S update.
"""

from __future__ import annotations

import pytest

from magicite.core import plasticity as plasticity_mod


def test_tier0_cannot_move_S() -> None:
    """GIVEN a Tier-0 inferred signal
    WHEN plasticity.apply() is asked to move storage strength
    THEN it SHALL raise P0Violation, leaving S unchanged."""
    s_before = 0.42
    delta = plasticity_mod.PlasticityDelta(tier=0, target="S", magnitude=0.9)

    with pytest.raises(plasticity_mod.P0Violation):
        plasticity_mod.apply(s_before, delta)

    # apply() is pure: a raise means no new value was ever produced, so
    # there is nothing a caller could have persisted -- "leaving S unchanged"
    # is not just an assertion on s_before (floats are immutable) but a
    # property of the function never returning in this case.
    assert s_before == 0.42


def test_tier0_can_move_R_shaped_deltas_but_they_carry_zero_weight() -> None:
    """A Tier-0 delta targeting anything other than 'S' does not raise --
    but TIER_WEIGHT[0] == 0.0, so it is still inert by construction. (R's
    *actual* update path is the synchronous eta_R nudge in
    core/signals.py/storage/ephemeral.py::bump_retrieval, which never routes
    through this gate at all in M3 -- see the module docstring.)"""
    result = plasticity_mod.apply(0.1, plasticity_mod.PlasticityDelta(tier=0, target="R", magnitude=0.9))
    assert result == 0.1


@pytest.mark.parametrize("tier,expected_weight", [(1, 0.6), (2, 1.0)])
def test_tier1_and_tier2_move_S_with_the_docs05_weight(tier: int, expected_weight: float) -> None:
    result = plasticity_mod.apply(0.5, plasticity_mod.PlasticityDelta(tier=tier, target="S", magnitude=0.2))
    assert result == pytest.approx(0.5 + 0.2 * expected_weight)


def test_unknown_tier_is_rejected() -> None:
    with pytest.raises(ValueError):
        plasticity_mod.apply(0.5, plasticity_mod.PlasticityDelta(tier=3, target="S", magnitude=0.1))


def test_tier_weight_table_matches_docs05_exactly() -> None:
    """docs/05 §"How Plasticity Scales Across Tiers": tier 0 -> S untouched,
    tier 1 -> eta*0.6, tier 2 -> eta*1.0 (full weight, no cap)."""
    assert plasticity_mod.TIER_WEIGHT == {0: 0.0, 1: 0.6, 2: 1.0}
