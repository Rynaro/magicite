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


def compute_policy_digest(policy_id: str, cfg: Config) -> str:
    """SHA-256 over the policy's scoring-semantic fingerprint (no DB state)."""
    if policy_id == POLICY_DENSE_V1:
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
        }
    else:
        raise InvalidInputError(f"unknown routing_policy {policy_id!r}")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def query_fingerprint(query: str) -> str:
    """Non-reversible query handle for route events (AC-S00-04 / C6)."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()
