"""AC-S11-01: load_skill_body returns stale_decision without the body."""

from __future__ import annotations

import hashlib
from pathlib import Path

from magicite.core import registry as registry_mod
from magicite.core import routing_policy as policy_mod
from magicite.mcp import bind_retrieval
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import LoadSkillBodyInput, RouteInput


def test_stale_body_denied(cfg, db_conn, embedder) -> None:
    """GIVEN a decision whose body changed
    WHEN load_skill_body runs with the prior content digest
    THEN the call SHALL return stale_decision without the body.
    """
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    routed = bind_retrieval.route(ctx, RouteInput(query="rollback proton for a steam game", k=3))
    assert routed.candidates
    name = routed.candidates[0].name
    engram_id = routed.candidates[0].id
    expected = routed.selected_content_digests.get(engram_id)
    if not expected:
        row0 = db_conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)).fetchone()
        expected = str(row0["content_sha256"])

    row = db_conn.execute("SELECT path FROM engram WHERE id = ?", (engram_id,)).fetchone()
    path = Path(cfg.project_root) / row["path"]
    path.write_text(path.read_text(encoding="utf-8") + "\n# stale-body-probe\n", encoding="utf-8")
    new_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    db_conn.execute("UPDATE engram SET content_sha256 = ? WHERE id = ?", (new_digest, engram_id))
    db_conn.commit()

    out = bind_retrieval.load_skill_body(
        ctx,
        LoadSkillBodyInput(
            name=name,
            level="L2",
            expected_content_digest=expected,
            expected_policy_digest=routed.policy_digest,
        ),
    )
    assert out.status == "stale_decision"
    assert out.procedure == ""
    assert out.pitfalls == ""
    assert out.examples is None
    assert "stale_decision" in out.reason_codes


def _route_top(cfg, db_conn, embedder):
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    routed = bind_retrieval.route(ctx, RouteInput(query="rollback proton for a steam game", k=3))
    top = routed.candidates[0]
    params = LoadSkillBodyInput(
        name=top.name,
        level="L2",
        expected_content_digest=routed.selected_content_digests[top.id],
        expected_policy_digest=routed.policy_digest,
    )
    return ctx, top, params


def test_policy_change_after_route_denied(cfg, db_conn, embedder) -> None:
    """GIVEN a decision whose routing policy changed after routing
    WHEN load_skill_body runs with the prior policy digest
    THEN the call SHALL return stale_decision without the body.
    """
    ctx, _top, params = _route_top(cfg, db_conn, embedder)
    cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    assert bind_retrieval._active_policy_digest(cfg) != params.expected_policy_digest

    out = bind_retrieval.load_skill_body(ctx, params)
    assert out.status == "stale_decision"
    assert "policy_digest_drift" in out.reason_codes
    assert out.procedure == ""
    assert out.pitfalls == ""


def test_admission_change_after_route_denied(cfg, db_conn, embedder) -> None:
    """GIVEN a decision whose trust admission changed after routing (quarantine)
    WHEN load_skill_body runs with the prior digests
    THEN the call SHALL return stale_decision without the body.
    """
    ctx, top, params = _route_top(cfg, db_conn, embedder)
    assert bind_retrieval.load_skill_body(ctx, params).status == "ok"
    db_conn.execute("UPDATE engram SET verification_status = 'quarantined' WHERE id = ?", (top.id,))
    db_conn.commit()

    out = bind_retrieval.load_skill_body(ctx, params)
    assert out.status == "stale_decision"
    assert "not_admitted" in out.reason_codes
    assert out.procedure == ""
    assert out.pitfalls == ""


def test_local_admission_withdrawn_after_route_denied(cfg, db_conn, embedder) -> None:
    ctx, _top, params = _route_top(cfg, db_conn, embedder)
    cfg.default_local_authorship_admission = False

    out = bind_retrieval.load_skill_body(ctx, params)
    assert out.status == "stale_decision"
    assert out.procedure == ""


