"""``needs``/``composes`` DAG closure -> Kahn topological sort -> cycle
guard (spec §3.3 step 9, AC-012).

Framework-free (INV-1), read-only (a plain ``sqlite3.Connection`` is
enough; no writer lease, no durable-write import -- composition is a pure
read+graph-algorithm step of ``route()``, not itself a hot-path module
named in AC-024's forbidden-import list, but it holds to the same
discipline as a matter of course). ``core/edge_weight.py`` is likewise
framework-free, so importing it here does not compromise that discipline
either.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from magicite.core import edge_weight as edge_weight_mod

#: spec §3.3 step 9: "closure over resolved needs/composes edges".
PLAN_EDGE_TYPES: tuple[str, ...] = ("depends_on", "composes")


@dataclass
class CompositionPlan:
    order: list[str]
    #: ``(dependency_name -> dependent_name)`` -> S_eff, for every
    #: in-closure edge that actually gates ``order`` -- the cycle-break
    #: total order's input (spec §3.3 step 9, §3.3.1 call site 4).
    edge_strength: dict[tuple[str, str], float] = field(default_factory=dict)
    #: [DECLARED-EDGES-AMENDED 2026-08-15] spec §3.3 step 10's ``E``:
    #: every declared depends_on/composes edge whose src is a node in the
    #: bounded closure, dangling targets INCLUDED. ``(dependency_name ->
    #: dependent_name)`` -> ``dst_id`` (``None`` if dangling). Purely
    #: structural input for :func:`plan_confidence` -- never touches
    #: S_eff/storage_strength (plan confidence is a statement about
    #: structural completeness, never about Hebbian strength, §3.3.1's
    #: decision record §5).
    declared_plan_edges: dict[tuple[str, str], str | None] = field(default_factory=dict)
    cycle_broken: bool = False
    warning: str | None = None


def _fetch_declared_deps(
    conn: sqlite3.Connection, node_id: str, *, declared_edge_strength: float
) -> list[tuple[str, str | None, float]]:
    """This node's own ``depends_on``/``composes`` targets: ``(dst_name,
    dst_id, S_eff)``. Dangling targets (``dst_id IS NULL``) are included --
    callers need them for the structural E/E_sat sets (spec step 10) even
    though they can never gate topological ordering. ``S_eff`` (spec
    §3.3.1) is computed here, the one site in this module that reads
    ``storage_strength`` (AC-040)."""
    placeholders = ",".join("?" for _ in PLAN_EDGE_TYPES)
    rows = conn.execute(
        f"""
        SELECT dst_name, dst_id, storage_strength, provenance FROM edge
        WHERE src_id = ? AND type IN ({placeholders})
        ORDER BY dst_name
        """,
        (node_id, *PLAN_EDGE_TYPES),
    ).fetchall()
    return [
        (
            r["dst_name"],
            r["dst_id"],
            edge_weight_mod.effective_strength(
                float(r["storage_strength"]), r["provenance"], declared_edge_strength
            ),
        )
        for r in rows
    ]


def _closure(
    conn: sqlite3.Connection,
    winner_id: str,
    winner_name: str,
    *,
    max_depth: int,
    max_size: int,
    declared_edge_strength: float,
) -> tuple[dict[str, str], dict[str, list[tuple[str, float]]], dict[tuple[str, str], str | None]]:
    """BFS from the winner over *resolved* (non-dangling) declared edges,
    bounded by ``max_depth``/``max_size``.

    Returns ``(name -> id)`` for the closure (winner included),
    ``(name -> [(in_closure_dependency_name, S_eff), ...])`` for the
    gating edges cycle-break needs (external, dangling or depth/size-cut
    targets are dropped here), and ``((dependency_name, dependent_name) ->
    dst_id)`` for **every** declared edge whose src is in the closure,
    dangling targets INCLUDED (spec step 10's ``E`` -- see
    :func:`plan_confidence`).
    """
    ids_by_name: dict[str, str] = {winner_name: winner_id}
    deps: dict[str, list[tuple[str, float]]] = {}
    declared_plan_edges: dict[tuple[str, str], str | None] = {}
    frontier: list[tuple[str, str, int]] = [(winner_id, winner_name, 0)]
    seen = {winner_name}

    while frontier and len(ids_by_name) <= max_size:
        node_id, node_name, depth = frontier.pop(0)
        declared = _fetch_declared_deps(conn, node_id, declared_edge_strength=declared_edge_strength)
        node_deps: list[tuple[str, float]] = []
        for dst_name, dst_id, s_eff in declared:
            declared_plan_edges[(dst_name, node_name)] = dst_id
            if dst_id is None:
                continue  # dangling: cannot gate ordering
            can_expand = dst_name not in seen and depth + 1 <= max_depth and len(ids_by_name) < max_size
            if dst_name in ids_by_name or can_expand:
                node_deps.append((dst_name, s_eff))
            if can_expand:
                seen.add(dst_name)
                ids_by_name[dst_name] = dst_id
                frontier.append((dst_id, dst_name, depth + 1))
        deps[node_name] = node_deps

    for name in ids_by_name:
        deps.setdefault(name, [])
    return ids_by_name, deps, declared_plan_edges


def expand(
    conn: sqlite3.Connection,
    winner_id: str,
    winner_name: str,
    *,
    max_depth: int = 5,
    max_size: int = 8,
    declared_edge_strength: float = edge_weight_mod.DEFAULT_DECLARED_EDGE_STRENGTH,
) -> CompositionPlan:
    """spec §3.3 step 9: closure -> Kahn topological sort -> cycle guard.

    AC-012's contract falls out directly: an edge ``winner --needs--> dep``
    is modeled as "``dep`` must precede ``winner``" (a standard Kahn's
    algorithm on the *reversed* dependency graph), so ``dep`` always lands
    earlier in ``order`` than ``winner``.
    """
    ids_by_name, deps, declared_plan_edges = _closure(
        conn,
        winner_id,
        winner_name,
        max_depth=max_depth,
        max_size=max_size,
        declared_edge_strength=declared_edge_strength,
    )
    names = set(ids_by_name)

    in_degree: dict[str, int] = dict.fromkeys(names, 0)
    dependents: dict[str, list[str]] = {name: [] for name in names}
    edge_strength: dict[tuple[str, str], float] = {}
    for name, dep_list in deps.items():
        for dep_name, s_eff in dep_list:
            in_degree[name] += 1
            dependents[dep_name].append(name)
            edge_strength[(dep_name, name)] = s_eff

    order: list[str] = []
    remaining = dict(in_degree)
    cycle_broken = False

    while len(order) < len(names):
        ready = sorted(n for n, deg in remaining.items() if deg == 0 and n not in order)
        if not ready:
            # spec step 9 [DECLARED-EDGES-AMENDED 2026-08-15]: "cycle =>
            # break the edge minimal under the TOTAL ORDER (S_eff,
            # dep_name, dependent_name)". Every declared plan edge ties at
            # `declared_edge_strength` (1.0 by default) now, so the
            # lexicographic tiebreak -- not the strength -- is what makes
            # the break reproducible across repeated expansions (AC-042);
            # before this amendment every candidate tied at 0.0 and the
            # break degenerated to dict-iteration order.
            cycle_broken = True
            blocked = [n for n in names if n not in order]
            weakest: tuple[str, str] | None = None
            weakest_key: tuple[float, str, str] | None = None
            for (dep_name, dependent_name), s_eff in edge_strength.items():
                still_blocked = dependent_name in blocked and remaining.get(dependent_name, 0) > 0
                if not still_blocked:
                    continue
                key = (s_eff, dep_name, dependent_name)
                if weakest_key is None or key < weakest_key:
                    weakest_key = key
                    weakest = (dep_name, dependent_name)
            if weakest is None:  # pragma: no cover - defensive, unreachable for a real cycle
                order.extend(sorted(blocked))
                break
            _dep_name, dependent_name = weakest
            remaining[dependent_name] -= 1
            continue
        for name in ready:
            order.append(name)
            remaining[name] = -1
            for dependent in dependents.get(name, []):
                if dependent not in order:
                    remaining[dependent] -= 1

    order = order[:max_size]
    warning = (
        "composition plan had a cycle; broke the weakest depends_on/composes edge to continue"
        if cycle_broken
        else None
    )
    return CompositionPlan(
        order=order,
        edge_strength=edge_strength,
        declared_plan_edges=declared_plan_edges,
        cycle_broken=cycle_broken,
        warning=warning,
    )


def plan_confidence(plan: CompositionPlan) -> float:
    """spec §3.3 step 10 [DECLARED-EDGES-AMENDED 2026-08-15]: structural
    satisfaction, never Hebbian.

    ```
    E     = every declared depends_on/composes edge whose src is a node
            in the emitted plan (the bounded closure), dangling targets
            INCLUDED
    E_sat = { e in E : e resolves to an engram (dangling = 0 AND
                        dst_id IS NOT NULL)
                        AND e.target is present in `order`
                        AND `order` respects e (index(target) < index(src)) }
    plan_confidence = round(|E_sat| / |E|, 4)   if |E| > 0
                     = 1.0                       if |E| == 0
    ```

    WAS ``mean(S_edge over plan edges) * (resolved_deps / declared_deps)``
    -- unsatisfiable as written: plan edges are always
    ``provenance='declared'`` and Dream never potentiates that type, so
    ``mean(S_edge)`` was a structural constant (0.0 pre-amendment, 1.0
    post-amendment) carrying no information in either regime. Removed
    rather than floated: plan confidence is a statement about the plan's
    structural completeness, not about Hebbian edge strength (decisions/
    DECLARED-EDGES-AMENDED.md §5). All three failure modes fall out of one
    rule with no extra constants: a dangling target fails clause 1, a
    target cut by ``plan_max_depth``/``plan_max_size`` fails clause 2, an
    edge dropped by cycle-breaking fails clause 3.

    **Deliberate behaviour change:** the previous implementation
    short-circuited ``len(plan.order) <= 1 -> 1.0``. That short-circuit is
    gone: a lone winner declaring unresolvable ``needs`` now honestly
    reports a value below 1.0 (0.0 if none resolve) -- exactly the case
    the short-circuit used to hide (AC-038 pins the two-target, one-
    resolved case at 0.5).
    """
    e = plan.declared_plan_edges
    if not e:
        return 1.0

    order_index = {name: i for i, name in enumerate(plan.order)}
    satisfied = 0
    for (dep_name, dependent_name), dst_id in e.items():
        if dst_id is None:
            continue  # clause 1: dangling, never resolves
        dep_idx = order_index.get(dep_name)
        dependent_idx = order_index.get(dependent_name)
        if dep_idx is None or dependent_idx is None:
            continue  # clause 2: cut by plan_max_depth/plan_max_size, or cycle-broken out
        if dep_idx < dependent_idx:  # clause 3: order respects the edge
            satisfied += 1

    return round(satisfied / len(e), 4)
