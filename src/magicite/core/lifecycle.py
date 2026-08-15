"""Minimal evidence-bar guard entry point (AC-016; spec §5.1's FSM guard
column, ``core/fitness.py``).

**This is deliberately not the full lifecycle FSM.** No approval wiring, no
``storage.durable.set_status()`` call site, no ``promote``/``sharpen``/
``archive`` tool body -- those land in M5 (spec Stories M5;
``mcp/bind_lifecycle.py``'s own docstring: "the lifecycle FSM and approval
machinery ... land in M5"). What ships here is the one thing AC-016 needs
to be provably true now: :func:`apply` raises
:class:`~magicite.errors.TransitionDeniedError`, naming every unmet guard,
whenever an attempted status transition's evidence bar is not cleared --
independent of who calls it or how many (possibly poisoned, R1) Tier-1/2
signals accumulated first. It performs **no mutation**: a passing guard
just returns; the actual state change is the FSM's (M5's) job.
"""

from __future__ import annotations

from magicite.config import Config
from magicite.core import fitness as fitness_mod
from magicite.errors import TransitionDeniedError

#: (from_status, to_status) pairs this minimal slice knows how to guard
#: (spec §5.1's FSM table, the three S/evidence-driven upward transitions
#: -- the ones AC-016's "evidence bar" language is about). Each fitness
#: gate has a deliberately different signature (spec §7.1: "each docs/07
#: gate at boundary values"), so dispatch is explicit per pair rather than
#: a single uniform ``gate(evidence, **kwargs)`` call.
_KNOWN_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {("nascent", "probation"), ("probation", "consolidated"), ("consolidated", "promoted")}
)


def apply(
    *,
    from_status: str,
    to_status: str,
    evidence: fitness_mod.Evidence,
    cfg: Config | None = None,
    **gate_kwargs: object,
) -> None:
    """Raise :class:`TransitionDeniedError` (naming the unmet guards) unless
    the ``(from_status, to_status)`` evidence bar is cleared. Returns
    ``None`` on success -- this function never mutates anything; it is
    the guard check M5's FSM will call before it does."""
    key = (from_status, to_status)
    if key not in _KNOWN_TRANSITIONS:
        raise ValueError(f"no evidence gate registered for {from_status!r} -> {to_status!r}")

    if key == ("nascent", "probation"):
        unmet = fitness_mod.nascent_to_probation_gate(**gate_kwargs)  # type: ignore[arg-type]
    elif key == ("probation", "consolidated"):
        unmet = fitness_mod.probation_to_consolidated_gate(evidence, cfg or Config())
    else:
        unmet = fitness_mod.consolidated_to_promoted_gate(evidence, **gate_kwargs)  # type: ignore[arg-type]

    if unmet:
        raise TransitionDeniedError(
            f"{from_status} -> {to_status} denied: evidence bar not cleared",
            unmet=unmet,
        )
