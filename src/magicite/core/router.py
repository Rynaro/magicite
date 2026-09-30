"""``route()`` -- policy-dispatched ranking (V1 S00 + legacy adaptive path).

**Stable default (``dense-v1``):** eligible cosine similarity with stable-ID
tie-breaks. Ignores retrieval strength, excitability, graph activation,
community rerank and Dream-written edge/node strengths (contracts C0, C4).

**Experimental (``experimental/adaptive-blend-v1``):** the pre-V1 algorithm
(cosine seeds -> sparse PPR activation -> inhibition -> weighted score ->
hub penalty -> context conditioning -> community rerank -> composition-plan
expansion). Opt-in only; never the implicit default.

AC-024 (statically enforced by ``tests/unit/test_p0_enforcement.py``):
this module MUST NOT import ``magicite.storage.durable`` or
``magicite.engram.writer`` -- ``route()`` is a hot-path, read-mostly tool
(the only durable write it ever performs is Tier-C bookkeeping via plain
``eph_*`` tables, spec §3.3 step 11). ``core/edge_weight.py`` is
framework-free (no DB handle, no forbidden import) so this module may
import it freely -- it is the one place an edge's routing weight
(``S_eff``, spec §3.3.1) is derived from ``edge.storage_strength``
(AC-040) on the experimental path.

**Two judgment calls worth flagging explicitly** (the spec text is not
fully self-contained on either; both apply to the experimental path):

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

import hashlib
import json
import sqlite3
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np

from magicite.config import Config
from magicite.core import activation as activation_mod
from magicite.core import calibration as calibration_mod
from magicite.core import composition as composition_mod
from magicite.core import edge_weight as edge_weight_mod
from magicite.core import eligibility as eligibility_mod
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.core import index_generation as index_gen_mod
from magicite.core import policy_store as policy_store_mod
from magicite.core import recovery_gate as recovery_gate_mod
from magicite.core import routing_policy as policy_mod
from magicite.core import session as session_mod
from magicite.core import trust as trust_mod
from magicite.core.context import RouteContext, ServerPermissionPolicy
from magicite.core.decay_math import effective_value
from magicite.embeddings import reranker as reranker_mod
from magicite.embeddings.base import Embedder, contraindication_model_name
from magicite.engram import ids as ids_mod
from magicite.engram import parser as parser_mod
from magicite.engram.assets import validate_assets
from magicite.engram.model import ROUTABLE_STATUSES, Engram
from magicite.engram.model_v1 import EngramRevisionRef, EngramV1, Relations
from magicite.errors import InvalidInputError
from magicite.storage import ephemeral as ephemeral_mod

INTENT_TRUNCATE = 200
CONTRAINDICATION_VIEW_SCHEMA = "magicite-contraindication-view/1"
ROUTE_DECISION_SCHEMA = "RouteDecision/1"
EXPLANATION_VERSION = "RouteExplanation/1"
#: Bound on composition diagnostic codes folded into RouteDecision.reason_codes.
_COMPOSITION_REASON_BOUND = 8
REASON_COMPOSITION_INVALID = "composition_invalid"
REASON_COMPOSITION_ERROR = "composition_error"

#: Default server permission ceiling when the caller does not supply one.
#: Unconstrained subjects (no required permissions/tools) remain eligible;
#: constrained subjects still fail closed against this ceiling.
DEFAULT_SERVER_POLICY = ServerPermissionPolicy(
    allowed_permissions=frozenset(),
    allowed_tools=frozenset(),
    policy_digest="magicite-default-server-policy/1",
    max_filesystem="write-project",
    max_subprocess="declared-tools",
    max_network="required",
    max_secrets="raw",
)

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
    diagnostics: dict[str, float] = field(default_factory=dict)
    content_digest: str | None = None


@dataclass(frozen=True)
class Confidence:
    value: float | None
    calibration_id: str | None


@dataclass(frozen=True)
class ExclusionSummary:
    engram_id: str
    reason_codes: tuple[str, ...]


@dataclass
class RouteDecision:
    """RouteDecision/1 (contracts.md C4). Internal; S11 owns public wrappers."""

    decision_id: str
    status: Literal["selected", "abstained", "error"]
    selected_ids: tuple[str, ...]
    candidates: list[Candidate]
    exclusions: tuple[ExclusionSummary, ...]
    score_components: dict[str, dict[str, float]]
    confidence: Confidence
    reason_codes: tuple[str, ...]
    missing_context: tuple[str, ...]
    truncations: dict[str, int]
    policy_id: str
    policy_digest: str
    policy_family: str
    config_digest: str
    calibration_digest: str | None
    query_fingerprint: str
    registry_digest: str | None = None
    schema_digest: str | None = None
    model_digest: str | None = None
    tokenizer_digest: str | None = None
    index_generation_id: str | None = None
    snapshot_id: str | None = None
    selected_content_digests: dict[str, str] = field(default_factory=dict)
    selection_mechanism: str = "dense-v1"
    propensity: dict[str, float] = field(default_factory=dict)
    explanation_version: str = EXPLANATION_VERSION
    fallback_identity: str | None = None
    operational_error: str | None = None
    #: Named C2 default-local-authorship policy (N1); fingerprinted in digests.
    default_local_authorship_policy: bool = True
    #: Where the selected policy id came from (store vs fresh-install cfg).
    policy_source: Literal["store", "config_fresh_install"] | None = None
    #: Plan/1 identity digest when composition succeeded (None on abstain/error).
    plan_digest: str | None = None
    schema_version: str = ROUTE_DECISION_SCHEMA


@dataclass
class RouteOutcome:
    candidates: list[Candidate]
    composition_plan: list[str]
    plan_confidence: float
    instructions: str
    session_id: str
    registry_size: int
    unresolved_context: list[str] = field(default_factory=list)
    policy_id: str = policy_mod.POLICY_DENSE_V1
    policy_digest: str = ""
    policy_family: str = "stable"
    #: Internal RouteDecision/1 — public wrappers owned by S11.
    decision: RouteDecision | None = None
    #: Composed Plan/1 for S11 public projection (None when composition did not run
    #: or failed before ``compose()`` returned). Carry-through only — no behaviour change.
    plan: composition_mod.Plan | None = None


#: docs/05 verbatim self-report instruction text (Tier-1 signal path).
ROUTE_INSTRUCTIONS = (
    "After applying a skill, call signal_use(skill_id) with its id. When the task "
    "outcome is known (tests pass, user confirms), call signal_outcome(valence, "
    "skill_ids) to drive learning."
)


class RerankerOperationalError(InvalidInputError):
    """Typed operational failure distinct from selection-quality abstention."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        fallback_identity: str | None = None,
    ) -> None:
        super().__init__(message, details={"operational_code": code, "fallback_identity": fallback_identity})
        self.operational_code = code
        self.fallback_identity = fallback_identity


def _fetch_candidates(conn: sqlite3.Connection, model_name: str) -> list[sqlite3.Row]:
    """Load embedded candidates for eligibility-then-score (C2 / C4).

    Includes ``quarantined`` verification rows so S06 can exclude them before
    scoring (AC-S07-01). Pending/other statuses stay out of the hot pool.
    """
    placeholders = ",".join("?" for _ in ROUTABLE_STATUSES)
    return conn.execute(
        f"""
        SELECT e.id, e.name, e.intent_does, e.intent_use_when, e.status, e.exposure_count,
               e.path, e.excitability, e.intent_not_when, e.identity_sha256, e.content_sha256,
               e.version, e.verification_status, e.origin,
               x.vec, x.dim, nx.vec AS contraindication_vec,
               COALESCE(r.r, 0.0) AS retrieval_strength, r.r_decayed_at AS retrieval_decayed_at
        FROM engram e
        JOIN eph_embedding x ON x.engram_id = e.id AND x.model = ?
        LEFT JOIN eph_embedding nx ON nx.engram_id = e.id AND nx.model = ?
        LEFT JOIN eph_retrieval r ON r.engram_id = e.id
        WHERE e.status IN ({placeholders})
          AND e.verification_status IN ('verified', 'quarantined')
        """,
        (model_name, contraindication_model_name(model_name), *ROUTABLE_STATUSES),
    ).fetchall()


