"""Public RouteContext/1 facts and operator permission ceiling through route/body."""

from __future__ import annotations

import hashlib
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from magicite.core import policy_store, registry, routing_policy
from magicite.engram import parser
from magicite.mcp import bind_retrieval
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import LoadSkillBodyInput, RouteInput


def _subject(cfg, conn, embedder):
    for path in cfg.registry_dir.glob("*.egr.md"):
        path.unlink()
    raw = (Path(__file__).parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md").read_text()
    front, body = parser.split_frontmatter(raw)
    doc = parser.load_frontmatter_doc(front)
    doc["relations"] = {"requires": [], "before": [], "supersedes": []}
    digest = hashlib.sha256(body.encode()).hexdigest()
    doc["routing"]["body_digest"] = digest
    doc["origin"]["content_hashes"]["body_sha256"] = digest
    stream = StringIO()
    YAML().dump(doc, stream)
    (cfg.registry_dir / "subject.egr.md").write_text("---\n" + stream.getvalue() + "---\n" + body)
    outcome = registry.register(cfg, conn, embedder, path=".magicite/engrams")
    assert not outcome.validation_errors
    return ToolContext(cfg=cfg, conn=conn, embedder=embedder)


def _request():
    return {
        "query": "prepare proton tooling",
        "context": {
            "platform": "linux",
            "languages": {"python": "3.12.0"},
            "host": {"id": "cursor", "version": "0.50.0"},
            "capabilities": {"host.fs.read-project": "1.0.0"},
            "artifact_inventory": [{"id": "artifact.wine-prefix", "version": "1.0.0"}],
            "allowed_tools": ["protontricks"],
            "permission_grants": [],
        },
    }


@pytest.mark.parametrize("stored", [False, True])
def test_typed_route_body_and_live_ceiling_revocation(cfg, db_conn, embedder, stored):
    cfg.allowed_tools = ("protontricks",)
    ctx = _subject(cfg, db_conn, embedder)
    if stored:
        manifest = policy_store.PolicyManifest(
            policy_id="dense-v1",
            policy_digest="a" * 64,
            policy_family="stable",
            config_digest=routing_policy.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="g",
            snapshot_id="s",
            selection="cosine_similarity",
        )
        policy_store.register_evaluated(cfg, manifest, evaluation_status="pass")
        approval = policy_store.approve(cfg, manifest.policy_digest, actor="fixture")
        policy_store.activate(
            cfg, expected_current=None, candidate_digest=manifest.policy_digest, approval_id=approval
        )
    request = RouteInput.model_validate(_request())
    out = bind_retrieval.route(ctx, request)
    assert out.status == "selected", out.reason_codes
    assert out.selected_ids == ["egr_a1b2c3d4"]
    assert out.policy_source == ("store" if stored else "config_fresh_install")
    if stored:
        assert out.policy_digest == routing_policy.bind_server_ceiling_digest("a" * 64, cfg)
    if out.plan is not None:
        assert out.plan.policy_digest == out.policy_digest
        assert out.plan.plan_digest == out.plan_digest
    body_args = dict(
        name="sample-host-tooling",
        context=request.context,
        expected_content_digest=out.selected_content_digests["egr_a1b2c3d4"],
        expected_policy_digest=out.policy_digest,
    )
    assert bind_retrieval.load_skill_body(ctx, LoadSkillBodyInput(**body_args)).status == "ok"
    no_context = {**body_args, "context": None}
    assert bind_retrieval.load_skill_body(ctx, LoadSkillBodyInput(**no_context)).status == "missing_context"
    cfg.allowed_tools = ()  # Operator revokes the ceiling; request cannot regrant it.
    blocked = bind_retrieval.route(ctx, request)
    assert blocked.status == "abstained"
    assert bind_retrieval.load_skill_body(ctx, LoadSkillBodyInput(**body_args)).status == "stale_decision"
    if stored:
        assert policy_store.status(cfg).active_digest == "a" * 64  # CAS identity is unchanged.


def test_missing_unknown_empty_facts_and_client_escalation(cfg, db_conn, embedder):
    ctx = _subject(cfg, db_conn, embedder)
    request = _request()
    assert bind_retrieval.route(ctx, RouteInput.model_validate(request)).status == "abstained"
    cfg.allowed_tools = ("protontricks",)
    for field, value in [
        ("languages", {}),
        ("platform", None),
        ("artifact_inventory", None),
        ("artifact_inventory", []),
        ("allowed_tools", []),
    ]:
        mutated = _request()
        mutated["context"][field] = value
        assert bind_retrieval.route(ctx, RouteInput.model_validate(mutated)).status == "abstained"


def test_operator_ceiling_toml_is_typed_and_pinned(tmp_path):
    from magicite.config import Config

    data = tmp_path / ".magicite"
    data.mkdir()
    config = data / "magicite.toml"
    config.write_text(
        '[routing]\nallowed_tools = ["protontricks", "protontricks"]\nallowed_permissions = ["fs.read"]\n'
    )
    loaded = Config.load(project_root=tmp_path)
    assert loaded.allowed_tools == ("protontricks",)
    assert loaded.allowed_permissions == ("fs.read",)
    assert routing_policy.compute_config_digest(loaded) != routing_policy.compute_config_digest(
        Config(project_root=tmp_path)
    )
    config.write_text('[routing]\nallowed_tools = "*"\n')
    with pytest.raises(ValueError):
        Config.load(project_root=tmp_path)
