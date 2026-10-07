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


@pytest.fixture(autouse=True)
def portable_synthetic_guard_identity(monkeypatch):
    # Synthetic validator records are not native guard evidence; Linux has no binary.
    if not Path("/usr/bin/sandbox-exec").exists():
        monkeypatch.setattr(qualification, "guard_binary_digest", lambda: "f" * 64)


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
        "write_guard": {
            "status": "enforced-launch",
            "profile_sha256": qualification.hashlib.sha256(
                qualification.denial_profile(list(qualification.configuration_paths().values())).encode()
            ).hexdigest(),
            "binary_sha256": qualification.guard_binary_digest(),
            "protected_roles": sorted(qualification.configuration_paths()),
            "canary": {
                key: True for key in (*qualification.CANARY_CHECKS, "bytes_unchanged", "owned_cleanup")
            },
        },
        "configuration_before": {key: None for key in qualification.configuration_paths()},
        "configuration_after": {key: None for key in qualification.configuration_paths()},
        "argv": [
            "<WRITE_GUARD>",
            "-f",
            "<DISPOSABLE>/config-write-denial.sb",
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
            {"status": "ok", "name": FIXTURE["name"], "level": "L2", **FIXTURE["body"]},
        ),
        (
            "load_skill_body",
            {
                "name": FIXTURE["id"],
                "level": "L2",
                "expected_content_digest": "0" * 64,
                "expected_policy_digest": POLICY,
            },
            {
                "status": "stale_decision",
                "name": FIXTURE["name"],
                "level": "L2",
                "procedure": "",
                "pitfalls": "",
            },
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


@pytest.mark.parametrize(
    "message,reason,status",
    [
        (
            'API Error: 401 {"type":"authentication_error",'
            '"message":"OAuth token expired credential-canary"}',
            "oauth-expired",
            401,
        ),
        ("HTTP 403 unauthorized credential-canary", "unauthorized", 403),
        ("apiKeyHelper failed credential-canary", "api-key-helper", None),
    ],
)
def test_actionable_error_diagnostic_never_archives_free_form_message(message, reason, status):
    observed = qualification.safe_error(message)
    assert reason in observed["reason_signals"]
    assert observed["http_status"] == status
    assert "credential-canary" not in json.dumps(observed)


def test_host_auth_preflight_retains_only_boolean_method_and_difference(tmp_path, monkeypatch):
    import subprocess

    responses = iter([True, False])
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "loggedIn": next(responses),
                    "authMethod": "oauth",
                    "email": "credential-canary",
                    "token": "credential-canary",
                }
            ),
        )

    monkeypatch.setattr(qualification.subprocess, "run", fake_run)
    observed = qualification.auth_preflight(Path("/claude"), tmp_path, str(tmp_path))
    assert observed["comparison"] == "restricted-auth-differs"
    assert "credential-canary" not in json.dumps(observed)
    assert all(argv[-3:] == ["auth", "status", "--json"] for argv in calls)
    assert "--restricted" not in calls[0] and "--restricted" in calls[1]


@pytest.mark.parametrize(
    "mutation", ["known-alias", "foreign-alias", "returned-identity", "alias-wrong-digest"]
)
def test_body_alias_remains_bound_to_routed_fixture(mutation):
    report, wire, host = records()
    qualification.validate(report, wire, host, SHA)
    for index in (4, 6):
        wire[index]["params"]["arguments"]["name"] = FIXTURE["name"]
    for index in (2, 4):
        host[index]["arguments"]["name"] = FIXTURE["name"]
    if mutation == "foreign-alias":
        wire[4]["params"]["arguments"]["name"] = "other-fixture"
        host[2]["arguments"]["name"] = "other-fixture"
    elif mutation == "returned-identity":
        wire[5]["result"]["value"]["name"] = "other-fixture"
    elif mutation == "alias-wrong-digest":
        wire[4]["params"]["arguments"]["expected_content_digest"] = "e" * 64
        host[2]["arguments"]["expected_content_digest"] = "e" * 64
    if mutation == "known-alias":
        assert qualification.validate(report, wire, host, SHA)["actual_calls"] == 3
    else:
        with pytest.raises(ValueError):
            qualification.validate(report, wire, host, SHA)


@pytest.mark.parametrize(
    "mutation", ["no-guard", "canary-failed", "missing-role", "metadata-changed", "unwrapped-command"]
)
def test_guard_and_config_preservation_cannot_be_waived(mutation):
    report, wire, host = records()
    qualification.validate(report, wire, host, SHA)
    if mutation == "no-guard":
        report.pop("write_guard")
    elif mutation == "canary-failed":
        report["write_guard"]["canary"]["atomic_replace_denied"] = False
    elif mutation == "missing-role":
        report["write_guard"]["protected_roles"].pop()
    elif mutation == "metadata-changed":
        report["configuration_after"]["user-config"] = [1, 2, 3]
    else:
        report["argv"] = report["argv"][3:]
    with pytest.raises(ValueError):
        qualification.validate(report, wire, host, SHA)


def test_denial_profile_covers_alias_and_resolved_target_without_directory_widening(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("canary")
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    profile = qualification.denial_profile([alias])
    assert json.dumps(str(alias)) in profile and json.dumps(str(target)) in profile
    assert "subpath" not in profile
    assert "(allow default)" in profile


def test_guard_rejects_alternate_auth_namespace_without_reading_it(tmp_path, monkeypatch):
    from types import SimpleNamespace

    # Isolate the namespace gate from earlier native-platform prerequisites.
    original_is_file = Path.is_file
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: True if path == Path("/usr/bin/sandbox-exec") else original_is_file(path),
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "untouched"))
    with pytest.raises(ValueError, match="alternate-configuration-namespace"):
        qualification.prepare_guard(tmp_path)
    assert not (tmp_path / "untouched").exists()


