"""S06 eligibility / RouteContext acceptance tests (AC-S06-01..05 + ATLAS nits).

Test anchors derive from frozen acceptance criteria + C2/C10 contracts — not
from a candidate implementation. Trust inputs use FixtureTrustDecision until S04.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magicite.core.context import (
    ArtifactInventoryEntry,
    GrantNormalizationError,
    HostFact,
    RouteContext,
    ServerPermissionPolicy,
    intersect_grants,
    normalize_grant_id,
)
from magicite.core.eligibility import (
    REASON_ASSET_INVALID,
    REASON_BUDGET_EXCEEDED,
    REASON_CONTEXT_REQUIRED,
    REASON_CYCLE,
    REASON_LIFECYCLE_BLOCKED,
    REASON_PERMISSION_DENIED,
    REASON_QUARANTINED,
    REASON_STALE_DIGEST,
    REASON_UNSUPPORTED_CONSTRAINT,
    REASON_UNTRUSTED_ORIGIN,
    EligibilitySubject,
    FixtureTrustDecision,
    check_dependency_closure,
    evaluate_dependency_eligibility,
    evaluate_eligibility,
    version_satisfies,
)
from magicite.engram import (
    Compatibility,
    EngramRevisionRef,
    Relations,
    RequiredCapability,
    VersionConstraint,
    parse_artifact,
)
from magicite.engram.model_v1 import (
    Capabilities,
    ExtensionValue,
    HostRequirement,
    Risk,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ENGRAM_V1 = FIXTURES / "engram-v1"
ELIG_V1 = FIXTURES / "eligibility-v1"

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64

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
    base: dict[str, object] = {
        "engram_id": engram_id,
        "content_digest": DIGEST_A,
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
    ctx = RouteContext(frameworks={"django": None})
    result = evaluate_eligibility(subject, ctx, _trust(subject.id), POLICY)

    assert result.eligible is False
    assert REASON_CONTEXT_REQUIRED in result.reason_codes
    assert "frameworks.django" in result.missing_fields
    assert result.context_required is True

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
    """AC-S06-03: archived/quarantined deps excluded; closure walks the chain."""
    winner = EligibilitySubject(
        id="egr_cccc0003",
        relations=Relations(requires=[EngramRevisionRef(id="egr_d0000001", version=1)]),
    )
    mid = EligibilitySubject(
        id="egr_d0000001",
        relations=Relations(requires=[EngramRevisionRef(id="egr_d0000002", version=1)]),
    )
    leaf = EligibilitySubject(id="egr_d0000002")
    ctx = RouteContext()

    winner_result = evaluate_eligibility(winner, ctx, _trust(winner.id), POLICY)
    assert winner_result.eligible is True

    archived = evaluate_dependency_eligibility(
        leaf, ctx, _trust(leaf.id, lifecycle_status="archived", admitted=False), POLICY
    )
    assert archived.eligible is False
    assert REASON_LIFECYCLE_BLOCKED in archived.reason_codes

    quarantined = evaluate_dependency_eligibility(
        leaf, ctx, _trust(leaf.id, quarantined=True, admitted=False), POLICY
    )
    assert quarantined.eligible is False
    assert REASON_QUARANTINED in quarantined.reason_codes

    excluded_ctx = RouteContext(excluded_engram_ids=frozenset({leaf.id}))
    excluded = evaluate_dependency_eligibility(leaf, excluded_ctx, _trust(leaf.id), POLICY)
    assert excluded.eligible is False
    assert "user_excluded" in excluded.reason_codes

    catalog = {winner.id: winner, mid.id: mid, leaf.id: leaf}
    trusts = {
        winner.id: _trust(winner.id),
        mid.id: _trust(mid.id),
        leaf.id: _trust(leaf.id, lifecycle_status="archived", admitted=False),
    }

    def resolve(eid: str, _version: int) -> EligibilitySubject | None:
        return catalog.get(eid)

    closure = check_dependency_closure(
        winner,
        resolve,
        ctx,
        lambda eid: trusts[eid],
        POLICY,
    )
    assert closure.eligible is False
    assert leaf.id in closure.denied_chain
    assert winner.id in closure.denied_chain
    assert mid.id in closure.denied_chain
    assert any(d.engram_id == leaf.id for d in closure.denied)
    assert REASON_LIFECYCLE_BLOCKED in closure.reason_codes


def test_unsupported_constraints() -> None:
    """AC-S06-04: malformed ranges / unsupported mandatory vocabulary → not eligible."""
    bad_range = EligibilitySubject(
        id="egr_dddd0004",
        compatibility=Compatibility(
            frameworks={"django": VersionConstraint(scheme="semver", range="^4.2.0")},
        ),
    )
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
    ctx = RouteContext(artifact_inventory=())
    result = evaluate_eligibility(subject, ctx, _trust(subject.id), POLICY)

    assert result.eligible is True
    assert result.unsatisfied_artifacts == ("artifact.wine-prefix",)
    assert REASON_CONTEXT_REQUIRED not in result.reason_codes


def test_body_path_requires_content_digest() -> None:
    """ATLAS MAJOR-1: path=body requires expected_content_digest vs trust."""
    subject = EligibilitySubject(id="egr_body0001")
    trust = _trust(subject.id, content_digest=DIGEST_A)
    ctx = RouteContext()

    missing = evaluate_eligibility(subject, ctx, trust, POLICY, path="body")
    assert missing.eligible is False
    assert REASON_STALE_DIGEST in missing.reason_codes
    assert "expected_content_digest" in missing.missing_fields

    mismatch = evaluate_eligibility(
        subject, ctx, trust, POLICY, path="body", expected_content_digest=DIGEST_B
    )
    assert mismatch.eligible is False
    assert REASON_STALE_DIGEST in mismatch.reason_codes

    ok = evaluate_eligibility(subject, ctx, trust, POLICY, path="body", expected_content_digest=DIGEST_A)
    assert ok.eligible is True


def test_signature_alone_never_routable() -> None:
    """ATLAS MAJOR-2: signature_valid=True does not admit without local trust."""
    subject = EligibilitySubject(id="egr_sig00001")
    ctx = RouteContext()

    not_admitted = evaluate_eligibility(
        subject,
        ctx,
        _trust(subject.id, signature_valid=True, admitted=False, origin_trusted=True),
        POLICY,
    )
    assert not_admitted.eligible is False
    assert REASON_UNTRUSTED_ORIGIN in not_admitted.reason_codes

    untrusted_origin = evaluate_eligibility(
        subject,
        ctx,
        _trust(subject.id, signature_valid=True, admitted=True, origin_trusted=False),
        POLICY,
    )
    assert untrusted_origin.eligible is False
    assert REASON_UNTRUSTED_ORIGIN in untrusted_origin.reason_codes


def test_dependency_closure_cycle_and_budget() -> None:
    """ATLAS MAJOR-3: cycles and depth exhaustion fail closed."""
    a = EligibilitySubject(
        id="egr_c000000a",
        relations=Relations(requires=[EngramRevisionRef(id="egr_c000000b", version=1)]),
    )
    b = EligibilitySubject(
        id="egr_c000000b",
        relations=Relations(requires=[EngramRevisionRef(id="egr_c000000a", version=1)]),
    )
    catalog = {a.id: a, b.id: b}
    ctx = RouteContext()

    cycle = check_dependency_closure(
        a,
        lambda eid, _v: catalog.get(eid),
        ctx,
        lambda eid: _trust(eid),
        POLICY,
    )
    assert cycle.eligible is False
    assert REASON_CYCLE in cycle.reason_codes
    assert a.id in cycle.denied_chain and b.id in cycle.denied_chain

    deep_root = EligibilitySubject(
        id="egr_d100000a",
        relations=Relations(requires=[EngramRevisionRef(id="egr_d100000b", version=1)]),
    )
    deep_mid = EligibilitySubject(
        id="egr_d100000b",
        relations=Relations(requires=[EngramRevisionRef(id="egr_d100000c", version=1)]),
    )
    deep_leaf = EligibilitySubject(id="egr_d100000c")
    deep = {deep_root.id: deep_root, deep_mid.id: deep_mid, deep_leaf.id: deep_leaf}
    budget = check_dependency_closure(
        deep_root,
        lambda eid, _v: deep.get(eid),
        ctx,
        lambda eid: _trust(eid),
        POLICY,
        max_depth=1,
    )
    assert budget.eligible is False
    assert REASON_BUDGET_EXCEEDED in budget.reason_codes


def test_intersect_grants_normalization() -> None:
    """ATLAS MINOR-4: NFKC+casefold, empty/None, reject wildcards."""
    none_req = intersect_grants(request_permissions=None, request_tools=None, server=POLICY)
    assert none_req.permissions == frozenset(
        {normalize_grant_id("perm.read"), normalize_grant_id("perm.write-project")}
    )

    empty = intersect_grants(request_permissions=[], request_tools=[], server=POLICY)
    assert empty.permissions == frozenset()
    assert empty.tools == frozenset()

    # Casefold + NFKC (ﬁ ligature → fi) must still intersect the server ceiling.
    assert normalize_grant_id("PERM.READ") == "perm.read"
    assert normalize_grant_id("perm.\ufb01") == "perm.fi"
    mixed = intersect_grants(
        request_permissions=["PERM.READ", "Perm.Write-Project"],
        request_tools=["ProtonTricks"],
        server=POLICY,
    )
    assert "perm.read" in mixed.permissions
    assert "perm.write-project" in mixed.permissions
    assert "protontricks" in mixed.tools

    with pytest.raises(GrantNormalizationError):
        intersect_grants(request_permissions=["*"], request_tools=None, server=POLICY)
    with pytest.raises(GrantNormalizationError):
        intersect_grants(
            request_permissions=None,
            request_tools=["shell*"],
            server=POLICY,
        )


def test_consumer_fixture_paths_agree() -> None:
    """ATLAS MINOR-5: route + dependency deny + body digest expectations."""
    payload = json.loads((ELIG_V1 / "consumer-paths.json").read_text(encoding="utf-8"))
    art = payload["artifact"]
    route = payload["paths"]["route"]
    dep = payload["paths"]["dependency"]
    body = payload["paths"]["body"]

    assert payload["trust_decision_view"]["signature_alone_never_routable"] is True
    assert "stale_digest" in payload["c10_body_split"]["s06_owns"]

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
    route_trust = _trust(**route["trust"])

    route_result = evaluate_eligibility(subject, ctx, route_trust, POLICY, path="route")
    assert route_result.eligible is route["expect"]["eligible"]
    assert list(route_result.unsatisfied_artifacts) == route["expect"]["unsatisfied_artifacts"]

    dep_subject = EligibilitySubject(id=dep["subject_id"])
    dep_result = evaluate_dependency_eligibility(dep_subject, ctx, _trust(**dep["trust_denied"]), POLICY)
    assert dep_result.eligible is dep["expect"]["eligible"]
    assert any(code in dep_result.reason_codes for code in dep["expect"]["reason_codes_any_of"])

    body_ok = evaluate_eligibility(
        subject,
        ctx,
        route_trust,
        POLICY,
        path="body",
        expected_content_digest=body["expected_content_digest"],
    )
    assert body_ok.eligible is body["expect_ok"]["eligible"]
    assert list(body_ok.unsatisfied_artifacts) == body["expect_ok"]["unsatisfied_artifacts"]

    body_stale = evaluate_eligibility(
        subject,
        ctx,
        route_trust,
        POLICY,
        path="body",
        expected_content_digest=body["expect_stale"]["expected_content_digest"],
    )
    assert body_stale.eligible is False
    assert list(body_stale.reason_codes) == body["expect_stale"]["reason_codes"]


def test_asset_invalid_emitted() -> None:
    """ATLAS NIT: emit asset_invalid when S02 admission flagged assets bad."""
    subject = EligibilitySubject(id="egr_asset001", assets_valid=False)
    result = evaluate_eligibility(subject, RouteContext(), _trust(subject.id), POLICY)
    assert result.eligible is False
    assert REASON_ASSET_INVALID in result.reason_codes


def test_host_id_lowercased() -> None:
    """ATLAS NIT: host ids compared case-insensitively like frameworks."""
    subject = EligibilitySubject(
        id="egr_host0001",
        compatibility=Compatibility(
            hosts=[HostRequirement(id="Cursor", version=VersionConstraint(scheme="semver", range=">=0.40.0"))]
        ),
    )
    ctx = RouteContext(host=HostFact(id="CURSOR", version="0.45.0"))
    result = evaluate_eligibility(subject, ctx, _trust(subject.id), POLICY)
    assert result.eligible is True


def test_trust_view_exceptions_propagate() -> None:
    """ATLAS NIT: trust-view errors propagate; S07 must treat as deny."""

    class BoomTrust:
        @property
        def engram_id(self) -> str:
            raise RuntimeError("trust backend unavailable")

        @property
        def content_digest(self) -> str:
            return DIGEST_A

        @property
        def quarantined(self) -> bool:
            return False

        @property
        def lifecycle_status(self) -> str:
            return "promoted"

        @property
        def origin_trusted(self) -> bool:
            return True

        @property
        def signature_valid(self) -> bool | None:
            return None

        @property
        def admitted(self) -> bool:
            return True

    with pytest.raises(RuntimeError, match="trust backend unavailable"):
        evaluate_eligibility(
            EligibilitySubject(id="egr_boom0001"),
            RouteContext(),
            BoomTrust(),  # type: ignore[arg-type]
            POLICY,
        )


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
