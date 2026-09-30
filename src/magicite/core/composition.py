"""Typed bounded composition (contracts.md C5) + legacy Kahn expand.

Framework-free (INV-1), read-only. No runtime subprocess or network.

* **Plan/1** — :func:`compose` returns a structurally valid/invalid plan under
  one snapshot/eligibility policy. Cycles, budget exhaustion, conflicts,
  dangling requires, ambiguity, and denied nodes yield ``status=invalid``
  with **no executable prefix**.
* **Legacy** — :func:`expand` / :func:`plan_confidence` remain for eval/bench
  callers (circular Plan-F1 diagnostics). The stable ``route()`` path uses
  :func:`compose` only. Legacy cycle-breaking order may appear on
  :attr:`Plan.legacy_order` for diagnostics; it is never marked executable.
"""

from __future__ import annotations

import heapq
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from magicite.core import edge_weight as edge_weight_mod
from magicite.core.context import RouteContext, ServerPermissionPolicy
from magicite.core.eligibility import (
    REASON_BUDGET_EXCEEDED,
    REASON_CONFLICT,
    REASON_CONTEXT_REQUIRED,
    REASON_CYCLE,
    REASON_DANGLING_DEPENDENCY,
    REASON_VERSION_MISMATCH,
    EligibilitySubject,
    TrustDecisionView,
    evaluate_eligibility,
    version_satisfies,
)
from magicite.engram import (
    Capabilities,
    ProducedCapability,
    RequiredCapability,
    Risk,
)

#: spec §3.3 step 9: "closure over resolved needs/composes edges".
PLAN_EDGE_TYPES: tuple[str, ...] = ("depends_on", "composes")

PLAN_SCHEMA = "Plan/1"
HOST_VERIFICATION_REPORT_SCHEMA = "HostVerificationReport/1"

DEFAULT_MAX_NODES = 16
DEFAULT_MAX_EDGES = 32
DEFAULT_MAX_DEPTH = 8

REASON_AMBIGUOUS_PROVIDER = "ambiguous_provider"
REASON_UNSATISFIED = "unsatisfied"
REASON_INVALID_REFERENCE = "invalid_reference"
REASON_TRUST_EXCEPTION = "trust_exception"

NormativeEdgeType = Literal[
    "requires",
    "produces",
    "before",
    "conflicts_with",
    "alternatives",
    "supersedes",
]
PlanStatus = Literal["valid", "invalid"]
TaskOutcome = Literal["pass", "fail", "unevaluated"]

TrustViewFn = Callable[[str], TrustDecisionView]


# ---------------------------------------------------------------------------
# Legacy expand (eval/bench only — not on the stable route() path)
# ---------------------------------------------------------------------------


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

    Legacy note (C5): cycle-breaking continues here for router compatibility.
    Typed :func:`compose` never labels a cycle-broken order executable.
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


# ---------------------------------------------------------------------------
# Plan/1 typed composition (C5 / S08)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositionLimits:
    """Default bounds (C5): 16 nodes, 32 normative edges, depth 8."""

    max_nodes: int = DEFAULT_MAX_NODES
    max_edges: int = DEFAULT_MAX_EDGES
    max_depth: int = DEFAULT_MAX_DEPTH


@dataclass(frozen=True)
class CompositionNode:
    """Immutable snapshot projection of one engram for composition."""

    subject: EligibilitySubject
    content_digest: str
    asset_digests: Mapping[str, str] = field(default_factory=dict)
    #: Untrusted procedure pointer (path/URI) — never executed by the planner.
    procedure_ref: str | None = None

    @property
    def id(self) -> str:
        return self.subject.id

    @property
    def version(self) -> int:
        return self.subject.version