def test_full_procedure_survives_dual_format_body_gate(cfg, db_conn, embedder) -> None:
    from magicite.engram import parser

    source = Path(__file__).parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"
    raw = source.read_text()
    yaml_text, _ = parser.split_frontmatter(raw)
    doc = parser.load_frontmatter_doc(yaml_text)
    doc["compatibility"] = {}
    doc["capabilities"] = {"requires": [], "produces": [], "alternatives": [], "conflicts_with": []}
    doc["relations"] = {"requires": [], "before": [], "supersedes": []}
    procedure = "1. Numbered instruction."
    body = "## Procedure\n" + procedure + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    doc["routing"]["body_digest"] = digest
    doc["origin"]["content_hashes"]["body_sha256"] = digest
    from io import StringIO

    from ruamel.yaml import YAML

    stream = StringIO()
    YAML().dump(doc, stream)
    path = Path(cfg.project_root) / ".magicite/engrams/sample-host-tooling.egr.md"
    path.write_text("---\n" + stream.getvalue() + "---\n" + body)
    registered = registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert not registered.validation_errors, registered.validation_errors
    # Model an admitted artifact with preserved procedure prose (also supported for imports).
    procedure = "Critical unnumbered instruction.\n" + procedure + "\nFinal unnumbered instruction."
    raw = path.read_text().replace("1. Numbered instruction.", procedure)
    path.write_text(raw)
    db_conn.execute(
        "UPDATE engram SET content_sha256 = ? WHERE name = ?",
        (hashlib.sha256(raw.encode()).hexdigest(), "sample-host-tooling"),
    )
    db_conn.commit()
    row = db_conn.execute("SELECT content_sha256 FROM engram WHERE name = 'sample-host-tooling'").fetchone()
    assert row is not None
    out = bind_retrieval.load_skill_body(
        ToolContext(cfg=cfg, conn=db_conn, embedder=embedder),
        LoadSkillBodyInput(
            name="sample-host-tooling",
            level="L2",
            expected_content_digest=row["content_sha256"],
            expected_policy_digest=bind_retrieval._active_policy_digest(cfg),
        ),
    )
    assert out.status == "ok"
    assert out.procedure == procedure


def test_durable_trust_revocation_blocks_previously_routed_body(cfg, db_conn, embedder) -> None:
    from magicite.core import trust

    ctx, top, params = _route_top(cfg, db_conn, embedder)
    trust.approve(
        cfg, db_conn, engram_id=top.id, expected_digest=params.expected_content_digest, actor="test-reviewer"
    )
    assert bind_retrieval.load_skill_body(ctx, params).status == "ok"
    trust.revoke(
        cfg, db_conn, engram_id=top.id, expected_digest=params.expected_content_digest, actor="test-reviewer"
    )
    out = bind_retrieval.load_skill_body(ctx, params)
    assert out.status == "stale_decision"
    assert out.procedure == ""


def test_cas_policy_activation_blocks_previously_routed_body(cfg, db_conn, embedder) -> None:
    from magicite.core import policy_store as store

    ctx, _top, params = _route_top(cfg, db_conn, embedder)
    assert bind_retrieval.load_skill_body(ctx, params).status == "ok"
    prior = None
    for policy_id, family, selection in (
        (policy_mod.POLICY_DENSE_V1, "stable", "cosine_similarity"),
        (policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1, "experimental", "adaptive_blend"),
    ):
        manifest = store.PolicyManifest(
            policy_id=policy_id,
            policy_digest=policy_mod.compute_policy_digest(policy_id, cfg),
            policy_family=family,
            config_digest=policy_mod.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="test-generation",
            snapshot_id="test-snapshot",
            selection=selection,
        )
        store.register_evaluated(cfg, manifest, evaluation_status="pass", evidence="unit fixture")
        approval = store.approve(cfg, manifest.policy_digest, actor="test-reviewer")
        store.activate(
            cfg, expected_current=prior, candidate_digest=manifest.policy_digest, approval_id=approval
        )
        prior = manifest.policy_digest
    out = bind_retrieval.load_skill_body(ctx, params)
    assert out.status == "stale_decision"
    assert "policy_digest_drift" in out.reason_codes
    assert out.procedure == ""