def expand_composition(
    conn: sqlite3.Connection,
    winner_id: str,
    winner_name: str,
    *,
    max_depth: int,
    max_size: int,
    declared_edge_strength: float,
) -> composition_mod.CompositionPlan:
    """Retired from the stable ``route()`` path — use :func:`compose_route_plan`.

    Kept as a thin legacy wrapper for any external callers that still expect
    Kahn ``expand()`` behaviour. Prefer :func:`magicite.core.composition.expand`
    directly for eval/bench gold.
    """
    return composition_mod.expand(
        conn,
        winner_id,
        winner_name,
        max_depth=max_depth,
        max_size=max_size,
        declared_edge_strength=declared_edge_strength,
    )


@dataclass(frozen=True)
class _ComposeRouteResult:
    """Internal outcome of wiring Plan/1 into RouteOutcome composition fields."""

    ok: bool
    order_names: tuple[str, ...] = ()
    plan_confidence: float = 0.0
    plan_digest: str | None = None
    reason_codes: tuple[str, ...] = ()
    missing_context: tuple[str, ...] = ()
    #: Composed Plan/1 when ``compose()`` returned (valid or invalid). None on
    #: pre-compose failures / exceptions. Carried for S11 public projection only.
    plan: composition_mod.Plan | None = None


def _compose_limits(cfg: Config) -> composition_mod.CompositionLimits:
    """Map router knobs onto compose limits without loosening S08 defaults."""
    return composition_mod.CompositionLimits(
        max_nodes=min(int(cfg.plan_max_size), composition_mod.DEFAULT_MAX_NODES),
        max_edges=composition_mod.DEFAULT_MAX_EDGES,
        max_depth=min(int(cfg.plan_max_depth), composition_mod.DEFAULT_MAX_DEPTH),
    )