@dataclass(frozen=True)
class CompositionSnapshot:
    """Pinned registry/policy view for one compose call (C5)."""

    snapshot_id: str
    policy_id: str
    policy_digest: str
    nodes: Mapping[str, CompositionNode]
    #: Deterministic capability → provider preference (policy). Empty = none.
    preferred_providers: Mapping[str, str] = field(default_factory=dict)

    def get(self, engram_id: str) -> CompositionNode | None:
        return self.nodes.get(engram_id)

    def resolve(self, engram_id: str, version: int) -> CompositionNode | None:
        node = self.nodes.get(engram_id)
        if node is None:
            return None
        if node.version != version:
            return None
        return node


@dataclass(frozen=True)
class PlanNode:
    engram_id: str
    version: int
    content_digest: str
    asset_digests: Mapping[str, str]
    procedure_ref: str | None
    required_permissions: frozenset[str]
    risk: Risk | None


@dataclass(frozen=True)
class PlanEdge:
    type: NormativeEdgeType
    src_id: str
    dst_id: str
    optional: bool = False
    #: Capability id for produces/requires-capability resolution edges.
    capability_id: str | None = None


@dataclass(frozen=True)
class PlanDiagnostic:
    code: str
    message: str
    subject_id: str | None = None
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """Plan/1 — structural validity under provided facts (C5).

    ``status=valid`` does **not** attest successful execution. Only a
    separate host verifier (evaluation fixtures) can set a task outcome.
    """

    status: PlanStatus
    nodes: tuple[PlanNode, ...]
    edges: tuple[PlanEdge, ...]
    topological_order: tuple[str, ...]
    supplied_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    diagnostics: tuple[PlanDiagnostic, ...]
    snapshot_id: str
    policy_id: str
    policy_digest: str
    schema_version: str = PLAN_SCHEMA
    #: False whenever status is invalid, including cycle/budget cases.
    executable: bool = False
    #: Legacy cycle-break order for diagnostics only — never executable.
    legacy_order: tuple[str, ...] | None = None
    required_permissions: frozenset[str] = frozenset()

    @property
    def structurally_valid(self) -> bool:
        return self.status == "valid"


@dataclass(frozen=True)
class HostVerificationReport:
    """Structural vs task-outcome report (AC-S08-04). Schema only — runner
    lives in evaluation fixtures, not the runtime planner.
    """

    structural_validity: PlanStatus
    verified_task_outcome: TaskOutcome
    plan_snapshot_id: str
    verifier_type: str
    verifier_id: str
    verifier_version: str
    verifier_artifact_digest: str
    schema_version: str = HOST_VERIFICATION_REPORT_SCHEMA
    details: str | None = None


