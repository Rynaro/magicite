"""Capture transparency and evidence rejection; synthetic records are not host qualification."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import qualify_claude_host as qualification  # noqa: E402
from capture_mcp_stdio import QUERY, Observer, relay  # noqa: E402

SHA = "a" * 40
DIGEST = "b" * 64
POLICY = "c" * 64
FIXTURE = {
    "id": "egr_fixture",
    "name": "fixture",
    "content_digest": DIGEST,
    "body": {
        "procedure": "Synthetic procedure",
        "pitfalls": "Synthetic pitfall",
        "examples": None,
        "provenance": None,
    },
}


def records():
    report = {
        "source_commit": SHA,
        "source_dirty": False,
        "source_hashes": qualification.source_hashes(),
        "host_exit": 0,
        "timed_out": False,
        "config_preserved": True,
        "fixture": copy.deepcopy(FIXTURE),
        "host": {"name": "claude-code", "version": "2.1.288", "binary_sha256": "d" * 64},
        "sdk": qualification.importlib.metadata.version("mcp"),
        "argv": [
            "--restricted",
            "--strict-mcp-config",
            "--settings",
            "<DISPOSABLE>/settings.json",
            "--mcp-config",
            "<DISPOSABLE>/mcp.json",
            "--no-session-persistence",
            "--permission-prompts",
            "none",
            "--tools",
            "",
            "--output-format",
            "stream-json",
            "--allowedTools",
            ",".join(sorted(qualification.ALLOWED)),
        ],
        "expected_tools": qualification.expected_tools(),
        "explicit_config_hashes": {"mcp.json": "e" * 64, "settings.json": "f" * 64},
    }
    wire = [
        {
            "direction": "request",
            "id": 0,
            "method": "tools/list",
            "adoption": {
                "protocolVersion": "2026-07-28",
                "clientInfo": {"name": "claude-code", "version": "2.1.288"},
            },
        },
        {
            "direction": "response",
            "id": 0,
            "method": "tools/list",
            "serverInfo": {"name": "magicite"},
            "result": {"tools": copy.deepcopy(report["expected_tools"])},
        },
    ]
    host = []
    calls = [
        (
            "route",
            {"query": QUERY, "k": 1},
            {
                "status": "selected",
                "selected_ids": [FIXTURE["id"]],
                "selected_content_digests": {FIXTURE["id"]: DIGEST},
                "policy_digest": POLICY,
            },
        ),
        (
            "load_skill_body",
            {
                "name": FIXTURE["id"],
                "level": "L2",
                "expected_content_digest": DIGEST,
                "expected_policy_digest": POLICY,
            },
            {"status": "ok", **FIXTURE["body"]},
        ),
        (
            "load_skill_body",
            {
                "name": FIXTURE["id"],
                "level": "L2",
                "expected_content_digest": "0" * 64,
                "expected_policy_digest": POLICY,
            },
            {"status": "stale_decision", "procedure": "", "pitfalls": ""},
        ),
    ]
    for number, (name, arguments, result) in enumerate(calls, 1):
        wire += [
            {
                "direction": "request",
                "id": number,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            {
                "direction": "response",
                "id": number,
                "method": "tools/call",
                "result": {"value": result, "isError": False},
            },
        ]
        host += [
            {
                "type": "tool_use",
                "id": f"toolu_{number}",
                "name": qualification.PREFIX + name,
                "arguments": arguments,
            },
            {"type": "tool_result", "tool_use_id": f"toolu_{number}", "is_error": False},
        ]
    return report, wire, host


def test_transparent_relay_does_not_change_bytes_or_capture_private_unknown_fields():
    raw = b'{"id":0,"method":"tools/list","params":{"private":"credential-canary"}}\n'
    target = io.BytesIO()
    observer = Observer(FIXTURE)
    relay(io.BytesIO(raw), target, observer, "request")
    assert target.getvalue() == raw
    assert "credential-canary" not in json.dumps(observer.rows)


def test_unknown_body_is_redacted_but_protocol_forwarding_is_unchanged():
    observer = Observer(FIXTURE)
    observer.observe(
        json.dumps(
            {"id": 1, "method": "tools/call", "params": {"name": "load_skill_body", "arguments": {}}}
        ).encode(),
        "request",
    )
    raw = (
        json.dumps({"id": 1, "result": {"structuredContent": {"procedure": "credential-canary"}}}).encode()
        + b"\n"
    )
    target = io.BytesIO()
    relay(io.BytesIO(raw), target, observer, "response")
    assert target.getvalue() == raw
    assert "credential-canary" not in json.dumps(observer.rows)
    assert observer.rows[-1]["result"]["value"]["procedure"] == "[NON_FIXTURE_BODY]"


def test_valid_synthetic_evidence_baseline():
    report, wire, host = records()
    assert qualification.validate(report, wire, host, SHA)["protocol"]["mode"] == "modern-adopted"


@pytest.mark.parametrize(
    "mutation",
    [
        "candidate",
        "source",
        "host-version",
        "sdk",
        "inventory",
        "response-id",
        "adoption",
        "route-digest",
        "body-policy",
        "body-value",
        "stale-body",
        "missing-call",
        "missing-host-result",
    ],
)
def test_missing_or_mismatched_evidence_refuses_pass(mutation):
    report, wire, host = records()
    qualification.validate(report, wire, host, SHA)  # prove the unchanged baseline is meaningful
    if mutation == "candidate":
        report["source_commit"] = "0" * 40
    elif mutation == "source":
        report["source_hashes"] = {}
    elif mutation == "host-version":
        report["host"]["version"] = "wrong"
    elif mutation == "sdk":
        report["sdk"] = "wrong"
    elif mutation == "inventory":
        wire[1]["result"]["tools"].pop()
    elif mutation == "response-id":
        wire[1]["id"] = "0"
    elif mutation == "adoption":
        wire[0].pop("adoption")
    elif mutation == "route-digest":
        wire[3]["result"]["value"]["selected_content_digests"][FIXTURE["id"]] = "e" * 64
    elif mutation == "body-policy":
        wire[4]["params"]["arguments"]["expected_policy_digest"] = "e" * 64
    elif mutation == "body-value":
        wire[5]["result"]["value"]["procedure"] = "incorrect fixture"
    elif mutation == "stale-body":
        wire[7]["result"]["value"]["pitfalls"] = "leak"
    elif mutation == "missing-call":
        wire = wire[:-2]
    else:
        host.pop()
    with pytest.raises((ValueError, KeyError)):
        qualification.validate(report, wire, host, SHA)


def test_fixture_expected_body_matches_existing_real_read_handler(tmp_path):
    # Supporting fixture check only; never substitute for actual Claude execution.
    state, prepared = qualification.prepare(tmp_path)
    from magicite.config import Config
    from magicite.core import routing_policy
    from magicite.mcp.bind_retrieval import load_skill_body
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput
    from magicite.storage import db
    from tests.support.custody_adapter import attach_fixture

    _python, _launcher, project, custody, registry_id, *_rest = state["server_argv"]
    with attach_fixture(Path(project), Path(custody), registry_id):
        cfg = Config.load(Path(project), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        conn = db.connect(cfg.db_path)
        try:
            body = load_skill_body(
                ToolContext(cfg, conn, None),
                LoadSkillBodyInput(
                    name=prepared["fixture"]["id"],
                    expected_content_digest=prepared["fixture"]["content_digest"],
                    expected_policy_digest=routing_policy.compute_policy_digest(
                        routing_policy.resolve_policy_id(cfg), cfg
                    ),
                ),
            )
            assert body.status == "ok"
            assert {key: getattr(body, key) for key in qualification.BODY_FIELDS} == prepared["fixture"][
                "body"
            ]
        finally:
            conn.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "body-before-route",
        "unsupported-legacy",
        "wrong-server",
        "missing-mcp-config",
        "unpaired-request",
        "host-final-error",
        "config-hashes",
    ],
)
def test_independent_review_controls_refuse_false_pass(mutation):
    report, wire, host = records()
    qualification.validate(report, wire, host, SHA)
    if mutation == "body-before-route":
        wire = wire[:2] + wire[4:] + wire[2:4]
        host = host[2:] + host[:2]
    elif mutation in {"unsupported-legacy", "wrong-server"}:
        version = "garbage" if mutation == "unsupported-legacy" else "2025-11-25"
        wire[:0] = [
            {
                "direction": "request",
                "id": 99,
                "method": "initialize",
                "params": {"clientInfo": report["host"], "protocolVersion": version},
            },
            {
                "direction": "response",
                "id": 99,
                "method": "initialize",
                "result": {"protocolVersion": version, "serverInfo": {"name": "wrong"}},
            },
        ]
    elif mutation == "missing-mcp-config":
        index = report["argv"].index("--mcp-config")
        del report["argv"][index : index + 2]
    elif mutation == "unpaired-request":
        wire.append(
            {
                "direction": "request",
                "id": 55,
                "method": "tools/call",
                "params": {"name": "route", "arguments": {"query": QUERY, "k": 1}},
            }
        )
    elif mutation == "host-final-error":
        host.append({"type": "result", "is_error": True})
    else:
        report["explicit_config_hashes"] = {}
    with pytest.raises(ValueError):
        qualification.validate(report, wire, host, SHA)


def test_known_keys_cannot_archive_credential_canary():
    observer = Observer(FIXTURE)
    raw = (
        json.dumps(
            {
                "id": "credential-canary",
                "method": "tools/call",
                "params": {
                    "name": "load_skill_body",
                    "arguments": {
                        "cursor": "credential-canary",
                        "expected_content_digest": "credential-canary".ljust(64, "x"),
                    },
                },
            }
        ).encode()
        + b"\n"
    )
    forwarded = io.BytesIO()
    relay(io.BytesIO(raw), forwarded, observer, "request")
    assert forwarded.getvalue() == raw
    assert "credential-canary" not in json.dumps(observer.rows)
    assert observer.rows[0]["params"]["arguments"]["expected_content_digest"] == "[INVALID_DIGEST]"


@pytest.mark.parametrize("index", [{}, {"../outside": "a" * 64}, {"report.json": "a" * 64}])
def test_transcript_manifest_requires_exact_bounded_coverage(tmp_path, index):
    (tmp_path / "artifacts.json").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="coverage"):
        qualification.verify_artifacts(tmp_path)


def test_host_tool_stream_order_is_causal():
    report, wire, host = records()
    qualification.validate(report, wire, host, SHA)
    host = host[2:] + host[:2]
    with pytest.raises(ValueError, match="event ordering"):
        qualification.validate(report, wire, host, SHA)


def test_malformed_observation_cannot_truncate_forwarded_protocol():
    raw = b'{"id":0,"method":"tools/call","params":null}\n'
    forwarded = io.BytesIO()
    observer = Observer(FIXTURE)
    relay(io.BytesIO(raw), forwarded, observer, "request")
    assert forwarded.getvalue() == raw
    assert observer.rows == []


def test_unknown_wire_tool_name_is_redacted():
    observer = Observer(FIXTURE)
    observer.observe(
        json.dumps(
            {"id": 1, "method": "tools/call", "params": {"name": "credential-canary", "arguments": {}}}
        ).encode(),
        "request",
    )
    assert "credential-canary" not in json.dumps(observer.rows)
    assert observer.rows[0]["scope_violation"]


def test_fixture_subprocess_imports_exact_candidate_source(tmp_path):
    import os
    import subprocess

    state, prepared = qualification.prepare(tmp_path)
    env = {**os.environ, **state["server_env"]}
    actual = subprocess.check_output(
        [sys.executable, "-c", "import magicite; print(magicite.__file__)"],
        cwd=prepared["project"],
        env=env,
        text=True,
    ).strip()
    assert Path(actual).resolve() == ROOT / "src/magicite/__init__.py"
