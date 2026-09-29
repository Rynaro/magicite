"""S08 Plan/1 composition integration tests (AC-S08-02..06).

Anchors derive from frozen C5 / acceptance criteria + S02 relation fixtures —
not from legacy expand() cycle-breaking behaviour.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from magicite.core.composition import (
    REASON_AMBIGUOUS_PROVIDER,
    CompositionLimits,
    compose,
)
from magicite.core.eligibility import (
    REASON_BUDGET_EXCEEDED,
    REASON_CONFLICT,
    REASON_CYCLE,
    REASON_TOOL_DENIED,
)
from magicite.engram import (
    EngramRevisionRef,
    ProducedCapability,
    RequiredCapability,
    VersionConstraint,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
COMPOSITION_V1 = FIXTURES / "composition-v1"
ENGRAM_V1 = FIXTURES / "engram-v1"


def _hv():
    import sys

    path = COMPOSITION_V1 / "host_verifier.py"
    name = "composition_v1_host_verifier"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cycle_and_budget() -> None:
    """AC-S08-02: cycle or exceeded node/depth/edge limit ⇒ no executable prefix.

    Contrasts with legacy expand(), which breaks cycles and still returns an
    order (cycle_broken=True) that callers historically treated as a plan.
    """
    hv = _hv()
    a = hv.node(
        "egr_c0000001",
        relation_requires=[EngramRevisionRef(id="egr_c0000002", version=1)],
    )
    b = hv.node(
        "egr_c0000002",
        relation_requires=[EngramRevisionRef(id="egr_c0000001", version=1)],
    )
    snap = hv.snapshot([a, b])
    ctx = hv.base_context()
    trusts = {n.id: hv.trust(n.id) for n in (a, b)}

    cycle_plan = compose(
        ["egr_c0000001"],
        ctx,
        snap,
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert cycle_plan.status == "invalid"
    assert cycle_plan.executable is False
    assert cycle_plan.topological_order == ()
    assert REASON_CYCLE in {d.code for d in cycle_plan.diagnostics}
    # Legacy diagnostic order may exist but must not be executable.
    if cycle_plan.legacy_order is not None:
        assert set(cycle_plan.legacy_order) == {"egr_c0000001", "egr_c0000002"}

    # Depth budget: chain of 3 with max_depth=1.
    root = hv.node(
        "egr_d1000001",
        relation_requires=[EngramRevisionRef(id="egr_d1000002", version=1)],
    )
    mid = hv.node(
        "egr_d1000002",
        relation_requires=[EngramRevisionRef(id="egr_d1000003", version=1)],
    )
    leaf = hv.node("egr_d1000003")
    deep_snap = hv.snapshot([root, mid, leaf])
    deep_trusts = {n.id: hv.trust(n.id) for n in (root, mid, leaf)}
    budget_plan = compose(
        ["egr_d1000001"],
        ctx,
        deep_snap,
        CompositionLimits(max_nodes=16, max_edges=32, max_depth=1),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: deep_trusts[eid],
    )
    assert budget_plan.status == "invalid"
    assert budget_plan.executable is False
    assert budget_plan.topological_order == ()
    assert REASON_BUDGET_EXCEEDED in {d.code for d in budget_plan.diagnostics}

    # Node budget: selected set already over limit.
    nodes = [hv.node(f"egr_{i:08x}") for i in range(1, 5)]
    over = compose(
        [n.id for n in nodes],
        ctx,
        hv.snapshot(nodes),
        CompositionLimits(max_nodes=2, max_edges=32, max_depth=8),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: hv.trust(eid),
    )
    assert over.status == "invalid"
    assert over.executable is False
    assert over.topological_order == ()
    assert REASON_BUDGET_EXCEEDED in {d.code for d in over.diagnostics}


def test_ambiguous_alternatives() -> None:
    """AC-S08-03: two satisfying producers, no preference ⇒ ambiguous_provider."""
    hv = _hv()
    consumer = hv.node(
        "egr_b0000002",
        requires=[
            RequiredCapability(
                kind="artifact",
                id="artifact.shared-x",
                version=VersionConstraint(scheme="semver", range=">=1.0.0"),
            )
        ],
    )
    prod_a = hv.node(
        "egr_a000000a",
        produces=[ProducedCapability(kind="artifact", id="artifact.shared-x", version="1.0.0")],
    )
    prod_b = hv.node(
        "egr_a000000b",
        produces=[ProducedCapability(kind="artifact", id="artifact.shared-x", version="1.1.0")],
    )
    snap = hv.snapshot([consumer, prod_a, prod_b])
    ctx = hv.base_context(artifact_inventory=())
    trusts = {n.id: hv.trust(n.id) for n in (consumer, prod_a, prod_b)}

    plan = compose(
        ["egr_b0000002"],
        ctx,
        snap,
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert plan.status == "invalid"
    assert plan.executable is False
    assert plan.topological_order == ()
    assert REASON_AMBIGUOUS_PROVIDER in {d.code for d in plan.diagnostics}

    # Explicit policy preference resolves ambiguity.
    snap_pref = hv.snapshot(
        [consumer, prod_a, prod_b],
        preferred_providers={"artifact.shared-x": "egr_a000000a"},
    )
    resolved = compose(
        ["egr_b0000002"],
        ctx,
        snap_pref,
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert resolved.status == "valid"
    assert resolved.executable is True
    assert resolved.topological_order.index("egr_a000000a") < resolved.topological_order.index(
        "egr_b0000002"
    )


def test_host_verifier_report() -> None:
    """AC-S08-04: report distinguishes structural validity from verified task outcome."""
    hv = _hv()
    alone = hv.node("egr_e0000001")
    snap = hv.snapshot([alone])
    ctx = hv.base_context()
    plan = compose(
        ["egr_e0000001"],
        ctx,
        snap,
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: hv.trust(eid),
    )
    assert plan.status == "valid"

    task = hv.load_echo_task()
    report = hv.run_deterministic_host_verifier(plan, task)
    assert report.structural_validity == "valid"
    assert report.verified_task_outcome == "pass"
    # Distinct fields: structural validity is independent of task outcome.
    assert hasattr(report, "structural_validity")
    assert hasattr(report, "verified_task_outcome")
    assert report.structural_validity == "valid"
    assert report.verified_task_outcome in {"pass", "fail", "unevaluated"}
    assert report.verifier_type == "deterministic_test"
    assert report.verifier_artifact_digest == task.artifact_digest

    # Invalid plan: structural invalid, task outcome unevaluated (never executed).
    cyclic_a = hv.node(
        "egr_c0000001",
        relation_requires=[EngramRevisionRef(id="egr_c0000002", version=1)],
    )
    cyclic_b = hv.node(
        "egr_c0000002",
        relation_requires=[EngramRevisionRef(id="egr_c0000001", version=1)],
    )
    bad = compose(
        ["egr_c0000001"],
        ctx,
        hv.snapshot([cyclic_a, cyclic_b]),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: hv.trust(eid),
    )
    bad_report = hv.run_deterministic_host_verifier(bad, task)
    assert bad_report.structural_validity == "invalid"
    assert bad_report.verified_task_outcome == "unevaluated"
    assert bad_report.structural_validity != bad_report.verified_task_outcome

    # Empirical/external claims stay unevaluated when verifier is not run.
    from magicite.core.composition import host_verification_report

    uneval = host_verification_report(
        plan,
        verified_task_outcome="unevaluated",
        verifier_type="external_verified",
        verifier_id="external-unset",
        verifier_version="0",
        verifier_artifact_digest="0" * 64,
        details="external claim not executed in this harness",
    )
    assert uneval.verified_task_outcome == "unevaluated"
    assert uneval.structural_validity == "valid"


def test_producer_satisfies_consumer() -> None:
    """AC-S08-05: known-empty inventory + B requires X + eligible A produces X ⇒ A before B.

    Paired case: denied host tool on B ⇒ invalid even when a producer exists.
    """
    hv = _hv()
    producer = hv.node(
        "egr_a0000001",
        produces=[
            ProducedCapability(kind="artifact", id="artifact.wine-prefix", version="1.0.0")
        ],
    )
    consumer = hv.node(
        "egr_b0000001",
        requires=[
            RequiredCapability(
                kind="artifact",
                id="artifact.wine-prefix",
                version=VersionConstraint(scheme="semver", range=">=1.0.0"),
            )
        ],
    )
    snap = hv.snapshot([producer, consumer])
    ctx = hv.base_context(artifact_inventory=())
    trusts = {
        "egr_a0000001": hv.trust("egr_a0000001"),
        "egr_b0000001": hv.trust("egr_b0000001"),
    }

    plan = compose(
        ["egr_b0000001"],
        ctx,
        snap,
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert plan.status == "valid"
    assert plan.executable is True
    assert plan.topological_order == ("egr_a0000001", "egr_b0000001")
    assert any(e.type == "produces" and e.src_id == "egr_a0000001" for e in plan.edges)

    # Paired denied-host-tool case: consumer requires a tool not in grants.
    denied_consumer = hv.node(
        "egr_b0000001",
        requires=[
            RequiredCapability(
                kind="artifact",
                id="artifact.wine-prefix",
                version=VersionConstraint(scheme="semver", range=">=1.0.0"),
            )
        ],
        risk=hv.risk_with_tool("forbidden-tool"),
    )
    denied_plan = compose(
        ["egr_b0000001"],
        ctx,
        hv.snapshot([producer, denied_consumer]),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert denied_plan.status == "invalid"
    assert denied_plan.executable is False
    assert denied_plan.topological_order == ()
    assert REASON_TOOL_DENIED in {d.code for d in denied_plan.diagnostics}


def test_shared_relation_semantics() -> None:
    """AC-S08-06: before/supersedes from S02 shared fixtures match C1 semantics."""
    hv = _hv()
    shared = hv.load_shared_relation_fixtures()
    assert shared["schema"] == "magicite/engram-v1-relation-fixtures/1"

    before_refs = [EngramRevisionRef(**r) for r in shared["relations"]["before"]]
    supersedes_refs = [EngramRevisionRef(**r) for r in shared["relations"]["supersedes"]]

    # Fixture engram egr_33333333 declares before + supersedes (relations/before-supersedes.egr.md).
    subject = hv.node(
        "egr_33333333",
        before=before_refs,
        supersedes=supersedes_refs,
    )
    # before target present at exact revision → orders.
    before_target = hv.node("egr_aaaa0001", version=2, content_digest=hv.DIGEST_B)
    # before target wrong revision when selected → invalid reference.
    # supersedes target at exact revision → conflict when both selected.
    superseded = hv.node("egr_bbbb0001", version=4, content_digest=hv.DIGEST_C)
    # Inactive before target (absent) must not select or fail.
    # egr_aaaa0002@version=1 absent from selection → inactive.

    ctx = hv.base_context()
    trusts = {
        "egr_33333333": hv.trust("egr_33333333"),
        "egr_aaaa0001": hv.trust("egr_aaaa0001", content_digest=hv.DIGEST_B),
        "egr_bbbb0001": hv.trust("egr_bbbb0001", content_digest=hv.DIGEST_C),
    }

    ordered = compose(
        ["egr_33333333", "egr_aaaa0001"],
        ctx,
        hv.snapshot([subject, before_target]),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert ordered.status == "valid"
    assert ordered.topological_order.index("egr_aaaa0001") < ordered.topological_order.index(
        "egr_33333333"
    )
    assert any(e.type == "before" and e.src_id == "egr_aaaa0001" for e in ordered.edges)

    conflicted = compose(
        ["egr_33333333", "egr_bbbb0001"],
        ctx,
        hv.snapshot([subject, superseded]),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert conflicted.status == "invalid"
    assert conflicted.executable is False
    assert conflicted.topological_order == ()
    assert REASON_CONFLICT in {d.code for d in conflicted.diagnostics}

    # Wrong before revision selected → invalid_reference (C1).
    wrong_rev = hv.node("egr_aaaa0001", version=1, content_digest=hv.DIGEST_D)
    trusts_wrong = {
        "egr_33333333": hv.trust("egr_33333333"),
        "egr_aaaa0001": hv.trust("egr_aaaa0001", content_digest=hv.DIGEST_D),
    }
    bad_ref = compose(
        ["egr_33333333", "egr_aaaa0001"],
        ctx,
        hv.snapshot([subject, wrong_rev]),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts_wrong[eid],
    )
    assert bad_ref.status == "invalid"
    assert bad_ref.executable is False
    assert "invalid_reference" in {d.code for d in bad_ref.diagnostics}

    assert (ENGRAM_V1 / "relations" / "before-supersedes.egr.md").is_file()


def test_edge_budget_never_exceeds_max() -> None:
    """ATLAS nit: relation-edge appends must check budget *before* each append.

    With max_edges=10, an invalid budget plan must never retain more than 10 edges.
    """
    hv = _hv()
    # 20 independent peers all selected; subject declares before→each peer.
    peers = [hv.node(f"egr_{i:08x}") for i in range(1, 21)]
    subject = hv.node(
        "egr_33333333",
        before=[EngramRevisionRef(id=p.id, version=1) for p in peers],
    )
    nodes = [subject, *peers]
    trusts = {n.id: hv.trust(n.id, content_digest=n.content_digest) for n in nodes}
    plan = compose(
        [n.id for n in nodes],
        hv.base_context(),
        hv.snapshot(nodes),
        CompositionLimits(max_nodes=64, max_edges=10, max_depth=8),
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )
    assert plan.status == "invalid"
    assert plan.executable is False
    assert plan.topological_order == ()
    assert REASON_BUDGET_EXCEEDED in {d.code for d in plan.diagnostics}
    assert len(plan.edges) <= 10


def _corpus_world(hv, case_id: str):
    """Materialize snapshot/context/trusts for a structural-corpus case id."""
    from magicite.engram import ProducedCapability, RequiredCapability, VersionConstraint

    if case_id == "producer-before-consumer":
        producer = hv.node(
            "egr_a0000001",
            produces=[
                ProducedCapability(kind="artifact", id="artifact.wine-prefix", version="1.0.0")
            ],
        )
        consumer = hv.node(
            "egr_b0000001",
            requires=[
                RequiredCapability(
                    kind="artifact",
                    id="artifact.wine-prefix",
                    version=VersionConstraint(scheme="semver", range=">=1.0.0"),
                )
            ],
        )
        nodes = [producer, consumer]
        return hv.snapshot(nodes), hv.base_context(artifact_inventory=()), {
            n.id: hv.trust(n.id, content_digest=n.content_digest) for n in nodes
        }

    if case_id == "ambiguous-two-producers":
        consumer = hv.node(
            "egr_b0000002",
            requires=[
                RequiredCapability(
                    kind="artifact",
                    id="artifact.shared-x",
                    version=VersionConstraint(scheme="semver", range=">=1.0.0"),
                )
            ],
        )
        prod_a = hv.node(
            "egr_a000000a",
            produces=[ProducedCapability(kind="artifact", id="artifact.shared-x", version="1.0.0")],
        )
        prod_b = hv.node(
            "egr_a000000b",
            produces=[ProducedCapability(kind="artifact", id="artifact.shared-x", version="1.1.0")],
        )
        nodes = [consumer, prod_a, prod_b]
        return hv.snapshot(nodes), hv.base_context(artifact_inventory=()), {
            n.id: hv.trust(n.id, content_digest=n.content_digest) for n in nodes
        }

    if case_id == "cycle-no-executable-prefix":
        a = hv.node(
            "egr_c0000001",
            relation_requires=[EngramRevisionRef(id="egr_c0000002", version=1)],
        )
        b = hv.node(
            "egr_c0000002",
            relation_requires=[EngramRevisionRef(id="egr_c0000001", version=1)],
        )
        nodes = [a, b]
        return hv.snapshot(nodes), hv.base_context(), {
            n.id: hv.trust(n.id, content_digest=n.content_digest) for n in nodes
        }

    if case_id == "before-ordering":
        shared = hv.load_shared_relation_fixtures()
        before_refs = [EngramRevisionRef(**r) for r in shared["relations"]["before"]]
        subject = hv.node("egr_33333333", before=before_refs)
        before_target = hv.node("egr_aaaa0001", version=2, content_digest=hv.DIGEST_B)
        nodes = [subject, before_target]
        return hv.snapshot(nodes), hv.base_context(), {
            "egr_33333333": hv.trust("egr_33333333"),
            "egr_aaaa0001": hv.trust("egr_aaaa0001", content_digest=hv.DIGEST_B),
        }

    if case_id == "supersedes-conflict":
        shared = hv.load_shared_relation_fixtures()
        supersedes_refs = [EngramRevisionRef(**r) for r in shared["relations"]["supersedes"]]
        subject = hv.node("egr_33333333", supersedes=supersedes_refs)
        superseded = hv.node("egr_bbbb0001", version=4, content_digest=hv.DIGEST_C)
        nodes = [subject, superseded]
        return hv.snapshot(nodes), hv.base_context(), {
            "egr_33333333": hv.trust("egr_33333333"),
            "egr_bbbb0001": hv.trust("egr_bbbb0001", content_digest=hv.DIGEST_C),
        }

    raise KeyError(f"unknown corpus case: {case_id}")


def _load_structural_corpus() -> list[dict]:
    data = json.loads((COMPOSITION_V1 / "structural-corpus.json").read_text(encoding="utf-8"))
    assert data["schema"] == "magicite/composition-v1-corpus/1"
    return list(data["cases"])


@pytest.mark.parametrize("case", _load_structural_corpus(), ids=lambda c: c["id"])
def test_structural_corpus_cases(case: dict) -> None:
    """ATLAS nit: every structural-corpus case runs through compose()."""
    hv = _hv()
    snap, ctx, trusts = _corpus_world(hv, case["id"])
    plan = compose(
        case["selected"],
        ctx,
        snap,
        server_policy=hv.DEFAULT_POLICY,
        trust_view=lambda eid: trusts[eid],
    )

    expect_status = case.get("expect_status", "valid")
    assert plan.status == expect_status
    if expect_status == "invalid":
        assert plan.executable is False
        if case.get("expect_empty_order"):
            assert plan.topological_order == ()
        for code in case.get("expect_codes", []):
            assert code in {d.code for d in plan.diagnostics}
    else:
        assert plan.executable is True
        accepted = [tuple(order) for order in case.get("accepted_orders", [])]
        assert accepted, f"corpus case {case['id']} missing accepted_orders"
        assert tuple(plan.topological_order) in accepted
