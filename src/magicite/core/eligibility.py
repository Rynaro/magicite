"""Shared eligibility / compatibility evaluator (contracts.md C2 / C10).

Pure domain module owned by S06. Consumed by routing (S07), composition
(S08), and body-load revalidation (S11 after S04 trust). Trust inputs are
expressed as :class:`TrustDecisionView` — S04 later supplies real
``TrustDecision/1`` projections; until then tests use
:class:`FixtureTrustDecision`.

Fail-closed rules (non-negotiable):

* Unknown mandatory host/environment facts → ``context_required`` (not
  silently eligible).
* Malformed / unsupported authored constraints → not eligible.
* Request grants never elevate server policy.
* Artifact-kind prerequisites do **not** deny pre-ranking eligibility;
  they surface as ``unsatisfied_artifacts`` for C5 (AC-S06-05).
* Lifecycle / quarantine / trust denials exclude dependencies the same
  way as route candidates (AC-S06-03); use :func:`check_dependency_closure`
  for transitive walks.
* ``path="body"`` **requires** ``expected_content_digest`` and compares it
  to ``trust.content_digest`` (``stale_digest`` on mismatch/missing). This
  is the only C10 digest check owned here — see "C10 split" below.
* Exceptions raised while reading a trust view **propagate**. S07/S08/S11
  MUST catch and treat as deny (never interpret a raised trust view as
  eligible).

---------------------------------------------------------------------------
TrustDecisionView ↔ C10 TrustDecision/1 (frozen projection contract)
---------------------------------------------------------------------------

Required projection fields (S04 MUST populate; no silent defaults that
admit):

| Field | Semantics | Fail-closed when absent/false |
|---|---|---|
| ``engram_id`` | Must equal the subject id | ``conflict`` |
| ``content_digest`` | Bound content/manifest digest | body: ``stale_digest`` |
| ``quarantined`` | Intake/scanner quarantine | ``quarantined`` |
| ``lifecycle_status`` | Server lifecycle FSM status | not ROUTABLE → ``lifecycle_blocked`` |
| ``origin_trusted`` | Authored / reviewed-import trust | ``untrusted_origin`` |
| ``signature_valid`` | ``True``/``False``/``None`` (N/A) | ``False`` → ``signature_invalid`` |
| ``admitted`` | Local routing/body admission (S04) | ``False`` → deny |

**Who sets ``admitted``:** S04 local review / trust policy only (operator
approve under pinned keys + revocation overlay). Eligibility never
computes admission.

**Imported signature alone NEVER makes an artifact routable (C10):**
``signature_valid=True`` with ``admitted=False`` or ``origin_trusted=False``
MUST remain ineligible. Signatures authenticate bytes; local admission is
a separate control-plane decision.

---------------------------------------------------------------------------
C10 checks that remain S11's (not performed here)
---------------------------------------------------------------------------

* Full offline bundle/manifest Ed25519 verify + key fingerprint pin.
* Revocation / expired-policy key overlay replay at body disclosure time.
* ``stale_decision`` when registry snapshot / policy digest drifted after
  the route decision (beyond the content digest equality check above).
* Refusing to return procedure bytes / sensitive paths in explanations.
* CLI/MCP schema wrapping around eligibility results.

S06 only: eligibility predicate + body ``expected_content_digest`` vs
``trust.content_digest``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from magicite.core.context import (
    ROUTE_CONTEXT_SCHEMA,
    EffectiveGrants,
    RouteContext,
    ServerPermissionPolicy,
    intersect_grants,
    normalize_grant_id,
)
from magicite.engram import (
    KNOWN_EXTENSIONS,
    Compatibility,
    EngramFrontmatterV1,
    EngramV1,
    Relations,
    RequiredCapability,
    Risk,
    VersionConstraint,
    validate_version_constraint,
)
from magicite.engram.model import ROUTABLE_STATUSES
from magicite.engram.model_v1 import Capabilities, ExtensionValue
from magicite.engram.version_constraints import VersionConstraintError

ELIGIBILITY_EVALUATOR_VERSION = "EligibilityEvaluator/1"
DEFAULT_CLOSURE_MAX_DEPTH = 8

#: Stable C2 reason codes (additional codes may appear; never fail open).
REASON_QUARANTINED = "quarantined"
REASON_LIFECYCLE_BLOCKED = "lifecycle_blocked"
REASON_UNTRUSTED_ORIGIN = "untrusted_origin"
REASON_SIGNATURE_INVALID = "signature_invalid"
REASON_VERSION_MISMATCH = "version_mismatch"
REASON_PLATFORM_MISMATCH = "platform_mismatch"
REASON_HOST_MISMATCH = "host_mismatch"
REASON_TOOL_DENIED = "tool_denied"
REASON_RISK_DENIED = "risk_denied"
REASON_CONTEXT_REQUIRED = "context_required"
REASON_ASSET_INVALID = "asset_invalid"
REASON_CONFLICT = "conflict"
REASON_USER_EXCLUDED = "user_excluded"
REASON_UNSUPPORTED_CONSTRAINT = "unsupported_constraint"
REASON_CAPABILITY_DENIED = "capability_denied"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_EVALUATOR_MISMATCH = "evaluator_mismatch"
REASON_STALE_DIGEST = "stale_digest"
REASON_CYCLE = "cycle"
REASON_BUDGET_EXCEEDED = "budget_exceeded"
REASON_DANGLING_DEPENDENCY = "dangling_dependency"

_FILESYSTEM_RANK = {"none": 0, "read-project": 1, "write-project": 2}
_SUBPROCESS_RANK = {"none": 0, "declared-tools": 1}
_NETWORK_RANK = {"none": 0, "optional": 1, "required": 2}
_SECRETS_RANK = {"none": 0, "redacted": 1, "raw": 2}

_SEMVER_CORE = (
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))"
    r"?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)
_SEMVER_EXACT = re.compile(rf"^{_SEMVER_CORE}$")
_SEMVER_COMPARATOR = re.compile(rf"^(<=|>=|<|>|=)\s*({_SEMVER_CORE})$")

#: Lifecycle statuses that may route / compose when trust admits them.
#: Delegates to Engram ROUTABLE_STATUSES (spec §5.1); all else fails closed.
_ROUTABLE_LIFECYCLE: frozenset[str] = ROUTABLE_STATUSES

ResolveFn = Callable[
    [str, int],
    "EligibilitySubject | EngramV1 | EngramFrontmatterV1 | None",
]
TrustViewFn = Callable[[str], "TrustDecisionView"]


@runtime_checkable
class TrustDecisionView(Protocol):
    """Frozen projection of C10 ``TrustDecision/1`` that eligibility reads.

    See module docstring for the field table, ``admitted`` ownership, and the
    rule that an imported signature alone never grants routability. S04 is
    the sole writer of real views; S06 only consumes them.
    """

    @property
    def engram_id(self) -> str: ...

    @property
    def content_digest(self) -> str: ...

    @property
    def quarantined(self) -> bool: ...

    @property
    def lifecycle_status(self) -> str: ...

    @property
    def origin_trusted(self) -> bool: ...

    @property
    def signature_valid(self) -> bool | None:
        """``True``/``False`` when signatures apply; ``None`` if N/A (local)."""
        ...

    @property
    def admitted(self) -> bool:
        """Local admission — set by S04 review, never inferred from signature."""
        ...


@dataclass(frozen=True)
class FixtureTrustDecision:
    """Fixture-backed :class:`TrustDecisionView` for S06 unit/contract tests."""

    engram_id: str
    content_digest: str = "0" * 64
    quarantined: bool = False
    lifecycle_status: str = "promoted"
    origin_trusted: bool = True
    signature_valid: bool | None = None
    admitted: bool = True


@dataclass(frozen=True)
class EligibilitySubject:
    """Minimal Engram 1.0 projection consumed by the evaluator."""

    id: str
    compatibility: Compatibility | None = None
    capabilities: Capabilities | None = None
    risk: Risk | None = None
    relations: Relations | None = None
    extensions: Mapping[str, ExtensionValue] = field(default_factory=dict)
    #: Optional permission ids the artifact declares it needs (policy intersect).
    required_permissions: frozenset[str] = frozenset()
    #: Precomputed S02 asset-admission result. ``False`` → ``asset_invalid``.
    #: Callers that ran ``validate_assets`` / ``load_artifact`` set this;
    #: eligibility does not re-open the registry filesystem.
    assets_valid: bool = True


@dataclass(frozen=True)
class EligibilityResult:
    """Compatibility result (C2). Denied bodies/paths never appear here."""

    engram_id: str
    eligible: bool
    reason_codes: tuple[str, ...]
    missing_fields: tuple[str, ...]
    unsatisfied_artifacts: tuple[str, ...]
    policy_digest: str
    evaluator_version: str = ELIGIBILITY_EVALUATOR_VERSION

    @property
    def context_required(self) -> bool:
        return REASON_CONTEXT_REQUIRED in self.reason_codes


@dataclass(frozen=True)
class DependencyClosureResult:
    """Outcome of a transitive ``relations.requires`` walk (S08 / AC-S06-03)."""

    root_id: str
    eligible: bool
    visited: tuple[str, ...]
    denied: tuple[EligibilityResult, ...]
    reason_codes: tuple[str, ...]
    denied_chain: tuple[str, ...]
    policy_digest: str


def subject_from_frontmatter(fm: EngramFrontmatterV1, *, assets_valid: bool = True) -> EligibilitySubject:
    return EligibilitySubject(
        id=fm.id,
        compatibility=fm.compatibility,
        capabilities=fm.capabilities,
        risk=fm.risk,
        relations=fm.relations,
        extensions=dict(fm.extensions),
        assets_valid=assets_valid,
    )


def subject_from_engram(
    engram: EngramV1 | EngramFrontmatterV1, *, assets_valid: bool = True
) -> EligibilitySubject:
    if isinstance(engram, EngramV1):
        return subject_from_frontmatter(engram.frontmatter, assets_valid=assets_valid)
    return subject_from_frontmatter(engram, assets_valid=assets_valid)


def evaluate_eligibility(
    subject: EligibilitySubject | EngramV1 | EngramFrontmatterV1,
    context: RouteContext,
    trust: TrustDecisionView,
    server_policy: ServerPermissionPolicy,
    *,
    evaluator_version: str = ELIGIBILITY_EVALUATOR_VERSION,
    path: Literal["route", "dependency", "body"] = "route",
    expected_content_digest: str | None = None,
) -> EligibilityResult:
    """Evaluate one artifact for route, dependency, or body-load paths.

    For ``path="body"``, ``expected_content_digest`` is **required** and must
    equal ``trust.content_digest`` or the result is ``stale_digest`` (fail
    closed). Broader C10 body gates (revocation overlay, snapshot/policy
    drift → ``stale_decision``, refusing procedure disclosure) remain S11's.

    Trust-view attribute access is not caught here — exceptions propagate;
    S07 must treat them as deny.
    """
    if isinstance(subject, (EngramV1, EngramFrontmatterV1)):
        subject = subject_from_engram(subject)

    reason_codes: list[str] = []
    missing_fields: list[str] = []
    unsatisfied_artifacts: list[str] = []

    if context.schema_version != ROUTE_CONTEXT_SCHEMA:
        return _result(
            subject.id,
            False,
            [REASON_EVALUATOR_MISMATCH],
            ["route_context.schema_version"],
            [],
            server_policy.policy_digest,
            evaluator_version,
        )
    if evaluator_version != ELIGIBILITY_EVALUATOR_VERSION:
        return _result(
            subject.id,
            False,
            [REASON_EVALUATOR_MISMATCH],
            ["evaluator_version"],
            [],
            server_policy.policy_digest,
            evaluator_version,
        )

    _check_body_digest(path, expected_content_digest, trust, reason_codes, missing_fields)

    grants = intersect_grants(
        request_permissions=context.permission_grants,
        request_tools=context.allowed_tools,
        server=server_policy,
    )

    if subject.id in context.excluded_engram_ids:
        reason_codes.append(REASON_USER_EXCLUDED)

    if not subject.assets_valid:
        reason_codes.append(REASON_ASSET_INVALID)

    _apply_trust(trust, subject.id, reason_codes)

    _check_extensions(subject.extensions, reason_codes)
    _check_authored_constraints(subject, reason_codes)

    if subject.compatibility is not None:
        _check_compatibility(subject.compatibility, context, reason_codes, missing_fields)

    if subject.capabilities is not None:
        _check_capabilities(
            subject.capabilities.requires,
            context,
            reason_codes,
            missing_fields,
            unsatisfied_artifacts,
        )

    if subject.risk is not None:
        _check_risk(subject.risk, grants, server_policy, reason_codes)

    if subject.required_permissions:
        _check_permissions(subject.required_permissions, grants, reason_codes)

    reason_codes = list(dict.fromkeys(reason_codes))
    missing_fields = list(dict.fromkeys(missing_fields))
    unsatisfied_artifacts = list(dict.fromkeys(unsatisfied_artifacts))

    eligible = len(reason_codes) == 0

    return _result(
        subject.id,
        eligible,
        reason_codes,
        missing_fields,
        unsatisfied_artifacts,
        grants.policy_digest,
        evaluator_version,
    )


def evaluate_dependency_eligibility(
    subject: EligibilitySubject | EngramV1 | EngramFrontmatterV1,
    context: RouteContext,
    trust: TrustDecisionView,
    server_policy: ServerPermissionPolicy,
    *,
    expected_content_digest: str | None = None,
) -> EligibilityResult:
    """Single-node dependency check (same predicate, ``path="dependency"``)."""
    return evaluate_eligibility(
        subject,
        context,
        trust,
        server_policy,
        path="dependency",
        expected_content_digest=expected_content_digest,
    )


def check_dependency_closure(
    root: EligibilitySubject | EngramV1 | EngramFrontmatterV1,
    resolve: ResolveFn,
    context: RouteContext,
    trust_view: TrustViewFn,
    server_policy: ServerPermissionPolicy,
    *,
    max_depth: int = DEFAULT_CLOSURE_MAX_DEPTH,
) -> DependencyClosureResult:
    """Walk ``relations.requires`` transitively and re-evaluate each node.

    Fails closed on cycles, depth/budget exhaustion, dangling resolves, and
    any ineligible node. ``denied_chain`` is the path from the root to the
    first blocking condition (cycle edge, missing dep, or denied node).
    """
    root_subject = subject_from_engram(root) if isinstance(root, (EngramV1, EngramFrontmatterV1)) else root
    visited: list[str] = []
    denied: list[EligibilityResult] = []
    aggregate_reasons: list[str] = []
    denied_chain: list[str] = []

    def walk(node: EligibilitySubject, stack: list[str], depth: int) -> bool:
        """Return True if this subtree is fully eligible."""
        if node.id in stack:
            cycle_path = [*stack, node.id]
            denied_chain[:] = cycle_path
            aggregate_reasons.append(REASON_CYCLE)
            return False
        if depth > max_depth:
            denied_chain[:] = [*stack, node.id]
            aggregate_reasons.append(REASON_BUDGET_EXCEEDED)
            return False

        trust = trust_view(node.id)
        result = evaluate_dependency_eligibility(node, context, trust, server_policy)
        if node.id not in visited:
            visited.append(node.id)
        if not result.eligible:
            denied.append(result)
            denied_chain[:] = [*stack, node.id]
            aggregate_reasons.extend(result.reason_codes)
            return False

        requires = list(node.relations.requires) if node.relations is not None else []
        for ref in requires:
            child = resolve(ref.id, ref.version)
            if child is None:
                dangling = _result(
                    ref.id,
                    False,
                    [REASON_DANGLING_DEPENDENCY],
                    [f"relations.requires.{ref.id}@{ref.version}"],
                    [],
                    server_policy.policy_digest,
                    ELIGIBILITY_EVALUATOR_VERSION,
                )
                denied.append(dangling)
                denied_chain[:] = [*stack, node.id, ref.id]
                aggregate_reasons.append(REASON_DANGLING_DEPENDENCY)
                return False
            child_subject = (
                subject_from_engram(child) if isinstance(child, (EngramV1, EngramFrontmatterV1)) else child
            )
            if not walk(child_subject, [*stack, node.id], depth + 1):
                return False
        return True

    ok = walk(root_subject, [], 0)
    reason_codes = tuple(dict.fromkeys(aggregate_reasons))
    return DependencyClosureResult(
        root_id=root_subject.id,
        eligible=ok and len(denied) == 0 and REASON_CYCLE not in reason_codes,
        visited=tuple(visited),
        denied=tuple(denied),
        reason_codes=reason_codes,
        denied_chain=tuple(denied_chain),
        policy_digest=server_policy.policy_digest,
    )


def _check_body_digest(
    path: Literal["route", "dependency", "body"],
    expected_content_digest: str | None,
    trust: TrustDecisionView,
    reason_codes: list[str],
    missing_fields: list[str],
) -> None:
    if path == "body":
        if expected_content_digest is None or expected_content_digest == "":
            reason_codes.append(REASON_STALE_DIGEST)
            missing_fields.append("expected_content_digest")
            return
        trust_digest = trust.content_digest
        if not trust_digest or trust_digest != expected_content_digest:
            reason_codes.append(REASON_STALE_DIGEST)
            if not trust_digest:
                missing_fields.append("trust.content_digest")
            return
        return

    # Optional check on non-body paths when the caller supplies a digest.
    if expected_content_digest is not None and expected_content_digest != "":
        trust_digest = trust.content_digest
        if not trust_digest or trust_digest != expected_content_digest:
            reason_codes.append(REASON_STALE_DIGEST)


def _result(
    engram_id: str,
    eligible: bool,
    reason_codes: Sequence[str],
    missing_fields: Sequence[str],
    unsatisfied_artifacts: Sequence[str],
    policy_digest: str,
    evaluator_version: str,
) -> EligibilityResult:
    return EligibilityResult(
        engram_id=engram_id,
        eligible=eligible,
        reason_codes=tuple(reason_codes),
        missing_fields=tuple(missing_fields),
        unsatisfied_artifacts=tuple(unsatisfied_artifacts),
        policy_digest=policy_digest,
        evaluator_version=evaluator_version,
    )


def _apply_trust(trust: TrustDecisionView, subject_id: str, reason_codes: list[str]) -> None:
    if trust.engram_id != subject_id:
        reason_codes.append(REASON_CONFLICT)
        return
    if trust.quarantined:
        reason_codes.append(REASON_QUARANTINED)
    status = trust.lifecycle_status.lower().strip()
    if status not in _ROUTABLE_LIFECYCLE:
        reason_codes.append(REASON_LIFECYCLE_BLOCKED)
    if not trust.origin_trusted:
        reason_codes.append(REASON_UNTRUSTED_ORIGIN)
    if trust.signature_valid is False:
        reason_codes.append(REASON_SIGNATURE_INVALID)
    if not trust.admitted:
        # Signature alone never admits (C10). admitted=False always blocks;
        # keep a specific code when one already applies, else untrusted_origin.
        if not any(
            c in reason_codes
            for c in (
                REASON_QUARANTINED,
                REASON_UNTRUSTED_ORIGIN,
                REASON_SIGNATURE_INVALID,
                REASON_LIFECYCLE_BLOCKED,
            )
        ):
            reason_codes.append(REASON_UNTRUSTED_ORIGIN)


def _check_extensions(extensions: Mapping[str, ExtensionValue], reason_codes: list[str]) -> None:
    for key, value in extensions.items():
        required = bool(getattr(value, "required", False))
        if required and key not in KNOWN_EXTENSIONS:
            reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)


def _check_authored_constraints(subject: EligibilitySubject, reason_codes: list[str]) -> None:
    """Malformed ranges / unsupported vocabulary → not eligible (AC-S06-04)."""
    compat = subject.compatibility
    if compat is not None:
        for constraint in (
            *compat.languages.values(),
            *compat.frameworks.values(),
            *compat.package_managers.values(),
            *(h.version for h in compat.hosts if h.version is not None),
        ):
            if not _constraint_valid(constraint):
                reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)
                return

    caps = subject.capabilities
    if caps is not None:
        for req in caps.requires:
            if req.kind not in ("host", "artifact"):
                reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)
                return
            if req.version is not None and not _constraint_valid(req.version):
                reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)
                return

    risk = subject.risk
    if risk is not None:
        if risk.filesystem not in _FILESYSTEM_RANK:
            reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)
        if risk.subprocess.mode not in _SUBPROCESS_RANK:
            reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)
        if risk.network.mode not in _NETWORK_RANK:
            reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)
        if risk.secrets not in _SECRETS_RANK:
            reason_codes.append(REASON_UNSUPPORTED_CONSTRAINT)


def _constraint_valid(constraint: VersionConstraint) -> bool:
    try:
        validate_version_constraint(constraint)
    except VersionConstraintError:
        return False
    except ValueError:
        return False
    return True


def _check_compatibility(
    compat: Compatibility,
    context: RouteContext,
    reason_codes: list[str],
    missing_fields: list[str],
) -> None:
    if compat.os:
        if context.platform is None:
            reason_codes.append(REASON_CONTEXT_REQUIRED)
            missing_fields.append("platform")
        elif context.platform not in compat.os:
            reason_codes.append(REASON_PLATFORM_MISMATCH)

    _check_version_map(
        compat.languages,
        context.normalized_languages(),
        "languages",
        reason_codes,
        missing_fields,
    )
    _check_version_map(
        compat.frameworks,
        context.normalized_frameworks(),
        "frameworks",
        reason_codes,
        missing_fields,
    )
    _check_version_map(
        compat.package_managers,
        context.normalized_package_managers(),
        "package_managers",
        reason_codes,
        missing_fields,
    )

    if compat.hosts:
        if context.host is None:
            reason_codes.append(REASON_CONTEXT_REQUIRED)
            missing_fields.append("host")
        else:
            host_id = context.host.id.lower()
            allowed = {h.id.lower(): h for h in compat.hosts}
            if host_id not in allowed:
                reason_codes.append(REASON_HOST_MISMATCH)
            else:
                host_req = allowed[host_id]
                if host_req.version is not None:
                    if context.host.version is None:
                        reason_codes.append(REASON_CONTEXT_REQUIRED)
                        missing_fields.append("host.version")
                    elif not _version_satisfies(context.host.version, host_req.version):
                        reason_codes.append(REASON_VERSION_MISMATCH)


def _check_version_map(
    required: Mapping[str, VersionConstraint],
    provided: Mapping[str, str | None],
    field_name: str,
    reason_codes: list[str],
    missing_fields: list[str],
) -> None:
    for name, constraint in required.items():
        key = str(name).lower()
        if key not in provided:
            reason_codes.append(REASON_CONTEXT_REQUIRED)
            missing_fields.append(f"{field_name}.{key}")
            continue
        value = provided[key]
        if value is None:
            reason_codes.append(REASON_CONTEXT_REQUIRED)
            missing_fields.append(f"{field_name}.{key}")
            continue
        if not _version_satisfies(value, constraint):
            reason_codes.append(REASON_VERSION_MISMATCH)


def _check_capabilities(
    requires: Sequence[RequiredCapability],
    context: RouteContext,
    reason_codes: list[str],
    missing_fields: list[str],
    unsatisfied_artifacts: list[str],
) -> None:
    for req in requires:
        if req.kind == "artifact":
            _check_artifact_requirement(req, context, unsatisfied_artifacts)
            continue
        if context.capabilities is None:
            reason_codes.append(REASON_CONTEXT_REQUIRED)
            missing_fields.append(f"capabilities.{req.id}")
            continue
        if req.id not in context.capabilities:
            reason_codes.append(REASON_CAPABILITY_DENIED)
            continue
        host_ver = context.capabilities[req.id]
        if req.version is not None:
            if host_ver is None:
                reason_codes.append(REASON_CONTEXT_REQUIRED)
                missing_fields.append(f"capabilities.{req.id}")
            elif not _version_satisfies(host_ver, req.version):
                reason_codes.append(REASON_VERSION_MISMATCH)


def _check_artifact_requirement(
    req: RequiredCapability,
    context: RouteContext,
    unsatisfied_artifacts: list[str],
) -> None:
    """Artifact prerequisites never deny pre-ranking eligibility (AC-S06-05)."""
    inventory = context.artifact_inventory
    if inventory is None:
        unsatisfied_artifacts.append(req.id)
        return
    for entry in inventory:
        if entry.id != req.id:
            continue
        if req.version is None:
            return
        if entry.version is None:
            unsatisfied_artifacts.append(req.id)
            return
        if _version_satisfies(entry.version, req.version):
            return
        unsatisfied_artifacts.append(req.id)
        return
    unsatisfied_artifacts.append(req.id)


def _check_risk(
    risk: Risk,
    grants: EffectiveGrants,
    server: ServerPermissionPolicy,
    reason_codes: list[str],
) -> None:
    if _FILESYSTEM_RANK.get(risk.filesystem, 99) > _FILESYSTEM_RANK.get(server.max_filesystem, -1):
        reason_codes.append(REASON_RISK_DENIED)
    if _SUBPROCESS_RANK.get(risk.subprocess.mode, 99) > _SUBPROCESS_RANK.get(server.max_subprocess, -1):
        reason_codes.append(REASON_RISK_DENIED)
    if _NETWORK_RANK.get(risk.network.mode, 99) > _NETWORK_RANK.get(server.max_network, -1):
        reason_codes.append(REASON_RISK_DENIED)
    if _SECRETS_RANK.get(risk.secrets, 99) > _SECRETS_RANK.get(server.max_secrets, -1):
        reason_codes.append(REASON_RISK_DENIED)

    if risk.subprocess.mode == "declared-tools":
        for tool in risk.subprocess.tools:
            if normalize_grant_id(tool) not in grants.tools:
                reason_codes.append(REASON_TOOL_DENIED)
                break


def _check_permissions(required: frozenset[str], grants: EffectiveGrants, reason_codes: list[str]) -> None:
    for perm in required:
        if normalize_grant_id(perm) not in grants.permissions:
            reason_codes.append(REASON_PERMISSION_DENIED)


def version_satisfies(version: str, constraint: VersionConstraint) -> bool:
    """Public helper for S07/S08 version checks against C1 constraints."""
    return _version_satisfies(version, constraint)


def _version_satisfies(version: str, constraint: VersionConstraint) -> bool:
    if not _constraint_valid(constraint):
        return False
    if constraint.scheme == "pep440":
        return _pep440_satisfies(version, constraint.range)
    if constraint.scheme == "semver":
        return _semver_satisfies(version, constraint.range)
    return False


def _pep440_satisfies(version: str, range_text: str) -> bool:
    text = range_text.strip()
    try:
        ver = Version(version)
    except InvalidVersion:
        return False
    try:
        spec = SpecifierSet(text)
        return ver in spec
    except InvalidSpecifier:
        try:
            return ver == Version(text)
        except InvalidVersion:
            return False


def _semver_satisfies(version: str, range_text: str) -> bool:
    text = range_text.strip()
    try:
        ver = Version(version)
    except InvalidVersion:
        return False

    if _SEMVER_EXACT.match(text):
        try:
            return ver == Version(text.split("+", 1)[0])
        except InvalidVersion:
            return False

    parts = [p.strip() for p in text.split(",")]
    for part in parts:
        m = _SEMVER_COMPARATOR.match(part)
        if not m:
            if _SEMVER_EXACT.match(part):
                try:
                    if ver != Version(part.split("+", 1)[0]):
                        return False
                except InvalidVersion:
                    return False
                continue
            return False
        op, raw = m.group(1), m.group(2)
        try:
            bound = Version(raw.split("+", 1)[0])
        except InvalidVersion:
            return False
        if op == "=" and ver != bound:
            return False
        if op == "<" and not (ver < bound):
            return False
        if op == "<=" and not (ver <= bound):
            return False
        if op == ">" and not (ver > bound):
            return False
        if op == ">=" and not (ver >= bound):
            return False
    return True
