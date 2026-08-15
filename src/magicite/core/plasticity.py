"""dw rules (spec §4.3, §6.2's "Tier gate") -- the M3 slice.

Full metaplastic-saturation/spacing Δw computation (spec §4.3 Phase 2:
``eta_eff = eta * (1 - w/w_max) * tier_weight[tier] * (1 - exp(-dt/tau_spacing))``,
``dw = eta_eff * mean_outcome * capture_weight``) is Dream's phase-2 update
rule and lands with ``core/dream.py`` in M4, where ``dt_since_last_update``,
``mean_outcome`` and a trace-replayed ``capture_weight`` are real quantities
this module has no honest way to manufacture yet -- there is no Dream replay
loop in this milestone to supply them. Claiming that formula is "implemented"
here would be exactly the kind of overclaiming this milestone's own
instructions warn against.

What ships now is the one piece M3's two P0 security risks actually depend
on: the mechanical **tier gate** that makes "Tier-0 evidence may move R and
bookkeeping only, never S" (docs/05 D3, spec §6.2) an enforced invariant
rather than a comment -- :func:`apply` raising :class:`P0Violation` -- plus
the ``TIER_WEIGHT`` table Dream's real Δw formula will read from, unchanged,
once M4 builds it.
"""

from __future__ import annotations

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