def compose(
    selected_ids: Sequence[str],
    context: RouteContext,
    snapshot: CompositionSnapshot,
    limits: CompositionLimits | None = None,
    *,
    server_policy: ServerPermissionPolicy,
    trust_view: TrustViewFn,
) -> Plan:
    """Pure typed composition: ``compose(...) -> Plan/1`` (C5).

    Revalidates every node under one snapshot/eligibility policy. Exceptions
    from trust views are treated as deny. Never returns a partial executable
    prefix on cycle/budget/conflict/ambiguity/unsatisfied failure.
    """
    lim = limits if limits is not None else CompositionLimits()
    diagnostics: list[PlanDiagnostic] = []
    edges: list[PlanEdge] = []
    selected = list(dict.fromkeys(selected_ids))

    if not selected:
        return _invalid_plan(
            snapshot,
            diagnostics=[
                PlanDiagnostic(code=REASON_UNSATISFIED, message="empty selection"),
            ],
        )

    # Working set: engram_id -> (node, depth from nearest selected root)
    working: dict[str, tuple[CompositionNode, int]] = {}
    for eid in selected:
        node = snapshot.get(eid)
        if node is None:
            diagnostics.append(
                PlanDiagnostic(
                    code=REASON_DANGLING_DEPENDENCY,
                    message=f"selected id not in snapshot: {eid}",
                    subject_id=eid,
                )
            )
            return _invalid_plan(snapshot, diagnostics=diagnostics)
        working[eid] = (node, 0)

    if len(working) > lim.max_nodes:
        diagnostics.append(
            PlanDiagnostic(
                code=REASON_BUDGET_EXCEEDED,
                message=f"selected set exceeds max_nodes={lim.max_nodes}",
            )
        )
        return _invalid_plan(snapshot, diagnostics=diagnostics)

    # Expand relations.requires and artifact producers iteratively.
    frontier = list(selected)
    while frontier:
        current_id = frontier.pop(0)
        current, depth = working[current_id]

        eligibility_ok, elig_diags, unsatisfied = _revalidate_node(
            current, context, server_policy, trust_view
        )
        diagnostics.extend(elig_diags)
        if not eligibility_ok:
            return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

        # Exact-revision requires expansion (C1/C5).
        requires = list(current.subject.relations.requires) if current.subject.relations else []
        for ref in requires:
            if len(edges) >= lim.max_edges:
                diagnostics.append(
                    PlanDiagnostic(
                        code=REASON_BUDGET_EXCEEDED,
                        message=f"normative edge budget exceeded (max_edges={lim.max_edges})",
                        subject_id=current_id,
                    )
                )
                return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

            child = snapshot.resolve(ref.id, ref.version)
            if child is None:
                # Wrong revision present?
                present = snapshot.get(ref.id)
                if present is not None and present.version != ref.version:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_VERSION_MISMATCH,
                            message=(
                                f"relations.requires pin {ref.id}@{ref.version} "
                                f"but snapshot has version {present.version}"
                            ),
                            subject_id=current_id,
                            related_ids=(ref.id,),
                        )
                    )
                else:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_DANGLING_DEPENDENCY,
                            message=f"dangling relations.requires {ref.id}@{ref.version}",
                            subject_id=current_id,
                            related_ids=(ref.id,),
                        )
                    )
                return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

            # Ordering: prerequisite (child) before dependent (current).
            edges.append(PlanEdge(type="requires", src_id=child.id, dst_id=current_id))
            if child.id not in working:
                child_depth = depth + 1
                if child_depth > lim.max_depth:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_BUDGET_EXCEEDED,
                            message=f"depth budget exceeded (max_depth={lim.max_depth})",
                            subject_id=current_id,
                            related_ids=(child.id,),
                        )
                    )
                    return _invalid_plan(
                        snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working)
                    )
                if len(working) >= lim.max_nodes:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_BUDGET_EXCEEDED,
                            message=f"node budget exceeded (max_nodes={lim.max_nodes})",
                            subject_id=current_id,
                            related_ids=(child.id,),
                        )
                    )
                    return _invalid_plan(
                        snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working)
                    )
                working[child.id] = (child, child_depth)
                frontier.append(child.id)

        # Artifact capability resolution (inventory first, then producers).
        for cap_id in unsatisfied:
            req = _find_artifact_requirement(current.subject, cap_id)
            if req is None:
                diagnostics.append(
                    PlanDiagnostic(
                        code=REASON_UNSATISFIED,
                        message=f"missing artifact capability {cap_id}",
                        subject_id=current_id,
                    )
                )
                return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

            if _inventory_satisfies(context, req):
                continue

            choice, amb_diag = _resolve_provider(
                req,
                consumer_id=current_id,
                working=working,
                selected=set(selected),
                snapshot=snapshot,
                context=context,
                server_policy=server_policy,
                trust_view=trust_view,
            )
            if amb_diag is not None:
                diagnostics.append(amb_diag)
                return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))
            if choice is None:
                diagnostics.append(
                    PlanDiagnostic(
                        code=REASON_UNSATISFIED,
                        message=f"no eligible producer for artifact {cap_id}",
                        subject_id=current_id,
                    )
                )
                return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

            if len(edges) >= lim.max_edges:
                diagnostics.append(
                    PlanDiagnostic(
                        code=REASON_BUDGET_EXCEEDED,
                        message=f"normative edge budget exceeded (max_edges={lim.max_edges})",
                        subject_id=current_id,
                    )
                )
                return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

            edges.append(
                PlanEdge(
                    type="produces",
                    src_id=choice.id,
                    dst_id=current_id,
                    capability_id=cap_id,
                )
            )
            if choice.id not in working:
                child_depth = depth + 1
                if child_depth > lim.max_depth:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_BUDGET_EXCEEDED,
                            message=f"depth budget exceeded (max_depth={lim.max_depth})",
                            subject_id=current_id,
                            related_ids=(choice.id,),
                        )
                    )
                    return _invalid_plan(
                        snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working)
                    )
                if len(working) >= lim.max_nodes:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_BUDGET_EXCEEDED,
                            message=f"node budget exceeded (max_nodes={lim.max_nodes})",
                            subject_id=current_id,
                            related_ids=(choice.id,),
                        )
                    )
                    return _invalid_plan(
                        snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working)
                    )
                working[choice.id] = (choice, child_depth)
                frontier.append(choice.id)

    # Final eligibility pass on the closed working set (one policy snapshot).
    for _eid, (node, _depth) in list(working.items()):
        ok, diags, _unsat = _revalidate_node(node, context, server_policy, trust_view)
        diagnostics.extend(diags)
        if not ok:
            return _invalid_plan(snapshot, diagnostics=diagnostics, nodes=_plan_nodes(working))

    # before / supersedes / conflicts_with / alternatives among working set.
    conflict = _apply_relation_and_capability_edges(
        working=working,
        edges=edges,
        diagnostics=diagnostics,
        lim=lim,
    )
    if conflict:
        return _invalid_plan(
            snapshot,
            diagnostics=diagnostics,
            nodes=_plan_nodes(working),
            edges=tuple(edges),
        )

    if len(edges) > lim.max_edges:
        diagnostics.append(
            PlanDiagnostic(
                code=REASON_BUDGET_EXCEEDED,
                message=f"normative edge budget exceeded (max_edges={lim.max_edges})",
            )
        )
        return _invalid_plan(
            snapshot,
            diagnostics=diagnostics,
            nodes=_plan_nodes(working),
            edges=tuple(edges),
        )

    order, cycle = _stable_topo_sort(working, edges)
    if cycle:
        diagnostics.append(
            PlanDiagnostic(
                code=REASON_CYCLE,
                message="cycle in normative composition graph; no executable prefix",
                related_ids=tuple(sorted(working)),
            )
        )
        legacy = _legacy_cycle_break_order(working, edges)
        return _invalid_plan(
            snapshot,
            diagnostics=diagnostics,
            nodes=_plan_nodes(working),
            edges=tuple(edges),
            legacy_order=tuple(legacy),
        )

    supplied = _supplied_capabilities(working, context)
    missing = _missing_after_plan(working, context, supplied)
    if missing:
        for cap in missing:
            diagnostics.append(
                PlanDiagnostic(
                    code=REASON_UNSATISFIED,
                    message=f"unsatisfied artifact capability remains: {cap}",
                )
            )
        return _invalid_plan(
            snapshot,
            diagnostics=diagnostics,
            nodes=_plan_nodes(working),
            edges=tuple(edges),
        )

    plan_nodes = _plan_nodes(working)
    perms: set[str] = set()
    for n in plan_nodes:
        perms |= set(n.required_permissions)

    return Plan(
        status="valid",
        nodes=plan_nodes,
        edges=tuple(edges),
        topological_order=tuple(order),
        supplied_capabilities=tuple(supplied),
        missing_capabilities=(),
        diagnostics=tuple(diagnostics),
        snapshot_id=snapshot.snapshot_id,
        policy_id=snapshot.policy_id,
        policy_digest=snapshot.policy_digest,
        executable=True,
        required_permissions=frozenset(perms),
    )


