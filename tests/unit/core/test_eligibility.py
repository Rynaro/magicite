"""S06 eligibility / RouteContext acceptance tests (AC-S06-01..05).

Test anchors derive from frozen acceptance criteria + C2 contracts — not from
a candidate implementation. Trust inputs use FixtureTrustDecision until S04.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magicite.core.context import (
    ArtifactInventoryEntry,
    HostFact,
    RouteContext,
    ServerPermissionPolicy,
    intersect_grants,
)
from magicite.core.eligibility import (
    REASON_CONTEXT_REQUIRED,
    REASON_LIFECYCLE_BLOCKED,
    REASON_PERMISSION_DENIED,
    REASON_QUARANTINED,
    REASON_UNSUPPORTED_CONSTRAINT,
    EligibilitySubject,
    FixtureTrustDecision,
    evaluate_dependency_eligibility,
    evaluate_eligibility,
    version_satisfies,
)
from magicite.engram import (
    Compatibility,
    RequiredCapability,
    VersionConstraint,
    parse_artifact,
)
from magicite.engram.model_v1 import (
    Capabilities,
    ExtensionValue,
    Risk,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ENGRAM_V1 = FIXTURES / "engram-v1"
ELIG_V1 = FIXTURES / "eligibility-v1"

POLICY = ServerPermissionPolicy(
    allowed_permissions=frozenset({"perm.read", "perm.write-project"}),
    allowed_tools=frozenset({"protontricks", "shell"}),
    policy_digest="policy-fixture-v1",
    max_filesystem="write-project",
    max_subprocess="declared-tools",
    max_network="optional",
    max_secrets="redacted",
)


def _trust(engram_id: str, **overrides: object) -> FixtureTrustDecision:
    base = {
        "engram_id": engram_id,
        "lifecycle_status": "promoted",
        "quarantined": False,
        "origin_trusted": True,
        "admitted": True,
    }
    base.update(overrides)
    return FixtureTrustDecision(**base)  # type: ignore[arg-type]


def test_unknown_required_context() -> None:
    """AC-S06-01: declared framework range + unknown host version → context_required."""
    subject = EligibilitySubject(
        id="egr_aaaa0001",
        compatibility=Compatibility(
            frameworks={
                "django": VersionConstraint(scheme="semver", range=">=4.2.0,<5.0.0"),
            }
        ),
    )
    # Key present, version explicitly unknown (null).
    ctx = RouteContext(frameworks={"django": None})
    result = evaluate_eligibility(subject, ctx, _trust(subject.id), POLICY)

    assert result.eligible is False
    assert REASON_CONTEXT_REQUIRED in result.reason_codes
    assert "frameworks.django" in result.missing_fields
    assert result.context_required is True

    # Missing key is also unknown (not a wildcard match).
    ctx_missing = RouteContext(frameworks={})
    result_missing = evaluate_eligibility(subject, ctx_missing, _trust(subject.id), POLICY)
    assert result_missing.eligible is False
    assert REASON_CONTEXT_REQUIRED in result_missing.reason_codes


def test_request_cannot_elevate() -> None:
    """AC-S06-02: request grants beyond server policy remain denied."""
    grants = intersect_grants(
        request_permissions=["perm.read", "perm.admin", "perm.raw-secrets"],
        request_tools=["protontricks", "root-shell"],
        server=POLICY,
    )
    assert "perm.admin" not in grants.permissions
    assert "perm.raw-secrets" not in grants.permissions
    assert "root-shell" not in grants.tools
    assert grants.denied_request_permissions == frozenset({"perm.admin", "perm.raw-secrets"})
    assert grants.denied_request_tools == frozenset({"root-shell"})
    assert grants.permissions == frozenset({"perm.read"})

    subject = EligibilitySubject(
        id="egr_bbbb0002",
        required_permissions=frozenset({"perm.admin"}),
    )
    ctx = RouteContext(permission_grants=frozenset({"perm.read", "perm.admin"}))
    result = evaluate_eligibility(subject, ctx, _trust(subject.id), POLICY)
    assert result.eligible is False
    assert REASON_PERMISSION_DENIED in result.reason_codes


def test_transitive_denial() -> None:
    """AC-S06-03: archived or quarantined dependency is excluded."""
    winner = EligibilitySubject(id="egr_cccc0003")
    dep = EligibilitySubject(id="egr_dep00001")
    ctx = RouteContext()

    winner_result = evaluate_eligibility(winner, ctx, _trust(winner.id), POLICY)
    assert winner_result.eligible is True

    archived = evaluate_dependency_eligibility(
        dep, ctx, _trust(dep.id, lifecycle_status="archived", admitted=False), POLICY
    )
    assert archived.eligible is False
    assert REASON_LIFECYCLE_BLOCKED in archived.reason_codes

    quarantined = evaluate_dependency_eligibility(
        dep, ctx, _trust(dep.id, quarantined=True, admitted=False), POLICY
    )
    assert quarantined.eligible is False
    assert REASON_QUARANTINED in quarantined.reason_codes

    # User exclusions apply to composition dependencies too (C2).
    excluded_ctx = RouteContext(excluded_engram_ids=frozenset({dep.id}))
    excluded = evaluate_dependency_eligibility(dep, excluded_ctx, _trust(dep.id), POLICY)
    assert excluded.eligible is False
    assert "user_excluded" in excluded.reason_codes


def test_unsupported_constraints() -> None:
    """AC-S06-04: malformed ranges / unsupported mandatory vocabulary → not eligible."""
    bad_range = EligibilitySubject(
        id="egr_dddd0004",
        compatibility=Compatibility(
            frameworks={"django": VersionConstraint(scheme="semver", range="^4.2.0")},
        ),
    )
    # Bypass pydantic-level validation by constructing VersionConstraint then
    # forcing an invalid range the grammar rejects at evaluate time.
    # VersionConstraint itself allows any string; validate_version_constraint fails.
    ctx = RouteContext(frameworks={"django": "4.2.1"})
    result = evaluate_eligibility(bad_range, ctx, _trust(bad_range.id), POLICY)
    assert result.eligible is False
    assert REASON_UNSUPPORTED_CONSTRAINT in result.reason_codes

    unknown_ext = EligibilitySubject(
        id="egr_dddd0005",
        extensions={"vendor.experimental": ExtensionValue(required=True)},
    )
    ext_result = evaluate_eligibility(unknown_ext, RouteContext(), _trust(unknown_ext.id), POLICY)
    assert ext_result.eligible is False
    assert REASON_UNSUPPORTED_CONSTRAINT in ext_result.reason_codes

    bad_cap = EligibilitySubject(
        id="egr_dddd0006",
        capabilities=Capabilities(
            requires=[
                RequiredCapability(
                    kind="host",
                    id="host.fs.read-project",
                    version=VersionConstraint(scheme="semver", range="1.x"),
                )
            ]
        ),
    )
    cap_ctx = RouteContext(capabilities={"host.fs.read-project": "1.0.0"})
    cap_result = evaluate_eligibility(bad_cap, cap_ctx, _trust(bad_cap.id), POLICY)
    assert cap_result.eligible is False
    assert REASON_UNSUPPORTED_CONSTRAINT in cap_result.reason_codes


def test_plannable_requirement_is_not_host_denial() -> None:
    """AC-S06-05: known-empty inventory → eligible + unsatisfied_artifacts."""
    subject = EligibilitySubject(
        id="egr_eeee0007",
        capabilities=Capabilities(
            requires=[
                RequiredCapability(
                    kind="artifact",
                    id="artifact.wine-prefix",
                    version=VersionConstraint(scheme="semver", range=">=1.0.0"),
                )
            ]
        ),
    )
    ctx = RouteContext(artifact_inventory=())  # known-empty
    result = evaluate_eligibility(subject, ctx, _trust(subject.id), POLICY)

    assert result.eligible is True
    assert result.unsatisfied_artifacts == ("artifact.wine-prefix",)
    assert REASON_CONTEXT_REQUIRED not in result.reason_codes


def test_consumer_fixture_paths_agree() -> None:
    """Same artifact through route path remains eligible with artifact gap listed."""
    payload = json.loads((ELIG_V1 / "consumer-paths.json").read_text(encoding="utf-8"))
    art = payload["artifact"]
    route = payload["paths"]["route"]

    subject = EligibilitySubject(
        id=art["id"],
        compatibility=Compatibility.model_validate(art["compatibility"]),
        capabilities=Capabilities.model_validate(art["capabilities"]),
        risk=Risk.model_validate(art["risk"]),
        required_permissions=frozenset(art["required_permissions"]),
    )
    rctx = route["context"]
    ctx = RouteContext(
        platform=rctx["platform"],
        frameworks=rctx["frameworks"],
        host=HostFact(**rctx["host"]),
        capabilities=rctx["capabilities"],
        permission_grants=frozenset(rctx["permission_grants"]),
        allowed_tools=frozenset(rctx["allowed_tools"]),
        artifact_inventory=tuple(
            ArtifactInventoryEntry(**e) if isinstance(e, dict) else e for e in rctx["artifact_inventory"]
        ),
    )
    trust = _trust(**route["trust"])

    for path_name in ("route", "dependency", "body"):
        result = evaluate_eligibility(subject, ctx, trust, POLICY, path=path_name)  # type: ignore[arg-type]
        assert result.eligible is route["expect"]["eligible"]
        assert list(result.unsatisfied_artifacts) == route["expect"]["unsatisfied_artifacts"]


def test_sample_host_tooling_engram_fixture() -> None:
    """Positive S02 fixture evaluates under a complete RouteContext."""
    text = (ENGRAM_V1 / "positive" / "sample-host-tooling.egr.md").read_text(encoding="utf-8")
    engram, _ = parse_artifact(text, relpath="positive/sample-host-tooling.egr.md")
    assert engram.frontmatter.spec == "engram/1.0"

    ctx = RouteContext(
        platform="linux",
        languages={"python": "3.12"},
        host=HostFact(id="cursor", version="0.45.0"),
        capabilities={"host.fs.read-project": "1.2.0"},
        allowed_tools=frozenset({"protontricks"}),
        artifact_inventory=(),
    )
    result = evaluate_eligibility(engram, ctx, _trust(engram.id), POLICY, path="route")
    assert result.eligible is True
    assert "artifact.wine-prefix" in result.unsatisfied_artifacts


def test_version_satisfies_semver_and_pep440() -> None:
    assert version_satisfies("4.2.1", VersionConstraint(scheme="semver", range=">=4.2.0,<5.0.0"))
    assert not version_satisfies("5.0.0", VersionConstraint(scheme="semver", range=">=4.2.0,<5.0.0"))
    assert version_satisfies("3.12", VersionConstraint(scheme="pep440", range=">=3.11,<4"))
    assert not version_satisfies("3.10", VersionConstraint(scheme="pep440", range=">=3.11,<4"))


@pytest.mark.parametrize(
    "status",
    ["draft", "archived", "unknown-status"],
)
def test_lifecycle_fail_closed(status: str) -> None:
    subject = EligibilitySubject(id="egr_ffff0008")
    result = evaluate_eligibility(
        subject, RouteContext(), _trust(subject.id, lifecycle_status=status), POLICY
    )
    assert result.eligible is False
    assert REASON_LIFECYCLE_BLOCKED in result.reason_codes
