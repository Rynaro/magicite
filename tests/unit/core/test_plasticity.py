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


# ── M4: the full Δw formula (spec §4.3 Phase 2) ─────────────────────────


def test_compute_eta_eff_untiered_first_observation_is_zero_weight() -> None:
    """M4 hardening: a first-ever observation (no prior anchor,
    dt_since_last_update_s=None) contributes ZERO weight, not full weight
    -- closing the burst-potentiation gap where a single call fanning out
    into many simultaneous first-time observations (e.g. signal_use() on
    20 skill ids) could fully potentiate all of them at once."""
    eta_eff = plasticity_mod.compute_eta_eff_untiered(
        eta=0.08, w_max=1.0, tau_spacing_hours=6.0, current_w=0.0, dt_since_last_update_s=None
    )
    assert eta_eff == 0.0


def test_compute_eta_eff_untiered_saturates_toward_w_max() -> None:
    """spec §4.3: eta_eff = eta * (1 - w/w_max) * ... -- as w -> w_max,
    eta_eff -> 0 (metaplastic saturation ceiling)."""
    near_full = plasticity_mod.compute_eta_eff_untiered(
        eta=0.08, w_max=1.0, tau_spacing_hours=6.0, current_w=0.999, dt_since_last_update_s=3600.0 * 6
    )
    fresh = plasticity_mod.compute_eta_eff_untiered(
        eta=0.08, w_max=1.0, tau_spacing_hours=6.0, current_w=0.0, dt_since_last_update_s=3600.0 * 6
    )
    assert 0.0 <= near_full < fresh


def test_compute_eta_eff_untiered_spacing_damps_rapid_repeats() -> None:
    """spec §4.3: "massed repeats within dt < tau_s get scaled by
    (1 - e^{-dt/tau_s})" -- a touch 1 second after the anchor is damped
    far more than one a full spacing-interval later."""
    rapid = plasticity_mod.compute_eta_eff_untiered(
        eta=0.08, w_max=1.0, tau_spacing_hours=6.0, current_w=0.0, dt_since_last_update_s=1.0
    )
    spaced = plasticity_mod.compute_eta_eff_untiered(
        eta=0.08, w_max=1.0, tau_spacing_hours=6.0, current_w=0.0, dt_since_last_update_s=6 * 3600.0
    )
    assert rapid < spaced


def test_metaplastic_saturation_bound() -> None:
    """AC-017's proving test (frozen VERIFY pointer:
    ``tests/unit/core/test_plasticity.py::test_metaplastic_saturation_bound``).
    GIVEN captured tags from three sessions with positive outcomes WHEN a
    Dream run completes THEN the affected edge's storage_strength SHALL
    increase by no more than eta * (1 - w/w_max) per capture -- exercised
    here via ``compute_dw_magnitude()``'s own |dw| bound, true for ANY
    mean_outcome in [-1,1] and capture_weight in [0,1], since both
    TIER_WEIGHT<=1 and the spacing factor <=1."""
    eta, w_max, current_w = 0.08, 1.0, 0.3
    bound = eta * (1 - current_w / w_max)
    for mean_outcome in (-1.0, -0.3, 0.0, 0.5, 1.0):
        for capture_weight in (0.0, 0.4, 1.0):
            for dt in (None, 1.0, 3600.0, 6 * 3600.0, 100 * 3600.0):
                magnitude = plasticity_mod.compute_dw_magnitude(
                    eta=eta,
                    w_max=w_max,
                    tau_spacing_hours=6.0,
                    current_w=current_w,
                    dt_since_last_update_s=dt,
                    mean_outcome=mean_outcome,
                    capture_weight=capture_weight,
                )
                assert abs(magnitude) <= bound + 1e-9


def test_compute_dw_magnitude_captured_from_three_sessions_stays_bounded() -> None:
    """AC-017's literal scenario: captured tags from three sessions with
    positive outcomes, applied sequentially (each updating current_w for
    the next) -- every single application's |dw| <= eta*(1-w/w_max) at the
    w it was applied against."""
    eta, w_max = 0.08, 1.0
    w = 0.0
    dt = 6 * 3600.0  # fully spaced each time
    for _ in range(3):
        bound = eta * (1 - w / w_max)
        magnitude = plasticity_mod.compute_dw_magnitude(
            eta=eta,
            w_max=w_max,
            tau_spacing_hours=6.0,
            current_w=w,
            dt_since_last_update_s=dt,
            mean_outcome=1.0,
            capture_weight=1.0,
        )
        assert 0.0 <= magnitude <= bound + 1e-9
        delta = plasticity_mod.PlasticityDelta(tier=2, target="S", magnitude=magnitude)
        w = plasticity_mod.apply(w, delta)
    assert w > 0.0