def host_verification_report(
    plan: Plan,
    *,
    verified_task_outcome: TaskOutcome,
    verifier_type: str,
    verifier_id: str,
    verifier_version: str,
    verifier_artifact_digest: str,
    details: str | None = None,
) -> HostVerificationReport:
    """Build a report that keeps structural validity distinct from task outcome.

    The sandboxed deterministic verifier runner belongs to evaluation
    fixtures; this constructor only shapes the report schema for S14.
    Empirical outcomes must never be upgraded to ``pass`` without a real
    verifier run — callers pass ``unevaluated`` when not executed.
    """
    return HostVerificationReport(
        structural_validity=plan.status,
        verified_task_outcome=verified_task_outcome,
        plan_snapshot_id=plan.snapshot_id,
        verifier_type=verifier_type,
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        verifier_artifact_digest=verifier_artifact_digest,
        details=details,
    )


# ---------------------------------------------------------------------------
# Plan/1 helpers
# ---------------------------------------------------------------------------


def _revalidate_node(
    node: CompositionNode,
    context: RouteContext,
    server_policy: ServerPermissionPolicy,
    trust_view: TrustViewFn,
) -> tuple[bool, list[PlanDiagnostic], tuple[str, ...]]:
    """Evaluate eligibility; exceptions ⇒ deny."""
    try:
        trust = trust_view(node.id)
    except Exception as exc:  # noqa: BLE001 — fail closed (C5/C2)
        return (
            False,
            [
                PlanDiagnostic(
                    code=REASON_TRUST_EXCEPTION,
                    message=f"trust view raised: {type(exc).__name__}",
                    subject_id=node.id,
                )
            ],
            (),
        )

    result = evaluate_eligibility(
        node.subject,
        context,
        trust,
        server_policy,
        path="dependency",
        expected_content_digest=node.content_digest,
    )
    diags: list[PlanDiagnostic] = []
    for code in result.reason_codes:
        # C2: surface missing context fields on context_required so route()
        # can abstain without silently bypassing new constraints (S07 wire-up).
        related = (
            tuple(result.missing_fields)
            if code == REASON_CONTEXT_REQUIRED and result.missing_fields
            else ()
        )
        diags.append(
            PlanDiagnostic(
                code=code,
                message=f"eligibility denied: {code}",
                subject_id=node.id,
                related_ids=related,
            )
        )
    return result.eligible, diags, result.unsatisfied_artifacts


