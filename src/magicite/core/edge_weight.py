"""Effective edge weight ``S_eff`` (spec §3.3.1, DECLARED-EDGES-AMENDED).

An edge's routing weight has **two channels**, and ``edge.storage_strength``
is only the second one:

```
S_eff(edge) = max(edge.storage_strength, w_authored(edge))

w_authored(edge) = declared_edge_strength   if edge.provenance == 'declared'
                 = 0.0                      if edge.provenance in ('learned','derived','distilled')
```

An author's ``needs:``/``composes:``/``inhibits:`` is an *assertion*, not a
statistic -- there is no observable whose accumulation makes "A inhibits B"
more true (co-activation of A and B is evidence *against* it, not for it).
``edge.storage_strength`` remains the **learned (Hebbian) channel and
nothing else**: it starts at 0.0 for a declared edge, only Dream may raise
it, and only for the types Dream can potentiate (``co_activation``).

**Properties (all normative, decisions/DECLARED-EDGES-AMENDED.md §4.2):**

- **Computed at read, never stored.** No new column, no migration, no
  ``synapses:`` change. Dream's decay/prune/renormalise keep operating on
  the learned column alone (they never call this module) -- an authored
  assertion can never be decayed, pruned or renormalised away.
- **A floor, not a replacement.** Learning may exceed an assertion; it may
  never erase one.
- **Range-preserving.** ``S_eff in [0,1]`` given ``storage_strength in
  [0, w_max=1.0]`` and ``declared_edge_strength in [0,1]`` -- so
  ``1 - S*inhib_gain > 0`` keeps its arithmetic (spec §3.3 step 5).
- **Exactly revertible.** ``declared_edge_strength = 0.0`` reproduces
  pre-amendment behaviour bit-for-bit (``max(S, 0) == S``).

**One implementation.** Framework-free (INV-1): no DB handle, no
``sqlite3`` import, no ``magicite.storage.durable``/``magicite.engram.writer``
import -- ``core/router.py`` may import this module without breaching
AC-024. Every consumer of an edge's routing weight goes through this
module (AC-040, statically enforced by
``tests/unit/test_p0_enforcement.py::test_edge_weight_helper_is_the_only_weighting_site``).
"""

from __future__ import annotations

#: spec §3.3.1: an author's needs/composes/inhibits assertion carries this
#: much authored weight by default -- [routing] declared_edge_strength in
#: magicite.toml, an ablation switch. 0.0 is an EXACT revert to
#: pre-amendment behaviour (max(S, 0) == S), pinned by AC-039.
DEFAULT_DECLARED_EDGE_STRENGTH: float = 1.0

#: spec §3.3.1: only a ``declared``-provenance edge carries authored
#: weight. ``distilled`` is 0.0 by explicit decision, not by omission: no
#: v1 code path writes an edge row with that provenance yet, and a
#: reserved-but-unused value must not silently acquire full authored
#: weight the day something starts emitting one (see the module's
#: decision record §4.1).
_AUTHORED_PROVENANCE = "declared"


def w_authored(provenance: str, declared_edge_strength: float = DEFAULT_DECLARED_EDGE_STRENGTH) -> float:
    """spec §3.3.1: the authored channel. Zero for every provenance except
    ``'declared'`` -- including ``'learned'``, ``'derived'`` and
    ``'distilled'``."""
    return declared_edge_strength if provenance == _AUTHORED_PROVENANCE else 0.0


def effective_strength(
    storage_strength: float,
    provenance: str,
    declared_edge_strength: float = DEFAULT_DECLARED_EDGE_STRENGTH,
) -> float:
    """spec §3.3.1: ``S_eff(edge) = max(edge.storage_strength, w_authored(edge))``.

    The one legitimate site where an edge's routing weight is derived from
    ``edge.storage_strength`` -- every consumer (activation graph,
    inhibition, community weights, cycle-break tiebreak, ``introspect``)
    calls this, never reads the raw column for that purpose (AC-040).
    """
    return max(storage_strength, w_authored(provenance, declared_edge_strength))


def effective_strength_no_learned(
    storage_strength: float,
    provenance: str,
    declared_edge_strength: float = DEFAULT_DECLARED_EDGE_STRENGTH,
) -> float:
    """spec §3.3.1 call site 7 (``eval/bench.py`` baseline (c), docs/07's
    "no learned weights" arm): the same two-channel rule with the
    **learned** channel suppressed. A ``declared``/``learned``/``distilled``
    edge never contributes its accrued Hebbian ``storage_strength`` here --
    only its authored floor -- so baseline (c) never sees a potentiated
    weight. A ``derived`` (``similar_to`` kNN) edge is the one exception:
    its ``storage_strength`` *is* the raw cosine similarity, not a Hebbian
    value (``storage.durable.replace_similar_to_edges``), so it still
    passes through -- giving those edges their real cosine instead of one
    flat ``type_gain`` for every neighbour.
    """
    learned_component = storage_strength if provenance == "derived" else 0.0
    return max(w_authored(provenance, declared_edge_strength), learned_component)
