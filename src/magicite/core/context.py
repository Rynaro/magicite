"""RouteContext/1 and permission-intersection helpers (contracts.md C2).

Owned by S06. Downstream consumers:

* S07 — passes a ``RouteContext`` into eligibility before ranking/rerank.
* S08 — reuses the same context for composition-time rechecks and
  ``unsatisfied_artifacts`` resolution.
* S04 — does not own this module; supplies trust projections separately
  (see ``magicite.core.eligibility.TrustDecisionView``).

Absent/null facts are **unknown**, never wildcard permission. Empty
collections mean explicitly none. Request grants may only narrow server
policy (AC-S06-02). Grant/tool IDs are NFKC + casefold normalized; bare
``*`` / embedded wildcards are rejected (fail closed).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

ROUTE_CONTEXT_SCHEMA = "RouteContext/1"

PlatformName = Literal["linux", "macos", "windows"]


class GrantNormalizationError(ValueError):
    """Wildcard, empty, or otherwise illegal grant/tool id (fail closed)."""


@dataclass(frozen=True)
class HostFact:
    """Host identity asserted by the caller. ``version is None`` = unknown."""

    id: str
    version: str | None = None


@dataclass(frozen=True)
class ArtifactInventoryEntry:
    """One artifact-kind capability known to be present for the task."""

    id: str
    version: str | None = None


@dataclass(frozen=True)
class ServerPermissionPolicy:
    """Server-side ceiling. Request grants are intersected with this set."""

    allowed_permissions: frozenset[str]
    allowed_tools: frozenset[str]
    policy_digest: str
    #: Risk ceilings; artifact requirements above these → ``risk_denied``.
    max_filesystem: Literal["none", "read-project", "write-project"] = "write-project"
    max_subprocess: Literal["none", "declared-tools"] = "declared-tools"
    max_network: Literal["none", "optional", "required"] = "required"
    max_secrets: Literal["none", "redacted", "raw"] = "raw"


@dataclass(frozen=True)
class EffectiveGrants:
    """Intersection of request grants with server policy (never elevates)."""

    permissions: frozenset[str]
    tools: frozenset[str]
    policy_digest: str
    denied_request_permissions: frozenset[str] = frozenset()
    denied_request_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RouteContext:
    """RouteContext/1 — host assertions, not independently attested truth (C2).

    Mapping values of ``None`` mean the key is present but its version is
    unknown. A missing key for a required dimension is also unknown.
    ``artifact_inventory is None`` means the inventory was omitted (unknown);
    an empty tuple is known-empty (AC-S06-05).
    """

    languages: Mapping[str, str | None] = field(default_factory=dict)
    frameworks: Mapping[str, str | None] = field(default_factory=dict)
    package_managers: Mapping[str, str | None] = field(default_factory=dict)
    platform: PlatformName | None = None
    host: HostFact | None = None
    #: Host-kind capability id → version|None. ``None`` map = unknown inventory.
    capabilities: Mapping[str, str | None] | None = None
    #: Request-asserted permission grants (intersected with server policy).
    permission_grants: frozenset[str] | None = None
    #: Request-asserted allowed tools (intersected with server policy).
    allowed_tools: frozenset[str] | None = None
    artifact_inventory: tuple[ArtifactInventoryEntry, ...] | None = None
    #: Hard exclusions (including composition dependencies).
    excluded_engram_ids: frozenset[str] = frozenset()
    schema_version: str = ROUTE_CONTEXT_SCHEMA

    def normalized_languages(self) -> dict[str, str | None]:
        return {str(k).lower(): v for k, v in self.languages.items()}

    def normalized_frameworks(self) -> dict[str, str | None]:
        return {str(k).lower(): v for k, v in self.frameworks.items()}

    def normalized_package_managers(self) -> dict[str, str | None]:
        return {str(k).lower(): v for k, v in self.package_managers.items()}


def normalize_grant_id(value: str) -> str:
    """NFKC + casefold, matching language/framework key normalization intent."""
    if not isinstance(value, str):
        raise GrantNormalizationError(f"grant id must be str, got {type(value)!r}")
    return unicodedata.normalize("NFKC", value).casefold()


def normalize_grant_set(values: Iterable[str]) -> frozenset[str]:
    """Normalize grant/tool ids; reject empty and wildcard tokens."""
    out: set[str] = set()
    for raw in values:
        normalized = normalize_grant_id(raw)
        if not normalized:
            raise GrantNormalizationError(f"empty grant id rejected: {raw!r}")
        if "*" in normalized:
            raise GrantNormalizationError(f"wildcard grant id rejected: {raw!r}")
        out.add(normalized)
    return frozenset(out)


def intersect_grants(
    *,
    request_permissions: Iterable[str] | None,
    request_tools: Iterable[str] | None,
    server: ServerPermissionPolicy,
) -> EffectiveGrants:
    """Intersect request grants with server policy. Excess is denied, never granted.

    ``None`` request side means "no request narrowing" — effective equals the
    server ceiling (still cannot exceed it). An empty request set means
    explicitly none. IDs are NFKC+casefold normalized; wildcards raise
    :class:`GrantNormalizationError`.
    """
    server_perms = normalize_grant_set(server.allowed_permissions)
    server_tools = normalize_grant_set(server.allowed_tools)

    if request_permissions is None:
        eff_perms = server_perms
        denied_perms: frozenset[str] = frozenset()
    else:
        req_perms = normalize_grant_set(request_permissions)
        eff_perms = req_perms & server_perms
        denied_perms = req_perms - server_perms

    if request_tools is None:
        eff_tools = server_tools
        denied_tools: frozenset[str] = frozenset()
    else:
        req_tools = normalize_grant_set(request_tools)
        eff_tools = req_tools & server_tools
        denied_tools = req_tools - server_tools

    return EffectiveGrants(
        permissions=eff_perms,
        tools=eff_tools,
        policy_digest=server.policy_digest,
        denied_request_permissions=denied_perms,
        denied_request_tools=denied_tools,
    )