def _find_artifact_requirement(subject: EligibilitySubject, cap_id: str) -> RequiredCapability | None:
    caps = subject.capabilities
    if caps is None:
        return None
    for req in caps.requires:
        if req.kind == "artifact" and req.id == cap_id:
            return req
    return None


def _inventory_satisfies(context: RouteContext, req: RequiredCapability) -> bool:
    inventory = context.artifact_inventory
    if inventory is None:
        return False
    for entry in inventory:
        if entry.id != req.id:
            continue
        if req.version is None:
            return True
        if entry.version is None:
            return False
        return version_satisfies(entry.version, req.version)
    return False


def _producer_matches(prod: ProducedCapability, req: RequiredCapability) -> bool:
    if prod.id != req.id:
        return False
    if req.version is None:
        return True
    if prod.version is None:
        return False
    return version_satisfies(prod.version, req.version)


def _resolve_provider(
    req: RequiredCapability,
    *,
    consumer_id: str,
    working: Mapping[str, tuple[CompositionNode, int]],
    selected: set[str],
    snapshot: CompositionSnapshot,
    context: RouteContext,
    server_policy: ServerPermissionPolicy,
    trust_view: TrustViewFn,
) -> tuple[CompositionNode | None, PlanDiagnostic | None]:
    """Pick a single eligible producer or report ambiguous_provider / none."""
    candidates: list[CompositionNode] = []
    for node in snapshot.nodes.values():
        caps = node.subject.capabilities
        if caps is None:
            continue
        if not any(_producer_matches(p, req) for p in caps.produces):
            continue
        ok, _diags, _unsat = _revalidate_node(node, context, server_policy, trust_view)
        if not ok:
            continue
        candidates.append(node)

    if not candidates:
        return None, None

    # Explicit selected alternative: producer already selected or in working set.
    in_plan = [c for c in candidates if c.id in working or c.id in selected]
    if len(in_plan) == 1:
        return in_plan[0], None
    if len(in_plan) > 1:
        return None, PlanDiagnostic(
            code=REASON_AMBIGUOUS_PROVIDER,
            message=f"multiple selected producers for {req.id}",
            subject_id=consumer_id,
            related_ids=tuple(sorted(c.id for c in in_plan)),
        )

    # Consumer-authored alternatives list (optional substitutes).
    consumer = working[consumer_id][0]
    alt_ids: set[str] = set()
    if consumer.subject.capabilities is not None:
        alt_ids = set(consumer.subject.capabilities.alternatives)
    preferred_alts = [c for c in candidates if c.id in alt_ids]
    if len(preferred_alts) == 1:
        return preferred_alts[0], None
    if len(preferred_alts) > 1:
        return None, PlanDiagnostic(
            code=REASON_AMBIGUOUS_PROVIDER,
            message=f"multiple alternative producers for {req.id}",
            subject_id=consumer_id,
            related_ids=tuple(sorted(c.id for c in preferred_alts)),
        )

    # Deterministic policy preference.
    pref = snapshot.preferred_providers.get(req.id)
    if pref is not None:
        for c in candidates:
            if c.id == pref:
                return c, None

    if len(candidates) == 1:
        return candidates[0], None

    return None, PlanDiagnostic(
        code=REASON_AMBIGUOUS_PROVIDER,
        message=f"ambiguous_provider for artifact {req.id}",
        subject_id=consumer_id,
        related_ids=tuple(sorted(c.id for c in candidates)),
    )


