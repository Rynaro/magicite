"""dw rules (spec §4.3, §6.2's "Tier gate") -- M3's tier gate plus M4's full
metaplastic-saturation/spacing Δw formula.

M3 shipped the mechanical **tier gate** that makes "Tier-0 evidence may move
R and bookkeeping only, never S" (docs/05 D3, spec §6.2) an enforced
invariant rather than a comment -- :func:`apply` raising :class:`P0Violation`
-- plus the ``TIER_WEIGHT`` table. That function and table are UNCHANGED
here (AC-014's own tests still exercise exactly this shape).

M4 adds the rest of spec §4.3 Phase 2's formula:

```
eta_eff = eta * (1 - w / w_max)                      # metaplastic saturation
        * tier_weight[tier]                          # 0 -> 0.0, 1 -> 0.6, 2 -> 1.0
        * (1 - exp(-dt_since_last_update / tau_spacing))   # spacing effect
dw      = eta_eff * mean_outcome * capture_weight    # capture_weight carries recency + salience
```

Composed as: :func:`compute_eta_eff_untiered` computes everything **except**
``tier_weight`` (saturation * spacing only); the caller (``core/dream.py``'s
Phase 2) passes the resulting magnitude into :func:`apply` via a
:class:`PlasticityDelta`, and ``apply()`` is what multiplies by
``TIER_WEIGHT[tier]`` (and refuses Tier-0 -> S outright). This split means
the P0 gate (`apply()`) is exercised on *every* real Δw application -- not
just conceptually -- with no risk of double-applying the tier weight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

#: docs/05 §How Plasticity Scales Across Tiers / spec §4.3 Phase 2 tier_weight:
#: Tier-0 (inferred) evidence never reaches S (weight 0.0, "S untouched");
#: Tier-1 (self_reported) is capped at 60% of full weight (spec R1
#: anti-poisoning); Tier-2 (hook_verified) gets full weight, no cap.
TIER_WEIGHT: dict[int, float] = {0: 0.0, 1: 0.6, 2: 1.0}

PlasticityTarget = Literal["S", "R"]


class P0Violation(Exception):
    """Raised by :func:`apply` when a Tier-0 (inferred) signal targets
    storage strength (``S``). The mechanical enforcement point AC-014 checks
    for -- independent of, and in addition to, G1 (the SQLite authorizer,
    ``storage/authorizer.py``): G1 stops the hot path from writing the
    ``engram``/``edge`` tables at all; this stops a Tier-0 delta from ever
    being *computed* as a legitimate S update in the first place, even
    inside Dream's own (M4) writer-lease-holding context."""


@dataclass(frozen=True)
class PlasticityDelta:
    """A proposed weight-change request, shaped the way Dream's phase 2
    (M4) will construct one from a captured tag / replayed trace.

    ``target`` names the state variable the delta would move: ``'S'``
    (storage strength -- durable, tiered, gated) or ``'R'`` (retrieval
    strength -- ephemeral, tier-agnostic, updated synchronously by
    ``core/signals.py``/``storage/ephemeral.py::bump_retrieval`` on the hot
    path and never routed through this gate at all in M3).
    """

    tier: int
    target: PlasticityTarget
    magnitude: float


def apply(current: float, delta: PlasticityDelta) -> float:
    """Weight ``delta.magnitude`` by ``TIER_WEIGHT[delta.tier]`` and add it to
    ``current`` -- refusing outright (:class:`P0Violation`) when a Tier-0
    signal targets ``S`` (AC-014: "leaving S unchanged"). Because this
    function is pure and ``current`` is a plain immutable ``float``, raising
    *is* "leaving S unchanged": the P0 case never reaches the arithmetic that
    would otherwise produce a new value, and no caller can persist a value
    this function never returned.

    This is *not* the full Dream Δw formula (see module docstring): it is
    the gate every such formula must pass through, and the only piece of it
    that exists before M4 supplies real spacing/saturation inputs.
    """
    if delta.tier == 0 and delta.target == "S":
        raise P0Violation(
            "Tier-0 (inferred) signals may only move R and bookkeeping, never "
            "storage strength (docs/05 D3, spec §6.2 Tier gate)"
        )
    if delta.tier not in TIER_WEIGHT:
        raise ValueError(f"unknown signal tier {delta.tier!r}")
    return current + delta.magnitude * TIER_WEIGHT[delta.tier]


def compute_eta_eff_untiered(
    *,
    eta: float,
    w_max: float,
    tau_spacing_hours: float,
    current_w: float,
    dt_since_last_update_s: float | None,
) -> float:
    """spec §4.3 Phase 2, minus the ``tier_weight`` factor (applied
    separately by :func:`apply`, see module docstring):
    ``eta * (1 - w/w_max) * (1 - exp(-dt/tau_spacing))``.

    ``dt_since_last_update_s=None`` means "no prior application (anchor)
    exists yet" -- a node/edge's very first-ever observation. The spacing
    factor is **0.0** in that case, not 1.0: a first observation establishes
    the timing anchor (the caller is expected to still record
    ``last_applied``/``last_updated`` even when this returns 0.0) but
    contributes no weight itself. This closes an M4 input-hygiene gap:
    treating a first observation as "fully spaced" (1.0) let a *single*
    call that fans out into many simultaneous first-time observations --
    e.g. ``signal_use()`` on 20 skill ids asserting up to 380 directed
    co-activation facts in one call -- fully potentiate all of them at once
    from a single burst. Requiring at least one prior, time-anchored
    observation (i.e. evidence spanning >=2 genuinely separate moments)
    before any weight is applied means a burst can establish anchors but
    never move S; only repeated, separately-timed evidence can, and the
    continuous ``1 - exp(-dt/tau_spacing)`` term still smoothly damps
    evidence that returns before a real spacing interval has elapsed.
    """
    if w_max <= 0:
        return 0.0
    saturation = max(0.0, 1.0 - current_w / w_max)
    if dt_since_last_update_s is None:
        spacing = 0.0
    else:
        tau_spacing_s = tau_spacing_hours * 3600.0
        if tau_spacing_s <= 0:
            spacing = 1.0
        else:
            spacing = 1.0 - math.exp(-max(dt_since_last_update_s, 0.0) / tau_spacing_s)
    return eta * saturation * spacing


def compute_dw_magnitude(
    *,
    eta: float,
    w_max: float,
    tau_spacing_hours: float,
    current_w: float,
    dt_since_last_update_s: float | None,
    mean_outcome: float,
    capture_weight: float,
) -> float:
    """The un-tiered Δw magnitude (spec §4.3: ``dw = eta_eff * mean_outcome *
    capture_weight``), still missing the ``tier_weight`` factor -- pass the
    result as ``PlasticityDelta.magnitude`` into :func:`apply`, which
    supplies it (and the Tier-0 refusal). Bounded: since ``mean_outcome`` is
    a valence in ``[-1, 1]`` and ``capture_weight`` in ``[0, 1]`` (spec §3.3
    tool 6: ``capture_weight = salience * exp(-dt/tau_credit)``, both
    factors themselves in ``[0, 1]``), ``|dw_untiered| <= eta_eff <=
    eta * (1 - w/w_max)`` -- which, combined with ``TIER_WEIGHT[tier] <=
    1.0``, is exactly AC-017's bound: "increase by no more than
    ``eta * (1 - w/w_max)`` per capture".
    """
    eta_eff = compute_eta_eff_untiered(
        eta=eta,
        w_max=w_max,
        tau_spacing_hours=tau_spacing_hours,
        current_w=current_w,
        dt_since_last_update_s=dt_since_last_update_s,
    )
    return eta_eff * mean_outcome * capture_weight
