"""``route()`` -- the full v1 algorithm (spec §3.3): cosine seeds -> sparse
PPR activation -> inhibition -> weighted score -> hub penalty -> context
conditioning -> community rerank -> composition-plan expansion.

AC-024 (statically enforced by ``tests/unit/test_p0_enforcement.py``):
this module MUST NOT import ``magicite.storage.durable`` or
``magicite.engram.writer`` -- ``route()`` is a hot-path, read-mostly tool
(the only durable write it ever performs is Tier-C bookkeeping via plain
``eph_*`` tables, spec §3.3 step 11). ``core/edge_weight.py`` is
framework-free (no DB handle, no forbidden import) so this module may
import it freely -- it is the one place an edge's routing weight
(``S_eff``, spec §3.3.1) is derived from ``edge.storage_strength``
(AC-040).

**Two judgment calls worth flagging explicitly** (the spec text is not
fully self-contained on either):

1. *Hub-penalty "usage PageRank"* (step 7) -- **DELIBERATELY NOT REWIRED
   by DECLARED-EDGES-AMENDED (2026-08-15).** This module computes a
   *structural* PageRank for hub detection -- edge weight = ``type_gain``
   only, ignoring both ``S_edge`` and its ``S_eff`` successor.
   Originally that was because S_edge starts at 0.0 for every
   freshly-``declared`` edge and nothing potentiated it until Dream
   existed (M4), which would have made the hub penalty permanently inert
   pre-consolidation; that specific reason is now obsolete (§3.3.1 gives
   every declared edge a nonzero ``S_eff`` from the moment it is
   registered). The **restated** reason: this is deliberately a
   *structural* centrality metric, not a usage-weighted one, and it is
   the one graph mechanism the benchmark measured to help (+0.0286 Hit@1,
   +0.0362 MRR on 70 engrams / 210 queries) -- rewiring a measured-good
   component onto an unmeasured hunch is not warranted here. Whether it
   should instead be weighted by *learned* topology is an open experiment
   (FORGE's D3, decisions/DECLARED-EDGES-AMENDED.md CF-4).
   ``core/activation.py::page_rank`` is the shared power-iteration
   primitive either metric would use, so nothing here is thrown away if
   D3 later wires a usage-weighted variant in.
2. *"pitfalls declare that fault_class"* (step 7b, ``recent_failures``):
   the DDL (spec §2.2) has no ``engram_pitfall`` table at all -- the only
   durable ``fault_class`` column lives on ``engram_step`` (Procedure
   steps). This module resolves ``recent_failures`` against
   ``engram_step.fault_class``, the one column the schema actually
   offers; nothing currently populates it (that lands with Dream's audit
   phase, M4), so this conditioning is real code that is honestly inert
   until then.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from magicite.config import Config
from magicite.core import activation as activation_mod
from magicite.core import composition as composition_mod
from magicite.core import edge_weight as edge_weight_mod
from magicite.core import session as session_mod
from magicite.core.decay_math import effective_value
from magicite.embeddings.base import Embedder
from magicite.engram.model import ROUTABLE_STATUSES
from magicite.storage import ephemeral as ephemeral_mod

INTENT_TRUNCATE = 200

#: spec §3.3 step 4: positive-weight edge types fed to the activation
#: graph. ``inhibits`` is deliberately excluded -- it is applied as a
#: separate multiplicative pass (step 5), never as positive graph mass.
_ACTIVATION_EDGE_TYPES: tuple[str, ...] = ("co_activation", "composes", "depends_on", "similar_to")


@dataclass
class Candidate:
    rank: int
    id: str
    name: str
    intent_does: str
    intent_use_when: str
    score: float
    status: str
    exposure_count: int
    body_ref: str
    signal_tier_0: bool = True


@dataclass
class RouteOutcome:
    candidates: list[Candidate]
    composition_plan: list[str]
    plan_confidence: float
    instructions: str
    session_id: str
    registry_size: int
    unresolved_context: list[str] = field(default_factory=list)


#: docs/05 verbatim self-report instruction text (Tier-1 signal path).
ROUTE_INSTRUCTIONS = (
    "After applying a skill, call signal_use(skill_id) with its id. When the task "
    "outcome is known (tests pass, user confirms), call signal_outcome(valence, "
    "skill_ids) to drive learning."
)


def _fetch_candidates(conn: sqlite3.Connection, model_name: str) -> list[sqlite3.Row]:
    """spec §3.3 step 2's ``routable`` filter, plus everything later steps
    need (excitability, retrieval strength) in one query."""
    placeholders = ",".join("?" for _ in ROUTABLE_STATUSES)
    return conn.execute(
        f"""
        SELECT e.id, e.name, e.intent_does, e.intent_use_when, e.status, e.exposure_count,
               e.path, e.excitability, x.vec, x.dim,
               COALESCE(r.r, 0.0) AS retrieval_strength, r.r_decayed_at AS retrieval_decayed_at
        FROM engram e
        JOIN eph_embedding x ON x.engram_id = e.id AND x.model = ?
        LEFT JOIN eph_retrieval r ON r.engram_id = e.id
        WHERE e.status IN ({placeholders}) AND e.verification_status = 'verified'
        """,
        (model_name, *ROUTABLE_STATUSES),
    ).fetchall()


def _fetch_activation_edges(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in _ACTIVATION_EDGE_TYPES)
    return conn.execute(
        f"""
        SELECT src_id, dst_id, type, storage_strength, provenance FROM edge
        WHERE type IN ({placeholders}) AND dangling = 0 AND dst_id IS NOT NULL
        """,
        _ACTIVATION_EDGE_TYPES,
    ).fetchall()


def _fetch_inhibition_edges(
    conn: sqlite3.Connection, *, declared_edge_strength: float
) -> list[tuple[str, str, float]]:
    """spec §3.3 step 5: ``(src_j, dst_i, S_eff_ji)`` triples -- ``S_eff``,
    NOT the raw ``storage_strength`` column (§3.3.1 call site 2): a
    declared ``inhibits`` edge must scale the inhibited node's activation
    even though it is never Hebbian-potentiated."""
    rows = conn.execute(
        "SELECT src_id, dst_id, storage_strength, provenance FROM edge "
        "WHERE type = 'inhibits' AND dangling = 0 AND dst_id IS NOT NULL"
    ).fetchall()
    return [
        (
            r["src_id"],
            r["dst_id"],
            edge_weight_mod.effective_strength(
                float(r["storage_strength"]), r["provenance"], declared_edge_strength
            ),
        )
        for r in rows
    ]


def _apply_context_conditioning(
    conn: sqlite3.Connection,
    node_ids: list[str],
    id_by_name: dict[str, str],
    score: np.ndarray,
    context: dict | None,
    *,
    context_gain: float,
    pref_gain: float,
) -> tuple[np.ndarray, set[str], list[str]]:
    """spec §3.3 step 7b. Returns ``(conditioned_score, hard_excluded_ids,
    unresolved_context_strings)``."""
    if not context:
        return score, set(), []

    out = score.copy()
    excluded: set[str] = set()
    unresolved: list[str] = []
    index = {nid: i for i, nid in enumerate(node_ids)}

    project_tag = context.get("project_tag")
    if project_tag:
        node = conn.execute("SELECT id FROM context_node WHERE name = ?", (project_tag,)).fetchone()
        if node is None:
            unresolved.append(project_tag)
        else:
            linked = conn.execute(
                "SELECT engram_id, weight FROM engram_context WHERE context_id = ?", (node["id"],)
            ).fetchall()
            for row in linked:
                i = index.get(row["engram_id"])
                if i is not None:
                    out[i] *= 1 + context_gain * row["weight"]

    for fault_class in context.get("recent_failures") or []:
        matches = conn.execute(
            "SELECT DISTINCT engram_id FROM engram_step WHERE fault_class = ?", (fault_class,)
        ).fetchall()
        if not matches:
            unresolved.append(fault_class)
            continue
        for row in matches:
            i = index.get(row["engram_id"])
            if i is not None:
                out[i] *= 1 + context_gain

    for pref in context.get("user_prefs") or []:
        exclude = pref.startswith("-")
        target_name = pref[1:] if exclude else pref
        target_id = id_by_name.get(target_name)
        if target_id is None or target_id not in index:
            unresolved.append(pref)
            continue
        if exclude:
            excluded.add(target_id)
        else:
            out[index[target_id]] *= 1 + pref_gain

    return out, excluded, unresolved


def _community_rerank(conn: sqlite3.Connection, kept_ids: list[str], scores: dict[str, float]) -> list[str]:
    """spec §3.3 step 8: group by ``engram_community``, ``community_score =
    max + 0.25*mean``, keep the top-2 communities.

    A registry that has never run ``sync()`` (or whose ``sync()`` produced
    only a single group) has an empty/degenerate ``engram_community``
    table -- every candidate then falls into one shared "unassigned"
    bucket, so this degrades to a no-op (nothing gets filtered) rather
    than an accidental, unintended clip to whatever happened to be in the
    top 2 of a meaningless singleton partition.
    """
    if not kept_ids:
        return kept_ids
    rows = conn.execute("SELECT engram_id, community_id FROM engram_community").fetchall()
    community_by_id: dict[str, int] = {r["engram_id"]: r["community_id"] for r in rows}

    groups: dict[object, list[str]] = {}
    for nid in kept_ids:
        key = community_by_id.get(nid, "__unassigned__")
        groups.setdefault(key, []).append(nid)

    if len(groups) <= 1:
        return kept_ids

    ranked_groups: list[tuple[float, object]] = []
    for group_key, members in groups.items():
        vals = [scores[m] for m in members]
        community_score = max(vals) + 0.25 * (sum(vals) / len(vals))
        ranked_groups.append((community_score, group_key))
    ranked_groups.sort(key=lambda t: t[0], reverse=True)
    top_keys = {group_key for _, group_key in ranked_groups[:2]}

    return [nid for nid in kept_ids if community_by_id.get(nid, "__unassigned__") in top_keys]


def route(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    query: str,
    context: dict | None = None,
    k: int = 5,
    session_id: str | None = None,
) -> RouteOutcome:
    # core/session.py (M3): the one session-resolution rule every
    # session-participating tool follows (spec §3.3) -- mint/reuse/expire,
    # in one place, instead of route() rolling its own uuid4() + upsert.
    sid = session_mod.resolve_id(cfg, conn, session_id)
    now = datetime.now(UTC).isoformat()

    qvec = embedder.embed(query)
    rows = _fetch_candidates(conn, embedder.model_name)
    registry_size = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    if not rows:
        return RouteOutcome(
            candidates=[],
            composition_plan=[],
            plan_confidence=0.0,
            instructions=ROUTE_INSTRUCTIONS,
            session_id=sid,
            registry_size=registry_size,
            unresolved_context=_echo_all_context(context),
        )

    # step 1-2: cosine seeds, over every routable+verified+embedded engram.
    node_ids: list[str] = []
    id_by_name: dict[str, str] = {}
    row_by_id: dict[str, sqlite3.Row] = {}
    cosine_list: list[float] = []
    retrieval_list: list[float] = []
    excitability_list: list[float] = []
    for row in rows:
        vec = np.frombuffer(row["vec"], dtype=np.float32)
        node_ids.append(row["id"])
        id_by_name[row["name"]] = row["id"]
        row_by_id[row["id"]] = row
        cosine_list.append(float(np.dot(qvec, vec)))
        # M4 hardening: R is decayed *at read time* (spec §6.1: "Evaluated
        # lazily at read time"), not only when Dream's Phase 3 happens to
        # materialise it -- otherwise a stale, undecayed R keeps
        # contributing full weight to score_i between Dream runs, which is
        # exactly the "reversible and expires naturally" property docs/03
        # promises for R but that was not actually true until this read
        # path decayed it too.
        decayed_r = effective_value(
            float(row["retrieval_strength"]), row["retrieval_decayed_at"], now, cfg.lambda_r_per_day
        )
        retrieval_list.append(decayed_r)
        excitability_list.append(float(row["excitability"]))
    cosine = np.array(cosine_list, dtype=np.float64)

    m = min(max(5 * k, 25), 200)
    seed_order = sorted(range(len(node_ids)), key=lambda i: cosine[i], reverse=True)[:m]
    seed_cos = {node_ids[i]: cosine[i] for i in seed_order}

    # step 3: p = softmax(cos_sim(seeds) / temperature)
    p = activation_mod.softmax_personalization(node_ids, seed_cos, temperature=cfg.temperature)

    # step 4: a = PPR(p, W, ...), W_ij = S_eff_ij * type_gain[type] (§3.3.1
    # call site 1: S_eff, NOT the raw storage_strength column -- a
    # declared composes/depends_on edge must be present in the graph at
    # declared_edge_strength * type_gain[type], not dropped as w<=0).
    edge_rows = _fetch_activation_edges(conn)
    activation_edges = [
        (
            r["src_id"],
            r["dst_id"],
            edge_weight_mod.effective_strength(
                float(r["storage_strength"]), r["provenance"], cfg.declared_edge_strength
            )
            * cfg.type_gain.get(r["type"], 0.0),
        )
        for r in edge_rows
    ]
    graph = activation_mod.build_graph(node_ids, activation_edges)
    a = activation_mod.personalized_pagerank(
        graph, p, restart=cfg.ppr_restart, max_iter=cfg.ppr_max_iter, tol=cfg.ppr_tol
    )

    # step 5: inhibition (§3.3.1 call site 2: S_eff directly, never x
    # type_gain -- type_gain['inhibits']=0.0 by design)
    a = activation_mod.apply_inhibition(
        a,
        node_ids,
        _fetch_inhibition_edges(conn, declared_edge_strength=cfg.declared_edge_strength),
        inhib_gain=cfg.inhib_gain,
    )

    # step 6: weighted score
    score_inputs = activation_mod.ScoreInputs(
        node_ids=node_ids,
        activation=a,
        cosine=cosine,
        retrieval=np.array(retrieval_list, dtype=np.float64),
        excitability=np.array(excitability_list, dtype=np.float64),
    )
    score = activation_mod.combine_scores(
        score_inputs,
        w_activation=cfg.w_activation,
        w_similarity=cfg.w_similarity,
        w_retrieval=cfg.w_retrieval,
        w_excitability=cfg.w_excitability,
    )

    # step 7: hub penalty (structural usage-PageRank proxy -- see module docstring)
    structural_edges = [
        (r["src_id"], r["dst_id"], cfg.type_gain.get(r["type"], 0.0))
        for r in edge_rows
        if cfg.type_gain.get(r["type"], 0.0) > 0
    ]
    hub_graph = activation_mod.build_graph(node_ids, structural_edges)
    usage_pagerank = activation_mod.page_rank(hub_graph)
    score = activation_mod.apply_hub_penalty(
        score, usage_pagerank, hub_penalty=cfg.hub_penalty, percentile=cfg.hub_penalty_percentile
    )

    # step 7b: context conditioning
    score, excluded_ids, unresolved_context = _apply_context_conditioning(
        conn, node_ids, id_by_name, score, context, context_gain=cfg.context_gain, pref_gain=cfg.pref_gain
    )

    kept_ids = [nid for nid in node_ids if nid not in excluded_ids]
    scores_by_id = {nid: float(score[i]) for i, nid in enumerate(node_ids)}

    # step 8: community rerank (M6 ablation switch, spec §7.3: "driven
    # from magicite.toml" -- cfg.ablation_no_communities skips the
    # top-2-communities filter entirely, the H-SCALE ablation eval/
    # ablations.py::run_no_communities compares against baseline (d)).
    if not cfg.ablation_no_communities:
        kept_ids = _community_rerank(conn, kept_ids, scores_by_id)

    # final ranking + truncation to k (deterministic tie-break by name)
    ranked = sorted(kept_ids, key=lambda nid: (-scores_by_id[nid], row_by_id[nid]["name"]))
    top_ids = ranked[:k]

    candidates = [
        Candidate(
            rank=i + 1,
            id=nid,
            name=row_by_id[nid]["name"],
            intent_does=row_by_id[nid]["intent_does"][:INTENT_TRUNCATE],
            intent_use_when=row_by_id[nid]["intent_use_when"][:INTENT_TRUNCATE],
            score=round(scores_by_id[nid], 6),
            status=row_by_id[nid]["status"],
            exposure_count=row_by_id[nid]["exposure_count"],
            body_ref=row_by_id[nid]["path"],
        )
        for i, nid in enumerate(top_ids)
    ]

    composition_plan: list[str] = []
    plan_confidence = 0.0
    if candidates:
        winner = candidates[0]
        plan = composition_mod.expand(
            conn,
            winner.id,
            winner.name,
            max_depth=cfg.plan_max_depth,
            max_size=cfg.plan_max_size,
            declared_edge_strength=cfg.declared_edge_strength,
        )
        composition_plan = plan.order
        plan_confidence = composition_mod.plan_confidence(plan)

    # step 11: Tier-C bookkeeping ONLY -- R and S are never touched here (Principle 1).
    for c in candidates:
        ephemeral_mod.bump_route_bookkeeping(conn, c.id)
    ephemeral_mod.append_event(
        conn,
        session_id=sid,
        tool="route",
        signal_tier=0,
        engram_id=candidates[0].id if candidates else None,
        payload={"query": query, "k": k, "candidate_ids": [c.id for c in candidates]},
    )

    return RouteOutcome(
        candidates=candidates,
        composition_plan=composition_plan,
        plan_confidence=plan_confidence,
        instructions=ROUTE_INSTRUCTIONS,
        session_id=sid,
        registry_size=registry_size,
        unresolved_context=unresolved_context,
    )


def _echo_all_context(context: dict | None) -> list[str]:
    """Empty-registry short-circuit: nothing can resolve, so every supplied
    context string is honestly unresolved."""
    if not context:
        return []
    out: list[str] = []
    if context.get("project_tag"):
        out.append(context["project_tag"])
    out.extend(context.get("recent_failures") or [])
    out.extend(context.get("user_prefs") or [])
    return out