@pytest.mark.skipif(
    sys.platform != "darwin", reason="native macOS write guard canary; not Linux qualification"
)
def test_native_write_guard_disposable_canary(tmp_path):
    observed = qualification.guard_canary(tmp_path)
    assert all(observed[key] for key in (*qualification.CANARY_CHECKS, "bytes_unchanged", "owned_cleanup"))
    assert not list(tmp_path.iterdir())


def test_auth_preflight_uses_same_write_guard_for_both_launches(tmp_path, monkeypatch):
    import subprocess

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"loggedIn":true,"authMethod":"claude.ai"}')

    monkeypatch.setattr(qualification.subprocess, "run", fake_run)
    prefix = ["/usr/bin/sandbox-exec", "-f", str(tmp_path / "deny.sb")]
    qualification.auth_preflight(Path("/claude"), tmp_path, str(tmp_path), prefix)
    assert len(calls) == 2 and all(argv[:3] == prefix for argv in calls)


def test_guard_unavailable_records_failure_without_host_launch_and_cleans_owned_fixture(
    tmp_path, monkeypatch
):
    def git_only(argv, **kwargs):
        if argv[0] == "git":
            return "a" * 40 + "\n" if "rev-parse" in argv else ""
        raise AssertionError("host launch must not occur before working guard")

    monkeypatch.setattr(qualification.platform, "platform", lambda: "synthetic-platform")
    monkeypatch.setattr(qualification.subprocess, "check_output", git_only)
    monkeypatch.setattr(qualification.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(
        qualification, "prepare_guard", lambda _work: (_ for _ in ()).throw(ValueError("unavailable"))
    )
    assert qualification.run(tmp_path / "output") == 1
    report = json.loads((tmp_path / "output/report.json").read_text())
    assert report["status"] == "UNEVALUATED" and report["host_exit"] is None
    assert report["failed_stage"] == "write-guard-setup"
    assert json.loads((tmp_path / "output/wire.json").read_text()) == []


@pytest.mark.parametrize("field", ["profile_sha256", "binary_sha256"])
def test_write_guard_integrity_must_match_actual_profile_and_binary(field):
    report, wire, host = records()
    qualification.validate(report, wire, host, SHA)
    report["write_guard"][field] = "0" * 64
    with pytest.raises(ValueError, match="actual monitored profile/binary"):
        qualification.validate(report, wire, host, SHA)


def test_parent_alias_and_leaf_symlink_have_all_exact_denial_forms(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    parent_alias = tmp_path / "parent-alias"
    parent_alias.symlink_to(real, target_is_directory=True)
    target = real / "target.json"
    target.write_text("canary")
    leaf = real / "leaf.json"
    leaf.symlink_to(target)
    protected = parent_alias / "leaf.json"
    profile = qualification.denial_profile([protected])
    assert all(json.dumps(str(path)) in profile for path in (protected, target, leaf))
    assert "subpath" not in profile


@pytest.mark.skipif(sys.platform != "darwin", reason="native default macOS TMPDIR canary")
def test_native_guard_handles_actual_default_temporary_directory(tmp_path):
    import shutil
    import tempfile

    owned = Path(tempfile.mkdtemp(prefix="guard-default-regression-"))
    try:
        observed = qualification.guard_canary(owned)
        assert all(
            observed[key] for key in (*qualification.CANARY_CHECKS, "bytes_unchanged", "owned_cleanup")
        )
    finally:
        shutil.rmtree(owned)


def test_failed_canary_records_measured_false_check_without_raw_stderr(tmp_path, monkeypatch):
    import subprocess

    checks = {key: True for key in qualification.CANARY_CHECKS}
    checks["symlink_replace_denied"] = False
    monkeypatch.setattr(
        qualification.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(checks)),
    )
    with pytest.raises(qualification.GuardFailure) as captured:
        qualification.guard_canary(tmp_path)
    assert captured.value.reason == "canary-check-failed"
    assert captured.value.details["checks"] == checks
    assert captured.value.details["owned_cleanup"] is True
    assert not list(tmp_path.iterdir())


def test_canary_launch_failure_has_fixed_reason_and_no_raw_error(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(
        qualification.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 71, "", "sandbox_apply: Operation not permitted credential-canary"
        ),
    )
    with pytest.raises(qualification.GuardFailure) as captured:
        qualification.guard_canary(tmp_path)
    assert captured.value.reason == "canary-process-nonzero"
    assert captured.value.details["exit_code"] == 71
    assert "credential-canary" not in json.dumps(captured.value.details)
    assert captured.value.details["owned_cleanup"] is True


@pytest.mark.parametrize(
    "platform,native_exists,reason",
    [
        ("linux", True, "unsupported-platform"),
        ("darwin", False, "native-guard-missing"),
    ],
)
def test_guard_native_prerequisites_fail_before_canary(
    tmp_path, monkeypatch, platform, native_exists, reason
):
    from types import SimpleNamespace

    original_is_file = Path.is_file
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: native_exists if path == Path("/usr/bin/sandbox-exec") else original_is_file(path),
    )
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(
        qualification,
        "guard_canary",
        lambda _work: pytest.fail("canary must not run without native prerequisites"),
    )
    with pytest.raises(qualification.GuardFailure) as captured:
        qualification.prepare_guard(tmp_path)
    assert captured.value.reason == reason
    assert not list(tmp_path.iterdir())