def _plan_identity_digest(plan: composition_mod.Plan) -> str:
    """Stable digest of Plan/1 identity fields (no opaque timings)."""
    payload = {
        "schema_version": plan.schema_version,
        "status": plan.status,
        "topological_order": list(plan.topological_order),
        "nodes": [
            {
                "engram_id": n.engram_id,
                "version": n.version,
                "content_digest": n.content_digest,
            }
            for n in plan.nodes
        ],
        "edges": [
            {
                "type": e.type,
                "src_id": e.src_id,
                "dst_id": e.dst_id,
                "optional": e.optional,
                "capability_id": e.capability_id,
            }
            for e in plan.edges
        ],
        "snapshot_id": plan.snapshot_id,
        "policy_id": plan.policy_id,
        "policy_digest": plan.policy_digest,
        "diagnostics": [d.code for d in plan.diagnostics],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _legacy_requires_refs(conn: sqlite3.Connection, engram_id: str) -> tuple[list[EngramRevisionRef], bool]:
    """Bridge declared depends_on/composes DB edges → relations.requires.

    Returns ``(refs, has_dangling)``. Dangling targets cannot form a valid
    EngramRevisionRef pin; callers must abstain (C5).
    """
    placeholders = ",".join("?" for _ in composition_mod.PLAN_EDGE_TYPES)
    rows = conn.execute(
        f"""
        SELECT e.dst_id, e.dst_name, e.dangling, d.version AS dst_version
        FROM edge e
        LEFT JOIN engram d ON d.id = e.dst_id
        WHERE e.src_id = ? AND e.type IN ({placeholders})
        ORDER BY e.dst_name
        """,
        (engram_id, *composition_mod.PLAN_EDGE_TYPES),
    ).fetchall()
    refs: list[EngramRevisionRef] = []
    has_dangling = False
    for row in rows:
        if row["dangling"] or row["dst_id"] is None:
            has_dangling = True
            continue
        version = int(row["dst_version"] or 1)
        refs.append(EngramRevisionRef(id=str(row["dst_id"]), version=version))
    return refs, has_dangling


def _subject_for_composition(
    subject: eligibility_mod.EligibilitySubject,
    conn: sqlite3.Connection,
    engram_id: str,
) -> tuple[eligibility_mod.EligibilitySubject, bool]:
    """Attach legacy edge requires when the artifact has no V1 relations.requires."""
    existing = (
        list(subject.relations.requires)
        if subject.relations is not None and subject.relations.requires
        else []
    )
    if existing:
        return subject, False
    refs, has_dangling = _legacy_requires_refs(conn, engram_id)
    if not refs and not has_dangling:
        return subject, False
    prior = subject.relations
    new_relations = Relations(
        requires=refs,
        before=list(prior.before) if prior is not None else [],
        supersedes=list(prior.supersedes) if prior is not None else [],
    )
    return replace(subject, relations=new_relations), has_dangling


def _row_by_engram_id(conn: sqlite3.Connection, engram_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name, path, version, status, origin, verification_status,
               content_sha256, identity_sha256
        FROM engram WHERE id = ?
        """,
        (engram_id,),
    ).fetchone()


def compose_route_plan(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    winner_id: str,
    winner_name: str,
    route_context: RouteContext,
    server_policy: ServerPermissionPolicy,
    policy_id: str,
    policy_digest: str,
    snapshot_id: str | None,
) -> _ComposeRouteResult:
    """Stable-route composition: Plan/1 ``compose()`` only (never ``expand()``).

    Invalid / exceptional plans abstain — never a partial or truncated order.
    """
    limits = _compose_limits(cfg)

    try:
        decisions, trust_policy = _trust_decisions_by_engram(cfg)
    except trust_mod.TrustLedgerCorruptError:
        return _ComposeRouteResult(
            ok=False,
            reason_codes=(REASON_COMPOSITION_INVALID, eligibility_mod.REASON_UNTRUSTED_ORIGIN),
        )

    nodes: dict[str, composition_mod.CompositionNode] = {}
    id_to_name: dict[str, str] = {winner_id: winner_name}
    frontier: list[tuple[str, int]] = [(winner_id, 0)]
    seen = {winner_id}

    while frontier:
        eid, depth = frontier.pop(0)
        row = _row_by_engram_id(conn, eid)
        if row is None:
            return _ComposeRouteResult(
                ok=False,
                reason_codes=(
                    REASON_COMPOSITION_INVALID,
                    eligibility_mod.REASON_DANGLING_DEPENDENCY,
                ),
            )
        id_to_name[eid] = str(row["name"])
        try:
            subject = _build_subject_from_row(cfg, row)
            subject, dangling = _subject_for_composition(subject, conn, eid)
        except _SubjectProjectionDenied as denied:
            return _ComposeRouteResult(
                ok=False,
                reason_codes=(
                    REASON_COMPOSITION_INVALID,
                    *denied.reason_codes[:_COMPOSITION_REASON_BOUND],
                ),
            )
        except Exception:
            return _ComposeRouteResult(
                ok=False,
                reason_codes=(REASON_COMPOSITION_ERROR, "composition_subject_error"),
            )
        if dangling:
            return _ComposeRouteResult(
                ok=False,
                reason_codes=(
                    REASON_COMPOSITION_INVALID,
                    eligibility_mod.REASON_DANGLING_DEPENDENCY,
                ),
            )
        nodes[eid] = composition_mod.CompositionNode(
            subject=subject,
            content_digest=str(row["content_sha256"] or ""),
            procedure_ref=str(row["path"]) if row["path"] else None,
        )

        # Seed transitive requires into the snapshot so compose can distinguish
        # dangling vs budget (C5). Compose itself enforces depth/node limits.
        requires = list(subject.relations.requires) if subject.relations is not None else []
        for ref in requires:
            if ref.id in seen:
                continue
            # Hard failsafe only — never looser than 2× S08 node default.
            if len(seen) >= composition_mod.DEFAULT_MAX_NODES * 2:
                continue
            seen.add(ref.id)
            frontier.append((ref.id, depth + 1))

    snap = composition_mod.CompositionSnapshot(
        snapshot_id=snapshot_id or "snapshot_unpinned",
        policy_id=policy_id,
        policy_digest=policy_digest,
        nodes=nodes,
    )

    def trust_view(engram_id: str) -> trust_mod.TrustDecisionView:
        row = _row_by_engram_id(conn, engram_id)
        if row is None:
            raise KeyError(engram_id)
        return _route_trust_view(
            cfg, row, cached_decision=decisions.get(engram_id), cached_policy=trust_policy
        )

    try:
        plan = composition_mod.compose(
            [winner_id],
            route_context,
            snap,
            limits,
            server_policy=server_policy,
            trust_view=trust_view,
        )
    except Exception:
        return _ComposeRouteResult(
            ok=False,
            reason_codes=(REASON_COMPOSITION_ERROR,),
        )

    if plan.status != "valid" or not plan.structurally_valid or not plan.executable:
        diag_codes = list(dict.fromkeys(d.code for d in plan.diagnostics if d.code))[
            :_COMPOSITION_REASON_BOUND
        ]
        missing: list[str] = []
        for d in plan.diagnostics:
            if d.code == eligibility_mod.REASON_CONTEXT_REQUIRED:
                missing.extend(d.related_ids)
        reasons = list(dict.fromkeys([REASON_COMPOSITION_INVALID, *diag_codes]))[
            : _COMPOSITION_REASON_BOUND + 1
        ]
        return _ComposeRouteResult(
            ok=False,
            reason_codes=tuple(reasons),
            missing_context=tuple(dict.fromkeys(missing)),
            plan_digest=_plan_identity_digest(plan),
            plan=plan,
        )

    order_names: list[str] = []
    for eid in plan.topological_order:
        name = id_to_name.get(eid)
        if name is None:
            row = _row_by_engram_id(conn, eid)
            if row is None:
                return _ComposeRouteResult(
                    ok=False,
                    reason_codes=(
                        REASON_COMPOSITION_INVALID,
                        eligibility_mod.REASON_DANGLING_DEPENDENCY,
                    ),
                    plan=plan,
                    plan_digest=_plan_identity_digest(plan),
                )
            name = str(row["name"])
            id_to_name[eid] = name
        order_names.append(name)

    # Downstream CompositionPlan confidence: valid Plan/1 ⇒ fully satisfied.
    adapted = composition_mod.CompositionPlan(order=list(order_names))
    confidence = composition_mod.plan_confidence(adapted)
    return _ComposeRouteResult(
        ok=True,
        order_names=tuple(order_names),
        plan_confidence=confidence,
        plan_digest=_plan_identity_digest(plan),
        plan=plan,
    )


#: In-process subject projection cache keyed by registry root + engram + file
#: identity (and asset file identities). Only digests matching the DB projection
#: are cached; drifted live bytes are denied and never cached as eligible.
@dataclass(frozen=True)
class _SubjectCacheEntry:
    subject: eligibility_mod.EligibilitySubject
    db_digest: str
    asset_idents: tuple[tuple[Any, ...], ...]


_SUBJECT_CACHE: OrderedDict[tuple[Any, ...], _SubjectCacheEntry] = OrderedDict()
_SUBJECT_CACHE_MAX = 4096


class _SubjectProjectionDenied(Exception):
    """Live artifact cannot be projected for route eligibility (fail closed)."""

    def __init__(self, *reason_codes: str) -> None:
        self.reason_codes = tuple(reason_codes) or (
            eligibility_mod.REASON_CONFLICT,
            eligibility_mod.REASON_ASSET_INVALID,
        )
        super().__init__(",".join(self.reason_codes))


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    # ctime is included because utime() can restore mtime but never ctime.
    st = path.stat()
    return (
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_size),
        int(st.st_mtime_ns),
        int(st.st_ctime_ns),
    )


def _asset_identities(assets: dict[str, Any], *, registry_root: Path) -> tuple[tuple[Any, ...], ...]:
    out: list[tuple[Any, ...]] = []
    for rel in sorted(assets):
        candidate = registry_root / rel
        try:
            out.append((rel, *_file_identity(candidate)))
        except OSError:
            out.append((rel, None))
    return tuple(out)


def _cache_get(key: tuple[Any, ...]) -> _SubjectCacheEntry | None:
    entry = _SUBJECT_CACHE.get(key)
    if entry is None:
        return None
    _SUBJECT_CACHE.move_to_end(key)
    return entry


def _cache_put(key: tuple[Any, ...], entry: _SubjectCacheEntry) -> None:
    _SUBJECT_CACHE[key] = entry
    _SUBJECT_CACHE.move_to_end(key)
    while len(_SUBJECT_CACHE) > _SUBJECT_CACHE_MAX:
        _SUBJECT_CACHE.popitem(last=False)


def _trust_decisions_by_engram(
    cfg: Config,
) -> tuple[dict[str, trust_mod.TrustDecision], trust_mod.TrustPolicy]:
    """One authenticated head binds policy and sequence-ordered decisions."""
    snapshot = trust_mod.authenticated_snapshot(cfg)
    return (
        {key: trust_mod.TrustDecision.from_dict(value) for key, value in snapshot.latest_by_engram.items()},
        trust_mod.TrustPolicy.from_dict(snapshot.policy),
    )


def _build_subject_from_row(
    cfg: Config,
    row: sqlite3.Row,
) -> eligibility_mod.EligibilitySubject:
    """Build a full EligibilitySubject from live bytes; drift/missing → deny.

    Cache key: (resolved registry root, engram_id, artifact file identity).
    Asset file identities are re-checked on hit. Live content digest must match
    the DB ``content_sha256`` projection (same digest register uses).
    """
    engram_id = str(row["id"])
    version = int(row["version"]) if "version" in row.keys() else 1
    db_digest = str(row["content_sha256"]) if "content_sha256" in row.keys() else ""
    rel = str(row["path"]) if "path" in row.keys() else ""
    full = Path(rel) if Path(rel).is_absolute() else (cfg.project_root / rel)
    registry_root = cfg.registry_dir.resolve()
    root_key = str(registry_root)

    if not full.is_file():
        raise _SubjectProjectionDenied(
            eligibility_mod.REASON_ASSET_INVALID,
            eligibility_mod.REASON_CONFLICT,
            "missing_artifact",
        )

    try:
        file_ident = _file_identity(full)
    except OSError as exc:
        raise _SubjectProjectionDenied(
            eligibility_mod.REASON_ASSET_INVALID,
            eligibility_mod.REASON_CONFLICT,
            "missing_artifact",
        ) from exc

    cache_key: tuple[Any, ...] = (root_key, engram_id, file_ident)
    cached = _cache_get(cache_key)
    if cached is not None and cached.db_digest == db_digest:
        # Re-stat asset files the subject's validity depended on.
        asset_paths = {str(item[0]): None for item in cached.asset_idents if item and item[0] is not None}
        if cached.asset_idents == _asset_identities(asset_paths, registry_root=registry_root):
            return cached.subject

    raw_bytes = full.read_bytes()
    live_digest = ids_mod.content_sha256(raw_bytes)
    if live_digest != db_digest:
        raise _SubjectProjectionDenied(
            eligibility_mod.REASON_ASSET_INVALID,
            eligibility_mod.REASON_CONFLICT,
            "registry_drift",
        )

    try:
        try:
            full.resolve().relative_to(registry_root)
            artifact, _doc = parser_mod.parse_artifact_file(
                full,
                registry_root=cfg.registry_dir,
                admit=False,
                require_asset_files=False,
            )
        except ValueError:
            artifact, _doc = parser_mod.parse_artifact(
                raw_bytes.decode("utf-8"),
                relpath=rel,
                file_mtime_ns=file_ident[3],
                admit=False,
                registry_root=cfg.registry_dir,
                require_asset_files=False,
            )
    except _SubjectProjectionDenied:
        raise
    except Exception as exc:
        raise _SubjectProjectionDenied(
            eligibility_mod.REASON_CONFLICT,
            eligibility_mod.REASON_ASSET_INVALID,
            "eligibility_parse_error",
        ) from exc

    asset_idents: tuple[tuple[Any, ...], ...] = ()
    if isinstance(artifact, EngramV1):
        assets = dict(artifact.frontmatter.assets or {})
        assets_valid = True
        if assets:
            issues = validate_assets(assets, registry_root=cfg.registry_dir, require_files=True)
            assets_valid = len(issues) == 0
            asset_idents = _asset_identities(assets, registry_root=registry_root)
        subject = eligibility_mod.subject_from_engram(artifact, assets_valid=assets_valid)
    elif isinstance(artifact, Engram):
        # Engram/0.2 has no C2 risk/compat/capabilities surface (injection_risk
        # is a distinct lint signal, not ServerPermissionPolicy risk).
        subject = eligibility_mod.EligibilitySubject(
            id=str(artifact.id or engram_id),
            version=int(getattr(artifact, "version", version) or version),
            assets_valid=True,
        )
    else:
        raise _SubjectProjectionDenied(
            eligibility_mod.REASON_CONFLICT,
            "unsupported_artifact_type",
        )

    _cache_put(
        cache_key,
        _SubjectCacheEntry(subject=subject, db_digest=db_digest, asset_idents=asset_idents),
    )
    return subject


def _route_trust_view(
    cfg: Config,
    row: sqlite3.Row,
    *,
    cached_decision: trust_mod.TrustDecision | None,
    cached_policy: trust_mod.TrustPolicy,
) -> trust_mod.TrustDecisionView:
    """Build TrustDecisionView for route eligibility without per-row file I/O.

    Uses the batched ledger decision + durable row fields. Default local
    authorship admission is the named Config knob (N1).
    """
    engram_id = str(row["id"])
    content_digest = str(row["content_sha256"]) if "content_sha256" in row.keys() else ""
    verification = str(row["verification_status"]) if "verification_status" in row.keys() else ""
    lifecycle = str(row["status"]) if "status" in row.keys() else ""
    origin = str(row["origin"]) if "origin" in row.keys() else ""

    decision = cached_decision
    quarantined = verification == "quarantined" or (
        decision is not None and decision.decision == "quarantine"
    )

    admitted = False
    if decision is not None and decision.decision == "admit":
        if decision.content_digest == content_digest:
            try:
                policy = cached_policy
                admitted = (
                    decision.policy_digest == policy.digest() and decision.policy_revision == policy.revision
                )
            except InvalidInputError:
                admitted = False

    if origin in ("authored", "sharpened") or (
        decision is not None and decision.source_channel in ("local_authored", "local_register")
    ):
        channel_trusted = True
        intake: trust_mod.SourceChannel = "local_register"
    elif admitted:
        channel_trusted = True
        intake = decision.source_channel if decision is not None else "unknown"
    else:
        channel_trusted = False
        intake = decision.source_channel if decision is not None else "unknown"

    origin_trusted = trust_mod.origin_trusted_for_channel(intake, admitted=admitted) or channel_trusted

    sig: bool | None = decision.signature_valid if decision is not None else None
    return trust_mod.TrustDecisionView(
        engram_id=engram_id,
        content_digest=content_digest,
        quarantined=quarantined,
        lifecycle_status=lifecycle,
        origin_trusted=origin_trusted,
        signature_valid=sig,
        admitted=admitted,
    )


def _evaluate_route_eligibility(
    cfg: Config,
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    route_context: RouteContext,
    server_policy: ServerPermissionPolicy,
) -> tuple[list[sqlite3.Row], list[ExclusionSummary], list[str]]:
    """Run S06 eligibility before scoring/rerank for EVERY candidate (C2).

    Always builds a full subject from live bytes and always calls
    ``evaluate_eligibility``. Missing/drifted artifacts are denied. Subject
    projections are cached by (registry root, engram_id, file identity).
    Trust decisions are batched once per route.
    """
    eligible_rows: list[sqlite3.Row] = []
    exclusions: list[ExclusionSummary] = []
    missing_context: list[str] = []
    try:
        decisions, trust_policy = _trust_decisions_by_engram(cfg)
        ledger_corrupt = False
    except trust_mod.TrustLedgerCorruptError:
        decisions = {}
        ledger_corrupt = True

    for row in rows:
        engram_id = str(row["id"])
        if ledger_corrupt:
            exclusions.append(
                ExclusionSummary(
                    engram_id=engram_id,
                    reason_codes=(eligibility_mod.REASON_UNTRUSTED_ORIGIN,),
                )
            )
            continue
        try:
            subject = _build_subject_from_row(cfg, row)
            trust = _route_trust_view(
                cfg, row, cached_decision=decisions.get(engram_id), cached_policy=trust_policy
            )
            result = eligibility_mod.evaluate_eligibility(
                subject, route_context, trust, server_policy, path="route"
            )
        except _SubjectProjectionDenied as denied:
            exclusions.append(ExclusionSummary(engram_id=engram_id, reason_codes=denied.reason_codes))
            continue
        except Exception:
            exclusions.append(
                ExclusionSummary(
                    engram_id=engram_id,
                    reason_codes=(
                        eligibility_mod.REASON_CONFLICT,
                        eligibility_mod.REASON_ASSET_INVALID,
                        "eligibility_evaluator_error",
                    ),
                )
            )
            continue
        if not result.eligible:
            exclusions.append(ExclusionSummary(engram_id=engram_id, reason_codes=tuple(result.reason_codes)))
            if result.context_required:
                missing_context.extend(result.missing_fields)
            continue
        eligible_rows.append(row)
    return eligible_rows, exclusions, missing_context


def _bound_exclusions(exclusions: Sequence[ExclusionSummary], *, limit: int) -> tuple[ExclusionSummary, ...]:
    if limit < 1:
        return ()
    ordered = sorted(exclusions, key=lambda e: e.engram_id)
    return tuple(ordered[:limit])


def _apply_optional_reranker(
    cfg: Config,
    *,
    query: str,
    ranked_ids: list[str],
    scores_by_id: dict[str, float],
) -> tuple[list[str], str | None, str | None]:
    """Optional reranker with timeout / missing-model fallback (AC-S07-04).

    Returns ``(ordered_ids, fallback_identity|None, operational_error|None)``.
    Selection-quality abstention is separate — this only handles ops failures.
    """
    provider = (cfg.reranker_provider or "").strip()
    if not provider:
        return ranked_ids, None, None

    fallback = (cfg.reranker_fallback or "").strip() or None

    try:
        reranker = reranker_mod.get_reranker(provider)
    except ValueError:
        if cfg.reranker_required:
            if fallback:
                return ranked_ids, fallback, None
            return ranked_ids, None, "reranker_model_missing"
        return ranked_ids, None, None

    # Lightweight slate objects satisfying the reranker protocol.
    slate = [
        type("RerankCand", (), {"id": nid, "fused_score": scores_by_id.get(nid, 0.0)})()
        for nid in ranked_ids[: reranker_mod.DEFAULT_RERANK_LIMIT]
    ]

    def _call() -> Sequence[Any]:
        return reranker.rerank(
            query,
            slate,
            token_budget=2048,
            timeout_s=float(cfg.reranker_timeout_s),
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_call)
            reranked = future.result(timeout=float(cfg.reranker_timeout_s))
    except FuturesTimeout:
        if fallback:
            return ranked_ids, fallback, None
        if cfg.reranker_required:
            return ranked_ids, None, "reranker_timeout"
        return ranked_ids, fallback, None
    except Exception:
        if fallback:
            return ranked_ids, fallback, None
        if cfg.reranker_required:
            return ranked_ids, None, "reranker_failure"
        return ranked_ids, None, None

    new_order: list[str] = []
    for c in reranked:
        nid = getattr(c, "id", None)
        if isinstance(nid, str) and nid in scores_by_id:
            new_order.append(nid)
    # Preserve any ids the reranker dropped, in prior order.
    seen = set(new_order)
    for nid in ranked_ids:
        if nid not in seen:
            new_order.append(nid)
    return new_order, None, None


def semantic_decision_fields(decision: RouteDecision) -> dict[str, Any]:
    """Equality-relevant RouteDecision fields (excludes opaque ids/timings)."""
    return {
        "status": decision.status,
        "selected_ids": list(decision.selected_ids),
        "candidate_ids": [c.id for c in decision.candidates],
        "candidate_scores": [c.score for c in decision.candidates],
        "exclusions": [
            {"engram_id": e.engram_id, "reason_codes": list(e.reason_codes)} for e in decision.exclusions
        ],
        "score_components": decision.score_components,
        "confidence": {
            "value": decision.confidence.value,
            "calibration_id": decision.confidence.calibration_id,
        },
        "reason_codes": list(decision.reason_codes),
        "missing_context": list(decision.missing_context),
        "truncations": dict(decision.truncations),
        "policy_id": decision.policy_id,
        "policy_digest": decision.policy_digest,
        "policy_family": decision.policy_family,
        "config_digest": decision.config_digest,
        "calibration_digest": decision.calibration_digest,
        "query_fingerprint": decision.query_fingerprint,
        "registry_digest": decision.registry_digest,
        "schema_digest": decision.schema_digest,
        "model_digest": decision.model_digest,
        "tokenizer_digest": decision.tokenizer_digest,
        "index_generation_id": decision.index_generation_id,
        "snapshot_id": decision.snapshot_id,
        "selected_content_digests": dict(decision.selected_content_digests),
        "selection_mechanism": decision.selection_mechanism,
        "propensity": dict(decision.propensity),
        "explanation_version": decision.explanation_version,
        "fallback_identity": decision.fallback_identity,
        "operational_error": decision.operational_error,
        "default_local_authorship_policy": decision.default_local_authorship_policy,
        "policy_source": decision.policy_source,
        "plan_digest": decision.plan_digest,
        "schema_version": decision.schema_version,
    }


def _registry_digest(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id, content_sha256 FROM engram ORDER BY id").fetchall()
    payload = [{"id": r["id"], "content_sha256": r["content_sha256"]} for r in rows]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


@lru_cache(maxsize=16)
def _cached_route_index(
    node_ids: tuple[str, ...],
    activation_edges: tuple[tuple[str, str, float], ...],
    structural_edges: tuple[tuple[str, str, float], ...],
) -> tuple[activation_mod.SparseGraph, np.ndarray]:
    """Reuse immutable graph normalization for an exact registry view."""
    ids = list(node_ids)
    graph = activation_mod.build_graph(ids, list(activation_edges))
    hub_graph = activation_mod.build_graph(ids, list(structural_edges))
    return graph, activation_mod.page_rank(hub_graph)


def _contraindication_contributions(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query_vector: np.ndarray,
    node_ids: list[str],
    row_by_id: dict[str, sqlite3.Row],
    *,
    weight: float,
) -> np.ndarray:
    """Independent, monotonic negative-cue contribution for each node."""
    if weight <= 0 or not node_ids:
        return np.zeros(len(node_ids), dtype=np.float64)
    out = np.zeros(len(node_ids), dtype=np.float64)
    trigger_rows = conn.execute(
        "SELECT engram_id, ord, text FROM engram_trigger WHERE polarity = 'negative' ORDER BY engram_id, ord"
    ).fetchall()
    triggers: dict[str, list[str]] = {}
    for row in trigger_rows:
        triggers.setdefault(str(row["engram_id"]), []).append(str(row["text"]))

    texts: list[str] = []
    active_indices: list[int] = []
    for i, node_id in enumerate(node_ids):
        cached = row_by_id[node_id]["contraindication_vec"]
        if cached is not None:
            similarity = float(np.dot(query_vector, np.frombuffer(cached, dtype=np.float32)))
            out[i] = -weight * max(0.0, similarity)
            continue
        not_when = row_by_id[node_id]["intent_not_when"]
        negative = triggers.get(node_id, [])
        if not_when is None and not negative:
            continue
        texts.append(
            json.dumps(
                {
                    "schema": CONTRAINDICATION_VIEW_SCHEMA,
                    "fields": ["intent.not_when", "triggers.negative"],
                    "intent.not_when": not_when,
                    "triggers.negative": negative,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        active_indices.append(i)

    # Missing vectors occur for upgraded v0.2 databases that have not yet
    # synced. Compute and persist the Tier-C cache lazily once.
    if not texts:
        return out
    vectors = embedder.embed_batch(texts)
    similarities = vectors @ query_vector
    for index, vector, similarity in zip(active_indices, vectors, similarities, strict=True):
        out[index] = -weight * max(0.0, float(similarity))
        node_id = node_ids[index]
        ephemeral_mod.upsert_embedding(
            conn,
            engram_id=node_id,
            model_name=contraindication_model_name(embedder.model_name),
            dim=embedder.dim,
            vec=vector,
            source_sha256=str(row_by_id[node_id]["identity_sha256"]),
        )
    return out


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


def _resolve_active_policy(
    cfg: Config,
) -> tuple[str, str, str, str | None, Literal["store", "config_fresh_install"] | None, tuple[str, ...]]:
    """Resolve policy id without mutating cfg (orchestrator decision).

    Returns ``(policy_id, policy_digest, policy_family, operational_error,
    policy_source, extra_reason_codes)``.

    - Store present & valid with active → store governs; cfg.routing_policy
      ignored for selection (disagreement → ``config_policy_ignored_store_active``).
    - Store present & corrupt/unknown → typed operational error.
    - Store ``state.json`` missing but prior governance evidence exists →
      ``policy_store_missing`` (fail closed).
    - No store and no prior evidence → S00 cfg resolution
      (``policy_source=config_fresh_install``).
    """
    store_path = policy_store_mod.policy_store_path(cfg)
    cfg_policy = policy_mod.resolve_policy_id(cfg)

    if not store_path.is_file():
        if policy_store_mod.prior_policy_governance_evidence(cfg):
            return (
                policy_mod.POLICY_DENSE_V1,
                "",
                "stable",
                "policy_store_missing",
                None,
                ("policy_store_missing",),
            )
        digest = policy_mod.compute_policy_digest(cfg_policy, cfg)
        return (
            cfg_policy,
            digest,
            policy_mod.policy_family(cfg_policy),
            None,
            "config_fresh_install",
            (),
        )

    try:
        manifest = policy_store_mod.get_active_manifest(cfg)
    except InvalidInputError:
        return (
            policy_mod.POLICY_DENSE_V1,
            "",
            "stable",
            "policy_store_corrupt",
            None,
            ("policy_store_corrupt",),
        )

    if manifest is None:
        # Valid store file with no active pointer — fresh-install cfg selection.
        digest = policy_mod.compute_policy_digest(cfg_policy, cfg)
        return (
            cfg_policy,
            digest,
            policy_mod.policy_family(cfg_policy),
            None,
            "config_fresh_install",
            (),
        )

    if manifest.policy_id not in policy_mod.KNOWN_POLICY_IDS:
        return (
            manifest.policy_id,
            manifest.policy_digest,
            manifest.policy_family,
            "unknown_active_policy",
            None,
            ("unknown_active_policy",),
        )

    digest = (
        policy_mod.bind_server_ceiling_digest(manifest.policy_digest, cfg)
        if manifest.policy_digest
        else policy_mod.compute_policy_digest(manifest.policy_id, cfg)
    )
    extra: tuple[str, ...] = ()
    if cfg_policy != manifest.policy_id:
        extra = ("config_policy_ignored_store_active",)
    return manifest.policy_id, digest, manifest.policy_family, None, "store", extra


def _pin_index_identity(
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None, str | None, str | None, tuple[str, ...]]:
    """Pin active index generation into RouteDecision identity fields (B2).

    Returns ``(generation_id, snapshot_id, schema_digest, tokenizer_digest, reason_codes)``.
    When no published generation exists, fields are explicit null with reason codes.
    """
    catalog = index_gen_mod.IndexCatalog(conn)
    gid = catalog.active_generation_id()
    if gid is None:
        return (
            None,
            None,
            None,
            None,
            (
                "index_generation_absent",
                "snapshot_unpinned",
                "schema_digest_unavailable",
                "tokenizer_digest_unavailable",
            ),
        )
    try:
        meta = catalog.pin(gid)
    except index_gen_mod.IndexGenerationError:
        return (
            None,
            None,
            None,
            None,
            (
                "index_generation_unpinnable",
                "snapshot_unpinned",
                "schema_digest_unavailable",
                "tokenizer_digest_unavailable",
            ),
        )
    schema_digest = hashlib.sha256(meta.fingerprint.index_schema_version.encode("utf-8")).hexdigest()
    tokenizer_digest = hashlib.sha256(meta.fingerprint.tokenizer_id.encode("utf-8")).hexdigest()
    return (
        meta.generation_id,
        meta.snapshot_id or None,
        schema_digest,
        tokenizer_digest,
        (),
    )


def pin_index_identity(
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None, str | None, str | None, tuple[str, ...]]:
    """Public alias of :func:`_pin_index_identity` for S11 body-load snapshot gates.

    Same return shape: ``(generation_id, snapshot_id, schema_digest, tokenizer_digest, reason_codes)``.
    """
    return _pin_index_identity(conn)


def route(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    query: str,
    context: dict | None = None,
    k: int = 5,
    session_id: str | None = None,
    route_context: RouteContext | None = None,
    server_policy: ServerPermissionPolicy | None = None,
) -> RouteOutcome:
    # S12 C8/C9: refuse routing while restore reconciliation is required.
    # Cheap no-op when no restore-generation markers exist.
    recovery_gate_mod.assert_routing_allowed(cfg)

    # core/session.py (M3): the one session-resolution rule every
    # session-participating tool follows (spec §3.3) -- mint/reuse/expire,
    # in one place, instead of route() rolling its own uuid4() + upsert.
    sid = session_mod.resolve_id(cfg, conn, session_id)
    (
        policy_id,
        digest,
        family,
        policy_ops_error,
        policy_source,
        policy_extra_reasons,
    ) = _resolve_active_policy(cfg)
    config_digest = policy_mod.compute_config_digest(cfg)

    if policy_ops_error:
        key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
        query_fp = fingerprint_key_mod.query_fingerprint(query, key=key)
        gen_id, snap_id, schema_d, tok_d, pin_reasons = _pin_index_identity(conn)
        decision = RouteDecision(
            decision_id=f"rd_{uuid.uuid4().hex[:16]}",
            status="error",
            selected_ids=(),
            candidates=[],
            exclusions=(),
            score_components={},
            confidence=Confidence(value=None, calibration_id=None),
            reason_codes=tuple(
                dict.fromkeys((policy_ops_error, "operational_error", *policy_extra_reasons, *pin_reasons))
            ),
            missing_context=(),
            truncations={},
            policy_id=policy_id,
            policy_digest=digest,
            policy_family=family,
            config_digest=config_digest,
            calibration_digest=None,
            query_fingerprint=query_fp,
            registry_digest=_registry_digest(conn),
            schema_digest=schema_d,
            model_digest=embedder.model_name,
            tokenizer_digest=tok_d,
            index_generation_id=gen_id,
            snapshot_id=snap_id,
            selection_mechanism=policy_id,
            operational_error=policy_ops_error,
            default_local_authorship_policy=bool(cfg.default_local_authorship_admission),
            policy_source=policy_source,
        )
        return RouteOutcome(
            candidates=[],
            composition_plan=[],
            plan_confidence=0.0,
            instructions=ROUTE_INSTRUCTIONS,
            session_id=sid,
            registry_size=conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"],
            unresolved_context=[],
            policy_id=policy_id,
            policy_digest=digest,
            policy_family=family,
            decision=decision,
        )

    # Local policy id only — never mutate the caller's Config.
    cal = calibration_mod.load_calibration(
        cfg,
        expected_config_digest=config_digest,
    )
    cal_digest = cal.digest if cal is not None else None
    if cal is not None and cal.policy_id != policy_id:
        calibration_mod.clear_calibration(cfg)
        cal = None
        cal_digest = None
    # A governed manifest is the policy authority; calibration validation must
    # not replace its effective identity with the fresh-install config identity.
    if policy_source != "store":
        digest = policy_mod.compute_policy_digest(policy_id, cfg, calibration_digest=cal_digest)
    if cal is not None and cal.policy_digest != digest:
        calibration_mod.clear_calibration(cfg)
        cal = None
        cal_digest = None
        if policy_source != "store":
            digest = policy_mod.compute_policy_digest(policy_id, cfg, calibration_digest=None)
    family = policy_mod.policy_family(policy_id)

    gen_id, snap_id, schema_d, tok_d, pin_reasons = _pin_index_identity(conn)

    qvec = embedder.embed(query)
    rows = _fetch_candidates(conn, embedder.model_name)
    registry_size = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]
    rctx = route_context if route_context is not None else RouteContext()
    spolicy = server_policy if server_policy is not None else DEFAULT_SERVER_POLICY

    if not rows:
        return _finalize_route(
            cfg,
            conn,
            query=query,
            k=k,
            candidates=[],
            unresolved_context=_echo_all_context(context),
            session_id=sid,
            registry_size=registry_size,
            policy_id=policy_id,
            policy_digest=digest,
            policy_family=family,
            config_digest=config_digest,
            calibration=cal,
            exclusions=(),
            missing_context=[],
            reason_codes=("no_candidates", *policy_extra_reasons, *pin_reasons),
            status="abstained",
            selection_mechanism=policy_id,
            score_components={},
            truncations={},
            model_digest=embedder.model_name,
            index_generation_id=gen_id,
            snapshot_id=snap_id,
            schema_digest=schema_d,
            tokenizer_digest=tok_d,
            policy_source=policy_source,
            route_context=rctx,
            server_policy=spolicy,
        )

    if policy_id == policy_mod.POLICY_DENSE_V1:
        outcome = _route_dense_v1(
            cfg,
            conn,
            embedder,
            query=query,
            qvec=qvec,
            rows=rows,
            k=k,
            session_id=sid,
            registry_size=registry_size,
            policy_id=policy_id,
            policy_digest=digest,
            policy_family=family,
            config_digest=config_digest,
            calibration=cal,
            route_context=rctx,
            server_policy=spolicy,
            index_generation_id=gen_id,
            snapshot_id=snap_id,
            schema_digest=schema_d,
            tokenizer_digest=tok_d,
            pin_reasons=(*policy_extra_reasons, *pin_reasons),
            policy_source=policy_source,
        )
    else:
        outcome = _route_adaptive_blend_v1(
            cfg,
            conn,
            embedder,
            query=query,
            context=context,
            qvec=qvec,
            rows=rows,
            k=k,
            session_id=sid,
            registry_size=registry_size,
            policy_id=policy_id,
            policy_digest=digest,
            policy_family=family,
            config_digest=config_digest,
            calibration=cal,
            route_context=rctx,
            server_policy=spolicy,
            index_generation_id=gen_id,
            snapshot_id=snap_id,
            schema_digest=schema_d,
            tokenizer_digest=tok_d,
            pin_reasons=(*policy_extra_reasons, *pin_reasons),
            policy_source=policy_source,
        )
    return outcome


def _route_event_payload(
    cfg: Config,
    *,
    query: str,
    k: int,
    candidates: list[Candidate],
    policy_id: str,
    policy_digest: str,
) -> dict:
    """Tier-C route receipt without raw query text (AC-S00-04 / C6).

    Persists a local keyed HMAC fingerprint (``hmac-sha256/local-v1``), never
    an unsalted hash of the query and never the raw query string.
    """
    key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return {
        "k": k,
        "candidate_ids": [c.id for c in candidates],
        "policy_id": policy_id,
        "policy_digest": policy_digest,
        "query_fingerprint": fingerprint_key_mod.query_fingerprint(query, key=key),
        "fingerprint_scheme": fingerprint_key_mod.FINGERPRINT_SCHEME,
    }


def _finalize_route(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    query: str,
    k: int,
    candidates: list[Candidate],
    unresolved_context: list[str],
    session_id: str,
    registry_size: int,
    policy_id: str,
    policy_digest: str,
    policy_family: str,
    config_digest: str = "",
    calibration: calibration_mod.CalibrationArtifact | None = None,
    exclusions: tuple[ExclusionSummary, ...] = (),
    missing_context: list[str] | None = None,
    reason_codes: tuple[str, ...] = (),
    status: Literal["selected", "abstained", "error"] | None = None,
    selection_mechanism: str = "dense-v1",
    score_components: dict[str, dict[str, float]] | None = None,
    truncations: dict[str, int] | None = None,
    model_digest: str | None = None,
    fallback_identity: str | None = None,
    operational_error: str | None = None,
    index_generation_id: str | None = None,
    snapshot_id: str | None = None,
    schema_digest: str | None = None,
    tokenizer_digest: str | None = None,
    policy_source: Literal["store", "config_fresh_install"] | None = None,
    route_context: RouteContext | None = None,
    server_policy: ServerPermissionPolicy | None = None,
) -> RouteOutcome:
    key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    query_fp = fingerprint_key_mod.query_fingerprint(query, key=key)
    cfg_digest = config_digest or policy_mod.compute_config_digest(cfg)
    missing = list(missing_context or [])
    missing.extend(unresolved_context)
    rctx = route_context if route_context is not None else RouteContext()
    spolicy = server_policy if server_policy is not None else DEFAULT_SERVER_POLICY

    composition_plan: list[str] = []
    plan_confidence = 0.0
    plan_digest: str | None = None
    composed_plan: composition_mod.Plan | None = None
    final_candidates = list(candidates)
    final_status: Literal["selected", "abstained", "error"]
    final_reasons = list(reason_codes)
    confidence = Confidence(value=None, calibration_id=None)
    cal_digest = calibration.digest if calibration is not None else None

    if operational_error and not fallback_identity:
        final_status = "error"
        final_candidates = []
        final_reasons = list(dict.fromkeys([*final_reasons, operational_error, "operational_error"]))
    else:
        # Abstention: calibrated artifact OR uncalibrated Config thresholds (N2).
        if cfg.abstention_enabled and final_candidates and not operational_error:
            top_score = final_candidates[0].score
            margin = calibration_mod.score_margin([c.score for c in final_candidates])
            abstention = calibration_mod.decide_abstention(
                query_fingerprint=query_fp,
                top_score=top_score,
                margin=margin,
                artifact=calibration,
                expected_policy_digest=policy_digest,
                expected_config_digest=cfg_digest,
                fallback_score_threshold=cfg.abstention_score_threshold,
                fallback_margin_threshold=cfg.abstention_margin_threshold,
                abstention_enabled=True,
            )
            if not abstention.calibrated:
                confidence = Confidence(value=None, calibration_id=None)
                cal_digest = None
                final_reasons.extend(abstention.reason_codes)
                if abstention.abstain:
                    final_candidates = []
            elif abstention.abstain:
                final_candidates = []
                final_reasons.extend(abstention.reason_codes)
            else:
                confidence = Confidence(
                    value=abstention.confidence_value,
                    calibration_id=abstention.calibration_id,
                )
                cal_digest = abstention.calibration_digest

        if status is not None and not final_candidates and status == "abstained":
            final_status = "abstained"
        elif not final_candidates:
            final_status = "error" if operational_error and not fallback_identity else "abstained"
            if "no_eligible_candidate" not in final_reasons and not operational_error:
                final_reasons.append("no_eligible_candidate")
        else:
            winner = final_candidates[0]
            composed = compose_route_plan(
                cfg,
                conn,
                winner_id=winner.id,
                winner_name=winner.name,
                route_context=rctx,
                server_policy=spolicy,
                policy_id=policy_id,
                policy_digest=policy_digest,
                snapshot_id=snapshot_id,
            )
            if not composed.ok:
                final_status = "abstained"
                final_candidates = []
                composition_plan = []
                plan_confidence = 0.0
                plan_digest = composed.plan_digest
                composed_plan = composed.plan
                final_reasons.extend(composed.reason_codes)
                missing.extend(composed.missing_context)
            else:
                final_status = "selected"
                composition_plan = list(composed.order_names)
                plan_confidence = composed.plan_confidence
                plan_digest = composed.plan_digest
                composed_plan = composed.plan

    # Deterministic propensity under a nonadaptive policy.
    propensity: dict[str, float] = {}
    if final_status == "selected" and final_candidates:
        for c in final_candidates:
            propensity[c.id] = 1.0 if c.id == final_candidates[0].id else 0.0
    elif final_status == "abstained":
        propensity["__abstain__"] = 1.0

    selected_ids = tuple(c.id for c in final_candidates[:1]) if final_status == "selected" else ()
    selected_digests = {c.id: (c.content_digest or "") for c in final_candidates if c.content_digest}

    route_decision = RouteDecision(
        decision_id=f"rd_{uuid.uuid4().hex[:16]}",
        status=final_status,
        selected_ids=selected_ids,
        candidates=final_candidates,
        exclusions=exclusions,
        score_components=score_components or {},
        confidence=confidence,
        reason_codes=tuple(dict.fromkeys(final_reasons)),
        missing_context=tuple(dict.fromkeys(missing)),
        truncations=truncations or {},
        policy_id=policy_id,
        policy_digest=policy_digest,
        policy_family=policy_family,
        config_digest=cfg_digest,
        calibration_digest=cal_digest,
        query_fingerprint=query_fp,
        registry_digest=_registry_digest(conn),
        model_digest=model_digest,
        schema_digest=schema_digest,
        tokenizer_digest=tokenizer_digest,
        index_generation_id=index_generation_id,
        snapshot_id=snapshot_id,
        selected_content_digests=selected_digests,
        selection_mechanism=fallback_identity or selection_mechanism,
        propensity=propensity,
        fallback_identity=fallback_identity,
        operational_error=operational_error,
        default_local_authorship_policy=bool(cfg.default_local_authorship_admission),
        policy_source=policy_source,
        plan_digest=plan_digest,
    )

    # step 11: Tier-C bookkeeping ONLY -- R and S are never touched here (Principle 1).
    # Hot path stays free of checkpoint/fsync work (S09 receipt hook point is forward).
    for c in final_candidates:
        ephemeral_mod.bump_route_bookkeeping(conn, c.id)
    ephemeral_mod.append_event(
        conn,
        session_id=session_id,
        tool="route",
        signal_tier=0,
        engram_id=final_candidates[0].id if final_candidates else None,
        payload=_route_event_payload(
            cfg,
            query=query,
            k=k,
            candidates=final_candidates,
            policy_id=policy_id,
            policy_digest=policy_digest,
        ),
    )

    return RouteOutcome(
        candidates=final_candidates,
        composition_plan=composition_plan,
        plan_confidence=plan_confidence,
        instructions=ROUTE_INSTRUCTIONS,
        session_id=session_id,
        registry_size=registry_size,
        unresolved_context=unresolved_context,
        policy_id=policy_id,
        policy_digest=policy_digest,
        policy_family=policy_family,
        decision=route_decision,
        plan=composed_plan,
    )


def _route_dense_v1(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    query: str,
    qvec: np.ndarray,
    rows: list[sqlite3.Row],
    k: int,
    session_id: str,
    registry_size: int,
    policy_id: str,
    policy_digest: str,
    policy_family: str,
    config_digest: str,
    calibration: calibration_mod.CalibrationArtifact | None,
    route_context: RouteContext,
    server_policy: ServerPermissionPolicy,
    index_generation_id: str | None = None,
    snapshot_id: str | None = None,
    schema_digest: str | None = None,
    tokenizer_digest: str | None = None,
    pin_reasons: tuple[str, ...] = (),
    policy_source: Literal["store", "config_fresh_install"] | None = None,
) -> RouteOutcome:
    """Nonadaptive incumbent: eligibility → cosine → optional rerank → abstain."""
    eligible_rows, exclusions, missing_ctx = _evaluate_route_eligibility(
        cfg,
        conn,
        rows,
        route_context=route_context,
        server_policy=server_policy,
    )
    bound_exclusions = _bound_exclusions(exclusions, limit=cfg.max_exclusion_summaries)

    if not eligible_rows:
        return _finalize_route(
            cfg,
            conn,
            query=query,
            k=k,
            candidates=[],
            unresolved_context=[],
            session_id=session_id,
            registry_size=registry_size,
            policy_id=policy_id,
            policy_digest=policy_digest,
            policy_family=policy_family,
            config_digest=config_digest,
            calibration=calibration,
            exclusions=bound_exclusions,
            missing_context=missing_ctx,
            reason_codes=("no_eligible_candidate", *pin_reasons),
            status="abstained",
            selection_mechanism=policy_id,
            model_digest=embedder.model_name,
            index_generation_id=index_generation_id,
            snapshot_id=snapshot_id,
            schema_digest=schema_digest,
            tokenizer_digest=tokenizer_digest,
            policy_source=policy_source,
            route_context=route_context,
            server_policy=server_policy,
        )

    node_ids: list[str] = []
    row_by_id: dict[str, sqlite3.Row] = {}
    cosine_list: list[float] = []
    for row in eligible_rows:
        vec = np.frombuffer(row["vec"], dtype=np.float32)
        node_ids.append(row["id"])
        row_by_id[row["id"]] = row
        cosine_list.append(float(np.dot(qvec, vec)))
    cosine = np.array(cosine_list, dtype=np.float64)
    scores_by_id = {nid: float(cosine[i]) for i, nid in enumerate(node_ids)}
    ranked = sorted(node_ids, key=lambda nid: (-scores_by_id[nid], nid))

    # Bounded refill: eligibility already filtered the pool; take up to
    # k + refill_limit then truncate so drops never silently shrink below k
    # when more eligible rows remain.
    refill_n = max(k, min(len(ranked), k + int(cfg.candidate_refill_limit)))
    pool = ranked[:refill_n]

    ordered, fallback_identity, operational_error = _apply_optional_reranker(
        cfg, query=query, ranked_ids=pool, scores_by_id=scores_by_id
    )
    top_ids = ordered[:k]

    score_components: dict[str, dict[str, float]] = {}
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
            content_digest=str(row_by_id[nid]["content_sha256"])
            if "content_sha256" in row_by_id[nid].keys()
            else None,
            diagnostics={
                "similarity": round(float(scores_by_id[nid]), 6),
                "final": round(float(scores_by_id[nid]), 6),
            },
        )
        for i, nid in enumerate(top_ids)
    ]
    for c in candidates:
        score_components[c.id] = dict(c.diagnostics)

    truncations: dict[str, int] = {}
    if len(ranked) > refill_n:
        truncations["eligible_pool"] = len(ranked) - refill_n

    return _finalize_route(
        cfg,
        conn,
        query=query,
        k=k,
        candidates=candidates,
        unresolved_context=[],
        session_id=session_id,
        registry_size=registry_size,
        policy_id=policy_id,
        policy_digest=policy_digest,
        policy_family=policy_family,
        config_digest=config_digest,
        calibration=calibration,
        exclusions=bound_exclusions,
        missing_context=missing_ctx,
        reason_codes=pin_reasons,
        selection_mechanism=policy_id,
        score_components=score_components,
        truncations=truncations,
        model_digest=embedder.model_name,
        fallback_identity=fallback_identity,
        operational_error=operational_error,
        index_generation_id=index_generation_id,
        snapshot_id=snapshot_id,
        schema_digest=schema_digest,
        tokenizer_digest=tokenizer_digest,
        policy_source=policy_source,
        route_context=route_context,
        server_policy=server_policy,
    )


def _route_adaptive_blend_v1(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    query: str,
    context: dict | None,
    qvec: np.ndarray,
    rows: list[sqlite3.Row],
    k: int,
    session_id: str,
    registry_size: int,
    policy_id: str,
    policy_digest: str,
    policy_family: str,
    config_digest: str,
    calibration: calibration_mod.CalibrationArtifact | None,
    route_context: RouteContext,
    server_policy: ServerPermissionPolicy,
    index_generation_id: str | None = None,
    snapshot_id: str | None = None,
    schema_digest: str | None = None,
    tokenizer_digest: str | None = None,
    pin_reasons: tuple[str, ...] = (),
    policy_source: Literal["store", "config_fresh_install"] | None = None,
) -> RouteOutcome:
    """Legacy adaptive blend — explicit experimental policy only."""
    eligible_rows, exclusions, missing_ctx = _evaluate_route_eligibility(
        cfg,
        conn,
        rows,
        route_context=route_context,
        server_policy=server_policy,
    )
    bound_exclusions = _bound_exclusions(exclusions, limit=cfg.max_exclusion_summaries)
    if not eligible_rows:
        return _finalize_route(
            cfg,
            conn,
            query=query,
            k=k,
            candidates=[],
            unresolved_context=_echo_all_context(context),
            session_id=session_id,
            registry_size=registry_size,
            policy_id=policy_id,
            policy_digest=policy_digest,
            policy_family=policy_family,
            config_digest=config_digest,
            calibration=calibration,
            exclusions=bound_exclusions,
            missing_context=missing_ctx,
            reason_codes=("no_eligible_candidate", *pin_reasons),
            status="abstained",
            selection_mechanism=policy_id,
            model_digest=embedder.model_name,
            index_generation_id=index_generation_id,
            snapshot_id=snapshot_id,
            schema_digest=schema_digest,
            tokenizer_digest=tokenizer_digest,
            policy_source=policy_source,
            route_context=route_context,
            server_policy=server_policy,
        )

    now = datetime.now(UTC).isoformat()
    rows = eligible_rows

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

    seed_cos = activation_mod.select_seed_cosines(node_ids, cosine, k=k)

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
    inhibition_edges = _fetch_inhibition_edges(conn, declared_edge_strength=cfg.declared_edge_strength)

    structural_edges = [
        (r["src_id"], r["dst_id"], cfg.type_gain.get(r["type"], 0.0))
        for r in edge_rows
        if cfg.type_gain.get(r["type"], 0.0) > 0
    ]
    graph, usage_pagerank = _cached_route_index(
        tuple(node_ids), tuple(activation_edges), tuple(structural_edges)
    )
    a = activation_mod.activate_graph(
        node_ids,
        seed_cos,
        graph,
        inhibition_edges,
        temperature=cfg.temperature,
        restart=cfg.ppr_restart,
        max_iter=cfg.ppr_max_iter,
        tol=cfg.ppr_tol,
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
    activation_contribution = cfg.w_activation * a
    similarity_contribution = cfg.w_similarity * cosine
    retrieval_contribution = cfg.w_retrieval * score_inputs.retrieval
    excitability_contribution = cfg.w_excitability * score_inputs.excitability
    score = activation_mod.combine_scores(
        score_inputs,
        w_activation=cfg.w_activation,
        w_similarity=cfg.w_similarity,
        w_retrieval=cfg.w_retrieval,
        w_excitability=cfg.w_excitability,
    )

    # step 7: hub penalty (structural usage-PageRank proxy -- see module docstring)
    before_hub = score.copy()
    score = activation_mod.apply_hub_penalty(
        score, usage_pagerank, hub_penalty=cfg.hub_penalty, percentile=cfg.hub_penalty_percentile
    )

    # step 7b: context conditioning
    hub_contribution = score - before_hub
    before_context = score.copy()
    score, excluded_ids, unresolved_context = _apply_context_conditioning(
        conn, node_ids, id_by_name, score, context, context_gain=cfg.context_gain, pref_gain=cfg.pref_gain
    )
    context_contribution = score - before_context

    contraindication_contribution = _contraindication_contributions(
        conn,
        embedder,
        qvec,
        node_ids,
        row_by_id,
        weight=cfg.negative_cue_weight,
    )
    score = score + contraindication_contribution

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
            content_digest=str(row_by_id[nid]["content_sha256"])
            if "content_sha256" in row_by_id[nid].keys()
            else None,
            diagnostics={
                "activation": round(float(activation_contribution[row_index]), 6),
                "similarity": round(float(similarity_contribution[row_index]), 6),
                "retrieval": round(float(retrieval_contribution[row_index]), 6),
                "excitability": round(float(excitability_contribution[row_index]), 6),
                "hub": round(float(hub_contribution[row_index]), 6),
                "context": round(float(context_contribution[row_index]), 6),
                "contraindication": round(float(contraindication_contribution[row_index]), 6),
                "final": round(float(score[row_index]), 6),
                "policy_experimental": 1.0,
            },
        )
        for i, nid in enumerate(top_ids)
        for row_index in [node_ids.index(nid)]
    ]
    return _finalize_route(
        cfg,
        conn,
        query=query,
        k=k,
        candidates=candidates,
        unresolved_context=unresolved_context,
        session_id=session_id,
        registry_size=registry_size,
        policy_id=policy_id,
        policy_digest=policy_digest,
        policy_family=policy_family,
        config_digest=config_digest,
        calibration=calibration,
        exclusions=bound_exclusions,
        missing_context=missing_ctx,
        reason_codes=pin_reasons,
        selection_mechanism=policy_id,
        model_digest=embedder.model_name,
        index_generation_id=index_generation_id,
        snapshot_id=snapshot_id,
        schema_digest=schema_digest,
        tokenizer_digest=tokenizer_digest,
        policy_source=policy_source,
        route_context=route_context,
        server_policy=server_policy,
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