def _apply_relation_and_capability_edges(
    *,
    working: Mapping[str, tuple[CompositionNode, int]],
    edges: list[PlanEdge],
    diagnostics: list[PlanDiagnostic],
    lim: CompositionLimits,
) -> bool:
    """Add before/supersedes/conflicts/alternatives; return True on hard conflict/budget."""
    ids = set(working)

    def _append(edge: PlanEdge, *, subject_id: str) -> bool:
        """Append if under budget; otherwise diagnose and signal stop (True)."""
        if len(edges) >= lim.max_edges:
            diagnostics.append(
                PlanDiagnostic(
                    code=REASON_BUDGET_EXCEEDED,
                    message=f"normative edge budget exceeded (max_edges={lim.max_edges})",
                    subject_id=subject_id,
                )
            )
            return True
        edges.append(edge)
        return False

    for eid, (node, _) in working.items():
        rel = node.subject.relations
        caps: Capabilities | None = node.subject.capabilities

        if rel is not None:
            for ref in rel.before:
                target = working.get(ref.id)
                if target is None:
                    continue  # absent → inactive (C1)
                tnode, _ = target
                if tnode.version != ref.version:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_INVALID_REFERENCE,
                            message=(
                                f"relations.before pin {ref.id}@{ref.version} "
                                f"but selected revision is {tnode.version}"
                            ),
                            subject_id=eid,
                            related_ids=(ref.id,),
                        )
                    )
                    return True
                # before: ref must precede eid (ref → before → eid)
                if _append(PlanEdge(type="before", src_id=ref.id, dst_id=eid), subject_id=eid):
                    return True

            for ref in rel.supersedes:
                target = working.get(ref.id)
                if target is None:
                    continue
                tnode, _ = target
                if tnode.version != ref.version:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_INVALID_REFERENCE,
                            message=(
                                f"relations.supersedes pin {ref.id}@{ref.version} "
                                f"but selected revision is {tnode.version}"
                            ),
                            subject_id=eid,
                            related_ids=(ref.id,),
                        )
                    )
                    return True
                # Both exact revisions in plan → conflict (C1).
                diagnostics.append(
                    PlanDiagnostic(
                        code=REASON_CONFLICT,
                        message=(
                            f"supersedes conflict: {eid} supersedes "
                            f"{ref.id}@{ref.version} and both are selected"
                        ),
                        subject_id=eid,
                        related_ids=(ref.id,),
                    )
                )
                if _append(PlanEdge(type="supersedes", src_id=eid, dst_id=ref.id), subject_id=eid):
                    return True
                return True

        if caps is not None:
            for other in caps.conflicts_with:
                if other in ids:
                    diagnostics.append(
                        PlanDiagnostic(
                            code=REASON_CONFLICT,
                            message=f"conflicts_with {other}",
                            subject_id=eid,
                            related_ids=(other,),
                        )
                    )
                    if _append(
                        PlanEdge(type="conflicts_with", src_id=eid, dst_id=other),
                        subject_id=eid,
                    ):
                        return True
                    return True
            for other in caps.alternatives:
                if other in ids:
                    if _append(
                        PlanEdge(type="alternatives", src_id=eid, dst_id=other, optional=True),
                        subject_id=eid,
                    ):
                        return True

    return False


