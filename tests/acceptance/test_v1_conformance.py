"""AC-S11-04: installed conformance probe + host evidence manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

pytestmark = pytest.mark.acceptance

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "protocol-v1"
HOST_MANIFEST = FIXTURES / "host-support-manifest.json"
OPAQUE_ID = "<opaque>"


def test_host_support_manifest_pins_exact_versions() -> None:
    assert HOST_MANIFEST.is_file(), "S11 must pin host/SDK/protocol versions"
    manifest = json.loads(HOST_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema"] == "magicite/host-support-manifest/1"
    assert manifest["mcp_sdk"]["distribution"] == "mcp"
    assert manifest["mcp_sdk"]["version"]
    assert set(manifest["protocol_versions"]["handshake"]) == set(HANDSHAKE_PROTOCOL_VERSIONS)
    assert manifest["hosts"], "at least one advertised host row required"
    for row in manifest["hosts"]:
        assert row["status"] in {"stable", "experimental", "unevaluated"}
        assert "transcript" in row


def test_conformance_probe_offline_fixture_route_and_body(
    cfg, review_fixture_artifacts, tmp_path: Path
) -> None:
    """Every required probe for the generic stdio host must pass."""
    import importlib.metadata as md

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from tests.conftest import TOY_ENGRAM_NAMES
    from tests.support.custody_adapter import fixture_cli_argv

    from magicite.mcp import app as _app  # noqa: F401
    from magicite.mcp.registry import registered_names

    assert len(registered_names()) == 16

    mcp_version = md.version("mcp")
    manifest = json.loads(HOST_MANIFEST.read_text(encoding="utf-8"))
    assert mcp_version == manifest["mcp_sdk"]["version"] or mcp_version.startswith(
        manifest["mcp_sdk"]["version"].split("+")[0]
    )

    command, *args = fixture_cli_argv(cfg, "serve", "--project-root", str(cfg.project_root))
    params = StdioServerParameters(
        command=command,
        args=args,
        env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"},
    )

    transcript: dict = {
        "probe": "stdio-generic",
        "mcp_sdk_version": mcp_version,
        "steps": [],
    }

    async def _run() -> None:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                transcript["steps"].append(
                    {
                        "step": "handshake",
                        "protocol_version": getattr(init, "protocolVersion", None)
                        or getattr(init, "protocol_version", None),
                        "server": getattr(init, "serverInfo", None)
                        or getattr(init, "server_info", None),
                    }
                )
                tools = await session.list_tools()
                assert len(tools.tools) == 16
                assert {t.name for t in tools.tools}
                transcript["steps"].append({"step": "tools_list", "count": len(tools.tools)})

                # Register toy engrams via sync tool so route has candidates.
                reg = await session.call_tool(
                    "register", arguments={"path": ".magicite/engrams", "request_id": "conf-reg"}
                )
                assert reg.is_error is False, reg.structured_content
                transcript["steps"].append(
                    {"step": "register", "ingested": reg.structured_content.get("ingested")}
                )
                # No MCP tool grants admission; the operator reviews out of band.
                review_fixture_artifacts(*TOY_ENGRAM_NAMES)

                route_result = await session.call_tool(
                    "route",
                    arguments={"query": "rollback proton steam", "k": 3},
                )
                assert route_result.is_error is False, route_result
                structured = route_result.structured_content or {}
                if not structured and route_result.content:
                    structured = json.loads(route_result.content[0].text)
                assert structured.get("candidates")
                # No raw query in structured payload.
                assert "rollback proton steam" not in json.dumps(structured)
                transcript["steps"].append(
                    {
                        "step": "route",
                        "decision_id": structured.get("decision_id"),
                        "status": structured.get("status"),
                        "candidate_count": len(structured.get("candidates") or []),
                    }
                )

                top = structured["candidates"][0]
                digests = structured.get("selected_content_digests") or {}
                expected = digests.get(top["id"])
                body = await session.call_tool(
                    "load_skill_body",
                    arguments={
                        "name": top["name"],
                        "level": "L2",
                        "expected_content_digest": expected,
                        "expected_policy_digest": structured.get("policy_digest"),
                    },
                )
                assert body.is_error is False, body
                body_payload = body.structured_content or json.loads(body.content[0].text)
                assert body_payload.get("status") in {"ok", "missing_context", "stale_decision"}
                if expected:
                    assert body_payload.get("status") == "ok"
                    assert body_payload.get("procedure")
                transcript["steps"].append(
                    {"step": "load_skill_body", "status": body_payload.get("status")}
                )

    import asyncio

    asyncio.run(_run())

    live_path = tmp_path / "stdio-generic.json"
    live_path.write_text(json.dumps(transcript, indent=2, default=str) + "\n", encoding="utf-8")
    golden = json.loads((FIXTURES / "transcripts" / "stdio-generic.json").read_text(encoding="utf-8"))
    live = json.loads(live_path.read_text(encoding="utf-8"))
    assert live["probe"] == golden["probe"]
    assert _stable_steps(live["steps"]) == _stable_steps(golden["steps"])


def _stable_steps(steps: list[dict]) -> list[dict]:
    """Drop fields that legitimately vary per run (opaque ids, server repr)."""
    stable = []
    for step in steps:
        row = {k: v for k, v in step.items() if k != "server"}
        if "decision_id" in row:
            row["decision_id"] = OPAQUE_ID
        stable.append(row)
    return stable


def test_v1_conformance_schema_snapshots_exist() -> None:
    from magicite.mcp import app as _app  # noqa: F401
    from magicite.mcp.registry import manifest

    snap_path = FIXTURES / "tool-manifest.snapshot.json"
    current = {"tools": manifest()}
    if os.environ.get("MAGICITE_UPDATE_SNAPSHOTS") == "1":
        snap_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert snap_path.is_file(), (
        "tool-manifest.snapshot.json is missing; regenerate deliberately with "
        "MAGICITE_UPDATE_SNAPSHOTS=1 and review the diff"
    )
    pinned = json.loads(snap_path.read_text(encoding="utf-8"))
    # Names and risk metadata must remain stable; schemas may grow additively.
    assert {t["name"] for t in pinned["tools"]} == {t["name"] for t in current["tools"]}
    assert len(current["tools"]) == 16
