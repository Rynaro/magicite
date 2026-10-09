"""Synthetic authenticated test custody only; never empirical E2/E3 qualification."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from click.testing import CliRunner

from magicite.__main__ import cli
from magicite.core import calibration as cal
from magicite.core import calibration_admission as ca
from magicite.core import policy_store as ps
from magicite.core import router, trust
from magicite.core import routing_policy as rp
from magicite.errors import InvalidInputError
from tests.unit.core.test_router_policy import _baseline_registry


@pytest.fixture
def synthetic_bundle(cfg, db_conn, embedder):
    cfg.embedding_dim = embedder.dim
    cfg.allowed_tools = ["Read"]
    _baseline_registry(cfg, db_conn, embedder)
    artifact = cal.fit_abstention(
        [
            cal.CalibrationExample("synthetic-positive", 0.9, 0.4, True),
            cal.CalibrationExample("synthetic-negative", 0.1, 0.01, False),
        ],
        cfg=cfg,
        policy_id="dense-v1",
        policy_digest=rp.compute_policy_digest("dense-v1", cfg),
        config_digest=rp.compute_config_digest(cfg),
        rejection_queries=(),
    ).to_dict()
    subject = ca.runtime_subject(cfg, db_conn, embedder, cal.CalibrationArtifact.from_dict(artifact))
    source = {"source_commit": "synthetic-test-only-source", "candidate_digest": "a" * 64}
    evidence = {
        "schema": ca.SCHEMA,
        "status": "PASS",
        "qualifying": True,
        "classification": "qualified_calibration",
        "gates": {"E2": "PASS", "E3": "PASS"},
        "subject": subject,
        "source": source,
    }
    for phase in ("fit", "final"):
        report = {
            "schema": ca.REPORT_SCHEMA,
            "phase": phase,
            "status": "PASS",
            "qualifying": True,
            "classification": "qualified_calibration",
            "gates": evidence["gates"],
            "subject": copy.deepcopy(subject),
            "source": source,
            "input_digest": phase + "-synthetic-only-input",
            "fit_digest": source["candidate_digest"] if phase == "fit" else evidence["fit"]["sha256"],
        }
        evidence[phase] = {"report": report, "sha256": ca.canonical_digest(report)}
    return artifact, evidence


def admit(cfg, bundle):
    artifact, evidence = bundle
    return ps.admit_calibration(
        cfg,
        artifact,
        evidence,
        reviewed_sha256=ca.canonical_digest(ca.validate(artifact, evidence)),
        actor="synthetic-test-custody-operator",
    )


def activate(cfg, bundle):
    record = admit(cfg, bundle)
    approval = ps.approve(cfg, record.digest, actor="synthetic-test-custody-reviewer")
    ps.activate(cfg, expected_current=None, candidate_digest=record.digest, approval_id=approval)
    return record


def test_synthetic_authenticated_runtime_and_ceiling(cfg, db_conn, embedder, synthetic_bundle):
    record = activate(cfg, synthetic_bundle)
    artifact = ps.active_calibration(cfg, db_conn, embedder)
    assert artifact.policy_digest == rp.compute_policy_digest("dense-v1", cfg)
    assert record.digest == rp.compute_policy_digest("dense-v1", cfg, calibration_digest=artifact.digest)
    assert record.digest != artifact.policy_digest
    assert router._resolve_active_policy(cfg)[1] == record.digest
    from magicite.mcp.bind_retrieval import _active_policy_digest

    assert _active_policy_digest(cfg, db_conn, embedder) == record.digest
    outcome = router.route(cfg, db_conn, embedder, query="orchid")
    assert outcome.decision.calibration_digest == artifact.digest
    assert outcome.decision.confidence.value is None
    assert outcome.decision.policy_digest == record.digest


def test_admission_never_activates_and_exact_review(cfg, synthetic_bundle):
    with pytest.raises(InvalidInputError):
        ps.admit_calibration(cfg, *synthetic_bundle, reviewed_sha256="wrong", actor="synthetic-test")
    assert ps.status(cfg).active_digest is None
    record = admit(cfg, synthetic_bundle)
    assert record.state == "evaluated"
    assert ps.status(cfg).active_digest is None


@pytest.mark.parametrize("phase", ["fit", "final"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "magicite/frozen-calibration-result/1"),
        ("status", "UNEVALUATED"),
        ("qualifying", False),
        ("gates", {"E2": "PASS", "E3": "UNEVALUATED"}),
    ],
)
def test_forged_outer_pass_refuses_underlying(cfg, synthetic_bundle, phase, field, value):
    artifact, evidence = copy.deepcopy(synthetic_bundle)
    evidence[phase]["report"][field] = value
    evidence[phase]["sha256"] = ca.canonical_digest(evidence[phase]["report"])
    with pytest.raises(InvalidInputError):
        admit(cfg, (artifact, evidence))
    assert ps.status(cfg).active_digest is None


@pytest.mark.parametrize("field", sorted(ca.SUBJECT_FIELDS))
def test_subject_anchor_mutation_denied(cfg, synthetic_bundle, field):
    artifact, evidence = copy.deepcopy(synthetic_bundle)
    evidence["subject"][field] += "-changed"
    with pytest.raises(InvalidInputError):
        admit(cfg, (artifact, evidence))


@pytest.mark.parametrize(
    "field,value",
    [
        ("split", "test"),
        ("rule_id", "other"),
        ("score_threshold", float("nan")),
        ("margin_threshold", "0.1"),
        ("n_examples", True),
        ("n_examples", -1),
        ("score_threshold", 10**1000),
        ("digest", "self-hash"),
        ("rejection_query_fingerprints", [1]),
        ("data_provenance", {}),
    ],
)
def test_strict_artifact_parser(synthetic_bundle, field, value):
    artifact = copy.deepcopy(synthetic_bundle[0])
    artifact[field] = value
    with pytest.raises(InvalidInputError):
        cal.CalibrationArtifact.from_dict(artifact)


@pytest.mark.parametrize(
    "field,value", [("n_non_relevant", -1), ("n_non_relevant", True), ("example_fingerprints", [1, 2])]
)
def test_strict_provenance(synthetic_bundle, field, value):
    artifact = copy.deepcopy(synthetic_bundle[0])
    artifact["data_provenance"][field] = value
    artifact["digest"] = cal.compute_artifact_digest(artifact)
    with pytest.raises(InvalidInputError):
        cal.CalibrationArtifact.from_dict(artifact)


@pytest.mark.parametrize("status", ["pass", "fail", "inconclusive", "unevaluated"])
def test_generic_dense_calibration_bypass(cfg, synthetic_bundle, status):
    artifact, evidence = synthetic_bundle
    manifest = ps.PolicyManifest(
        "dense-v1",
        "x",
        "stable",
        artifact["config_digest"],
        artifact["digest"],
        evidence["subject"]["generation"],
        evidence["subject"]["snapshot"],
        "cosine",
    )
    with pytest.raises(InvalidInputError):
        ps.register_evaluated(cfg, manifest, evaluation_status=status, evidence="PASS")
    assert not ps._activation_allowed(replace(manifest, evaluation_status="fail"))


@pytest.mark.parametrize(
    "drift", ["config", "ceiling", "model", "registry", "custody", "retirement", "control", "store"]
)
def test_current_authority_after_warm_call(cfg, db_conn, embedder, synthetic_bundle, drift):
    record = activate(cfg, synthetic_bundle)
    router.route(cfg, db_conn, embedder, query="orchid")
    if drift == "config":
        cfg.abstention_score_threshold = 0.8
    elif drift == "ceiling":
        cfg.allowed_tools = ["Read", "Write"]
    elif drift == "model":
        embedder.model_name += "-changed"
    elif drift == "registry":
        db_conn.execute("UPDATE engram SET content_sha256='changed' WHERE name='orchid'")
    elif drift == "custody":
        policy = trust.load_policy(cfg)
        trust.save_policy(cfg, replace(policy, revision=policy.revision + 1))
    elif drift == "retirement":
        from magicite.storage.lease import writer_lease

        with writer_lease("synthetic-retirement"):
            state = ps._load_raw(cfg)
            state["records"][record.digest]["state"] = "retired"
            ps._save_raw(cfg, state)
    elif drift == "control":
        state = ps._load_raw(cfg)
        (cfg.approvals_dir / (state["calibration_control"]["id"] + ".json")).unlink()
    else:
        ps.policy_store_path(cfg).write_text("{}")
    with pytest.raises(InvalidInputError):
        ps.active_calibration(cfg, db_conn, embedder)


def test_unsigned_active_file_cannot_authorize(cfg, db_conn, embedder, synthetic_bundle):
    artifact = cal.CalibrationArtifact.from_dict(synthetic_bundle[0])
    cal.save_calibration(cfg, artifact)
    assert ps.active_calibration(cfg, db_conn, embedder) is None
    outcome = router.route(cfg, db_conn, embedder, query="orchid")
    assert outcome.decision.calibration_digest is None
    assert outcome.decision.confidence.value is None
    activate(cfg, synthetic_bundle)
    cal.calibration_path(cfg).write_text("attacker-replaced-at-same-path")
    assert ps.active_calibration(cfg, db_conn, embedder).digest == artifact.digest


def test_cli_read_only_review_and_exact_admission(cfg, synthetic_bundle, tmp_path, monkeypatch):
    from magicite.mcp import bind_ops

    monkeypatch.setattr(bind_ops, "_cfg", lambda root: cfg)
    artifact_path = tmp_path / "artifact.json"
    evidence_path = tmp_path / "evidence.json"
    artifact_path.write_text(json.dumps(synthetic_bundle[0]))
    evidence_path.write_text(json.dumps(synthetic_bundle[1]))
    args = [
        "policy",
        "admit-calibration",
        "--artifact",
        str(artifact_path),
        "--evidence",
        str(evidence_path),
        "--actor",
        "synthetic-test-custody-operator",
    ]
    review = CliRunner().invoke(cli, args)
    assert review.exit_code == 0, review.output
    summary = json.loads(review.output)
    assert summary["artifact_digest"] == synthetic_bundle[0]["digest"]
    assert ps.status(cfg).records == ()
    rejected = CliRunner().invoke(cli, args + ["--reviewed-sha256", "no"])
    assert rejected.exit_code != 0
    result = CliRunner().invoke(cli, args + ["--reviewed-sha256", summary["bundle_digest"]])
    assert result.exit_code == 0, result.output
    assert ps.status(cfg).active_digest is None


def probability_bundle(bundle, *, budget=None):
    artifact, evidence = copy.deepcopy(bundle)
    model, _ = cal.fit_probability(
        [-0.1, 0.1, 0.9], [False, False, True], calibration_input_digest="f" * 64, rank_depth=5
    )
    fitted = cal.with_probability(
        cal.CalibrationArtifact.from_dict(artifact), model, evaluation_budget_digest=budget
    )
    evidence["subject"]["artifact_digest"] = fitted.digest
    for phase in ("fit", "final"):
        evidence[phase]["report"]["subject"] = copy.deepcopy(evidence["subject"])
        if phase == "final":
            evidence[phase]["report"]["fit_digest"] = evidence["fit"]["sha256"]
        evidence[phase]["sha256"] = ca.canonical_digest(evidence[phase]["report"])
    return fitted.to_dict(), evidence


def test_synthetic_v2_protected_probability_and_depth(cfg, db_conn, embedder, synthetic_bundle):
    bundle = probability_bundle(synthetic_bundle)
    activate(cfg, bundle)
    result = router.route(cfg, db_conn, embedder, query="orchid", k=5)
    assert result.decision.status == "selected"
    fitted = cal.CalibrationArtifact.from_dict(bundle[0])
    margin = cal.score_margin([c.score for c in result.decision.raw_candidates])
    assert result.decision.confidence.value == cal.probability_at_margin(fitted.probability_model, margin)
    different = router.route(cfg, db_conn, embedder, query="orchid", k=1)
    assert different.decision.confidence.value is None
    assert "probability_rank_depth_mismatch" in different.decision.reason_codes


def test_budget_v2_refuses_even_consistent_synthetic_ordinary_subject(cfg, synthetic_bundle):
    bundle = probability_bundle(synthetic_bundle, budget="d" * 64)
    with pytest.raises(InvalidInputError, match="evaluation-budget"):
        ca.validate(*bundle)
    assert ps.status(cfg).active_digest is None


def test_composition_error_clears_protected_probability(
    cfg, db_conn, embedder, synthetic_bundle, monkeypatch
):
    activate(cfg, probability_bundle(synthetic_bundle))

    # Use the real composition-result shape, preserving actual route selection.
    from dataclasses import replace

    original = router.compose_route_plan

    def invalid(*args, **kwargs):
        value = original(*args, **kwargs)
        return replace(value, ok=False, reason_codes=("composition_invalid",))

    monkeypatch.setattr(router, "compose_route_plan", invalid)
    result = router.route(cfg, db_conn, embedder, query="orchid", k=5)
    assert result.decision.status != "selected"
    assert result.decision.confidence.value is None
