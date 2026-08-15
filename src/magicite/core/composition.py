"""``needs``/``composes`` DAG closure -> Kahn topological sort -> cycle
guard (spec §3.3 step 9, AC-012).

Framework-free (INV-1), read-only (a plain ``sqlite3.Connection`` is
enough; no writer lease, no durable-write import -- composition is a pure
read+graph-algorithm step of ``route()``, not itself a hot-path module
named in AC-024's forbidden-import list, but it holds to the same
discipline as a matter of course).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

#: spec §3.3 step 9: "closure over resolved needs/composes edges".
PLAN_EDGE_TYPES: tuple[str, ...] = ("depends_on", "composes")


@dataclass
class CompositionPlan:
    order: list[str]
    #: ``(dependency_name -> dependent_name)`` -> S_edge, for every
    #: in-closure edge that actually gates ``order`` -- ``plan_confidence``'s
    #: "mean(S_edge over plan edges)" input.
    edge_strength: dict[tuple[str, str], float] = field(default_factory=dict)
    cycle_broken: bool = False
    warning: str | None = None


def _fetch_declared_deps(
    conn: sqlite3.Connection, node_id: str
) -> list[tuple[str, str | None, float]]:
    """This node's own ``depends_on``/``composes`` targets: ``(dst_name,
    dst_id, storage_strength)``. Dangling targets (``dst_id IS NULL``) are
    included -- callers need them to compute ``resolved/declared`` ratios
    (spec step 10) even though they can never gate topological ordering."""
    placeholders = ",".join("?" for _ in PLAN_EDGE_TYPES)
    rows = conn.execute(
        f"""
        SELECT dst_name, dst_id, storage_strength FROM edge
        WHERE src_id = ? AND type IN ({placeholders})
        ORDER BY dst_name
        """,
        (node_id, *PLAN_EDGE_TYPES),
    ).fetchall()
    return [(r["dst_name"], r["dst_id"], r["storage_strength"]) for r in rows]


def _closure(
    conn: sqlite3.Connection, winner_id: str, winner_name: str, *, max_depth: int, max_size: int
) -> tuple[dict[str, str], dict[str, list[tuple[str, float]]]]:
    """BFS from the winner over *resolved* (non-dangling) declared edges,
    bounded by ``max_depth``/``max_size``.

    Returns ``(name -> id)`` for the closure (winner included) and
    ``(name -> [(in_closure_dependency_name, S_edge), ...])`` -- external,
    dangling or depth/size-cut targets are dropped from the second map
    (they cannot gate ordering; the winner's *own* full declared set,
    dangling included, is fetched separately for the resolved/declared
    ratio -- see :func:`plan_confidence`).
    """
    ids_by_name: dict[str, str] = {winner_name: winner_id}
    deps: dict[str, list[tuple[str, float]]] = {}
    frontier: list[tuple[str, str, int]] = [(winner_id, winner_name, 0)]
    seen = {winner_name}

    while frontier and len(ids_by_name) <= max_size:
        node_id, node_name, depth = frontier.pop(0)
        declared = _fetch_declared_deps(conn, node_id)
        node_deps: list[tuple[str, float]] = []
        for dst_name, dst_id, s_edge in declared:
            if dst_id is None:
                continue  # dangling: cannot gate ordering
            can_expand = dst_name not in seen and depth + 1 <= max_depth and len(ids_by_name) < max_size
            if dst_name in ids_by_name or can_expand:
                node_deps.append((dst_name, s_edge))
            if can_expand:
                seen.add(dst_name)
                ids_by_name[dst_name] = dst_id
                frontier.append((dst_id, dst_name, depth + 1))
        deps[node_name] = node_deps

    for name in ids_by_name:
        deps.setdefault(name, [])
    return ids_by_name, deps


def expand(
    conn: sqlite3.Connection,
    winner_id: str,
    winner_name: str,
    *,
    max_depth: int = 5,
    max_size: int = 8,
) -> CompositionPlan:
    """spec §3.3 step 9: closure -> Kahn topological sort -> cycle guard.

    AC-012's contract falls out directly: an edge ``winner --needs--> dep``
    is modeled as "``dep`` must precede ``winner``" (a standard Kahn's
    algorithm on the *reversed* dependency graph), so ``dep`` always lands
    earlier in ``order`` than ``winner``.
    """
    ids_by_name, deps = _closure(conn, winner_id, winner_name, max_depth=max_depth, max_size=max_size)
    names = set(ids_by_name)

    in_degree: dict[str, int] = dict.fromkeys(names, 0)
    dependents: dict[str, list[str]] = {name: [] for name in names}
    edge_strength: dict[tuple[str, str], float] = {}
    for name, dep_list in deps.items():
        for dep_name, s_edge in dep_list:
            in_degree[name] += 1
            dependents[dep_name].append(name)
            edge_strength[(dep_name, name)] = s_edge

    order: list[str] = []
    remaining = dict(in_degree)
    cycle_broken = False

    while len(order) < len(names):
        ready = sorted(n for n, deg in remaining.items() if deg == 0 and n not in order)
        if not ready:
            # spec step 9: "cycle => break the weakest edge + warn". Find
            # the globally-weakest still-blocking edge among the
            # not-yet-ordered nodes and treat it as satisfied.
            cycle_broken = True
            blocked = [n for n in names if n not in order]
            weakest: tuple[str, str] | None = None
            weakest_strength = float("inf")
            for (dep_name, dependent_name), s_edge in edge_strength.items():
                still_blocked = dependent_name in blocked and remaining.get(dependent_name, 0) > 0
                if still_blocked and s_edge < weakest_strength:
                    weakest_strength = s_edge
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
        order=order, edge_strength=edge_strength, cycle_broken=cycle_broken, warning=warning
    )


def plan_confidence(conn: sqlite3.Connection, winner_id: str, plan: CompositionPlan) -> float:
    """spec §3.3 step 10: ``plan_confidence = mean(S_edge over plan edges) *
    (resolved_deps / declared_deps)``.

    ``resolved_deps``/``declared_deps`` are the *winner's own* directly
    declared ``depends_on``/``composes`` edges (dangling included in the
    denominator, excluded from the numerator) -- the same "how much of
    what I declared actually resolved" ratio ``sync()``'s dangling
    reporting uses elsewhere in this codebase. ``mean(S_edge)`` is taken
    over every edge that actually gates ``plan.order`` (the whole closure,
    not just the winner's direct edges), since a weak link anywhere in a
    multi-hop plan should lower confidence in the plan as a whole.
    """
    if len(plan.order) <= 1:
        return 1.0

    winner_edges = conn.execute(
        "SELECT storage_strength, dangling FROM edge WHERE src_id = ? AND type IN ('depends_on','composes')",
        (winner_id,),
    ).fetchall()
    declared = len(winner_edges)
    if declared == 0:
        return 1.0
    resolved = sum(1 for r in winner_edges if not r["dangling"])

    if plan.edge_strength:
        mean_strength = sum(plan.edge_strength.values()) / len(plan.edge_strength)
    else:
        mean_strength = sum(r["storage_strength"] for r in winner_edges) / declared

    return round(mean_strength * (resolved / declared), 4)