def _stable_topo_sort(
    working: Mapping[str, tuple[CompositionNode, int]],
    edges: Sequence[PlanEdge],
) -> tuple[list[str], bool]:
    """Classic Kahn sort: repeatedly emit the single lexicographically-least ready id.

    Ordering edges: dst depends on src (src before dst). Ties broken by engram id.
    Returns ``(order, cycle_detected)``. On cycle, ``order`` is empty (no
    executable prefix).
    """
    names = set(working)
    in_degree: dict[str, int] = dict.fromkeys(names, 0)
    dependents: dict[str, list[str]] = {n: [] for n in names}

    for edge in edges:
        if edge.type in {"conflicts_with", "supersedes", "alternatives"}:
            continue  # not ordering edges
        if edge.src_id not in names or edge.dst_id not in names:
            continue
        # src must precede dst
        in_degree[edge.dst_id] += 1
        dependents[edge.src_id].append(edge.dst_id)

    ready = [n for n, deg in in_degree.items() if deg == 0]
    heapq.heapify(ready)
    order: list[str] = []
    remaining = dict(in_degree)
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        remaining[name] = -1
        for dep in dependents.get(name, []):
            remaining[dep] -= 1
            if remaining[dep] == 0:
                heapq.heappush(ready, dep)

    if len(order) != len(names):
        return [], True
    return order, False


def _legacy_cycle_break_order(
    working: Mapping[str, tuple[CompositionNode, int]],
    edges: Sequence[PlanEdge],
) -> list[str]:
    """Diagnostic-only cycle break (lexicographic); never marked executable."""
    names = set(working)
    in_degree: dict[str, int] = dict.fromkeys(names, 0)
    dependents: dict[str, list[str]] = {n: [] for n in names}
    edge_keys: list[tuple[str, str]] = []
    for edge in edges:
        if edge.type in {"conflicts_with", "supersedes", "alternatives"}:
            continue
        if edge.src_id not in names or edge.dst_id not in names:
            continue
        in_degree[edge.dst_id] += 1
        dependents[edge.src_id].append(edge.dst_id)
        edge_keys.append((edge.src_id, edge.dst_id))

    order: list[str] = []
    remaining = dict(in_degree)
    while len(order) < len(names):
        ready = sorted(n for n, deg in remaining.items() if deg == 0 and n not in order)
        if not ready:
            blocked = [n for n in names if n not in order]
            weakest: tuple[str, str] | None = None
            for src, dst in sorted(edge_keys):
                if dst in blocked and remaining.get(dst, 0) > 0:
                    weakest = (src, dst)
                    break
            if weakest is None:
                order.extend(sorted(blocked))
                break
            remaining[weakest[1]] -= 1
            continue
        for name in ready:
            order.append(name)
            remaining[name] = -1
            for dep in dependents.get(name, []):
                if dep not in order:
                    remaining[dep] -= 1
    return order


