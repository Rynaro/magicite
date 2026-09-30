"""Stable vs experimental routing-policy identity (V1 S00 / contracts C0, C4, C7).

The default incumbent is nonadaptive ``dense-v1``: eligible cosine similarity
with stable-ID tie-breaks. Legacy adaptive blend (graph activation, retrieval
strength, excitability, community rerank) is preserved only behind an explicit
experimental policy id. Dream may still checkpoint historical strength; it
must never mutate this module's digest inputs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from magicite.config import Config
from magicite.errors import InvalidInputError

POLICY_DENSE_V1 = "dense-v1"
POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1 = "experimental/adaptive-blend-v1"

DEFAULT_ROUTING_POLICY = POLICY_DENSE_V1

STABLE_POLICY_IDS: frozenset[str] = frozenset({POLICY_DENSE_V1})
EXPERIMENTAL_POLICY_IDS: frozenset[str] = frozenset({POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1})
KNOWN_POLICY_IDS: frozenset[str] = STABLE_POLICY_IDS | EXPERIMENTAL_POLICY_IDS

PolicyFamily = Literal["stable", "experimental"]


def resolve_policy_id(cfg: Config) -> str:
    """Return the configured routing policy id, defaulting to dense-v1."""
    raw = getattr(cfg, "routing_policy", DEFAULT_ROUTING_POLICY)
    policy_id = (raw or DEFAULT_ROUTING_POLICY).strip()
    if policy_id not in KNOWN_POLICY_IDS:
        raise InvalidInputError(
            f"unknown routing_policy {policy_id!r}; known: {', '.join(sorted(KNOWN_POLICY_IDS))}"
        )
    return policy_id


def policy_family(policy_id: str) -> PolicyFamily:
    if policy_id in EXPERIMENTAL_POLICY_IDS:
        return "experimental"
    if policy_id in STABLE_POLICY_IDS:
        return "stable"
    raise InvalidInputError(f"unknown routing_policy {policy_id!r}")


def is_stable_policy(policy_id: str) -> bool:
    return policy_family(policy_id) == "stable"


def active_stable_policy_digest(cfg: Config) -> str:
    """Digest of the active *stable* incumbent semantics.

    Always fingerprints ``dense-v1`` scoring inputs (never learned node/edge
    strengths, usage, community assignments, or Dream checkpoints). When the
    configured request policy is experimental, the stable digest still
    describes the nonadaptive incumbent that rollback restores.
    """
    return compute_policy_digest(POLICY_DENSE_V1, cfg)


def compute_policy_digest(
    policy_id: str,
    cfg: Config,
    *,
    calibration_digest: str | None = None,
    eligibility_evaluator_version: str = "EligibilityEvaluator/1",
    fusion: str | None = None,
    reranker_fallback: str | None = None,
) -> str:
    """SHA-256 over the policy's scoring-semantic fingerprint (no DB state).

    ``dense-v1`` fingerprints the fixed cosine + stable-ID incumbent plus
    S07 stable knobs that change selection/abstention semantics (thresholds,
    margins, fallback identity, calibration digest, eligibility evaluator,
    fusion identity). Digest changes iff stable ranking/abstention semantics
    change. Do not fold Dream-learned strengths or experimental-only knobs
    into the dense digest.
    """
    if policy_id == POLICY_DENSE_V1:
        fb = reranker_fallback if reranker_fallback is not None else cfg.reranker_fallback
        payload: dict[str, object] = {
            "policy_id": POLICY_DENSE_V1,
            "family": "stable",
            "selection": "cosine_similarity",
            "tie_break": "stable_id",
            "uses_activation": False,
            "uses_retrieval_strength": False,
            "uses_excitability": False,
            "uses_community_rerank": False,
            "uses_learned_edges": False,
            "eligibility_evaluator_version": eligibility_evaluator_version,
            "fusion": fusion or "none",
            "reranker_provider": cfg.reranker_provider or "none",
            "reranker_fallback": fb or "none",
            "reranker_required": bool(cfg.reranker_required),
            "reranker_timeout_s": float(cfg.reranker_timeout_s),
            "candidate_refill_limit": int(cfg.candidate_refill_limit),
            "abstention_enabled": bool(cfg.abstention_enabled),
            "abstention_score_threshold": float(cfg.abstention_score_threshold),
            "abstention_margin_threshold": float(cfg.abstention_margin_threshold),
            "default_local_authorship_admission": bool(cfg.default_local_authorship_admission),
            "calibration_digest": calibration_digest or "none",
        }
    elif policy_id == POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1:
        payload = {
            "policy_id": POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1,
            "family": "experimental",
            "selection": "adaptive_blend",
            "tie_break": "name",
            "w_activation": cfg.w_activation,
            "w_similarity": cfg.w_similarity,
            "w_retrieval": cfg.w_retrieval,
            "w_excitability": cfg.w_excitability,
            "hub_penalty": cfg.hub_penalty,
            "hub_penalty_percentile": cfg.hub_penalty_percentile,
            "context_gain": cfg.context_gain,
            "pref_gain": cfg.pref_gain,
            "negative_cue_weight": cfg.negative_cue_weight,
            "declared_edge_strength": cfg.declared_edge_strength,
            "ablation_no_communities": cfg.ablation_no_communities,
            "ppr_restart": cfg.ppr_restart,
            "temperature": cfg.temperature,
            "inhib_gain": cfg.inhib_gain,
            "type_gain": dict(sorted(cfg.type_gain.items())),
            "eligibility_evaluator_version": eligibility_evaluator_version,
            "reranker_fallback": (
                reranker_fallback if reranker_fallback is not None else cfg.reranker_fallback
            )
            or "none",
            "default_local_authorship_admission": bool(cfg.default_local_authorship_admission),
            "calibration_digest": calibration_digest or "none",
        }
    else:
        raise InvalidInputError(f"unknown routing_policy {policy_id!r}")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return bind_server_ceiling_digest(hashlib.sha256(canonical.encode("utf-8")).hexdigest(), cfg)


def bind_server_ceiling_digest(base_digest: str, cfg: Config) -> str:
    """Bind operator grants to effective identity while preserving empty-ceiling legacy pins."""
    from magicite.core.context import normalize_grant_set

    permissions = sorted(normalize_grant_set(cfg.allowed_permissions))
    tools = sorted(normalize_grant_set(cfg.allowed_tools))
    if not permissions and not tools:
        return base_digest
    payload = {"policy_digest": base_digest, "allowed_permissions": permissions, "allowed_tools": tools}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compute_config_digest(cfg: Config) -> str:
    """Stable digest of config knobs that affect route selection semantics."""
    payload = {
        "routing_policy": cfg.routing_policy,
        "allowed_permissions": sorted(cfg.allowed_permissions),
        "allowed_tools": sorted(cfg.allowed_tools),
        "reranker_provider": cfg.reranker_provider,
        "reranker_timeout_s": cfg.reranker_timeout_s,
        "reranker_fallback": cfg.reranker_fallback,
        "reranker_required": cfg.reranker_required,
        "candidate_refill_limit": cfg.candidate_refill_limit,
        "max_exclusion_summaries": cfg.max_exclusion_summaries,
        "abstention_enabled": cfg.abstention_enabled,
        "abstention_score_threshold": cfg.abstention_score_threshold,
        "abstention_margin_threshold": cfg.abstention_margin_threshold,
        "default_local_authorship_admission": cfg.default_local_authorship_admission,
        "plan_max_depth": cfg.plan_max_depth,
        "plan_max_size": cfg.plan_max_size,
        "declared_edge_strength": cfg.declared_edge_strength,
        "embedding_provider": cfg.embedding_provider,
        "embedding_dim": cfg.embedding_dim,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