def _plan_nodes(working: Mapping[str, tuple[CompositionNode, int]]) -> tuple[PlanNode, ...]:
    nodes = [
        PlanNode(
            engram_id=node.id,
            version=node.version,
            content_digest=node.content_digest,
            asset_digests=dict(node.asset_digests),
            procedure_ref=node.procedure_ref,
            required_permissions=node.subject.required_permissions,
            risk=node.subject.risk,
        )
        for node, _ in working.values()
    ]
    return tuple(sorted(nodes, key=lambda n: n.engram_id))


def _supplied_capabilities(
    working: Mapping[str, tuple[CompositionNode, int]],
    context: RouteContext,
) -> list[str]:
    supplied: list[str] = []
    if context.artifact_inventory is not None:
        for entry in context.artifact_inventory:
            supplied.append(entry.id)
    for node, _ in working.values():
        caps = node.subject.capabilities
        if caps is None:
            continue
        for prod in caps.produces:
            supplied.append(prod.id)
    return list(dict.fromkeys(supplied))


def _missing_after_plan(
    working: Mapping[str, tuple[CompositionNode, int]],
    context: RouteContext,
    supplied: Sequence[str],
) -> list[str]:
    supplied_set = set(supplied)
    missing: list[str] = []
    for node, _ in working.values():
        caps = node.subject.capabilities
        if caps is None:
            continue
        for req in caps.requires:
            if req.kind != "artifact":
                continue
            if _inventory_satisfies(context, req):
                continue
            # Satisfied if any in-plan producer matches.
            if any(
                _producer_matches(p, req)
                for other, _ in working.values()
                if other.subject.capabilities
                for p in other.subject.capabilities.produces
            ):
                continue
            if req.id not in supplied_set:
                missing.append(req.id)
    return list(dict.fromkeys(missing))


def _invalid_plan(
    snapshot: CompositionSnapshot,
    *,
    diagnostics: Sequence[PlanDiagnostic],
    nodes: tuple[PlanNode, ...] = (),
    edges: tuple[PlanEdge, ...] = (),
    legacy_order: tuple[str, ...] | None = None,
) -> Plan:
    return Plan(
        status="invalid",
        nodes=nodes,
        edges=edges,
        topological_order=(),  # no executable prefix
        supplied_capabilities=(),
        missing_capabilities=tuple(
            d.subject_id for d in diagnostics if d.code == REASON_UNSATISFIED and d.subject_id
        ),
        diagnostics=tuple(diagnostics),
        snapshot_id=snapshot.snapshot_id,
        policy_id=snapshot.policy_id,
        policy_digest=snapshot.policy_digest,
        executable=False,
        legacy_order=legacy_order,
    )


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_EDGES",
    "DEFAULT_MAX_NODES",
    "HOST_VERIFICATION_REPORT_SCHEMA",
    "PLAN_EDGE_TYPES",
    "PLAN_SCHEMA",
    "REASON_AMBIGUOUS_PROVIDER",
    "REASON_INVALID_REFERENCE",
    "REASON_TRUST_EXCEPTION",
    "REASON_UNSATISFIED",
    "CompositionLimits",
    "CompositionNode",
    "CompositionPlan",
    "CompositionSnapshot",
    "HostVerificationReport",
    "Plan",
    "PlanDiagnostic",
    "PlanEdge",
    "PlanNode",
    "compose",
    "expand",
    "host_verification_report",
    "plan_confidence",
]
