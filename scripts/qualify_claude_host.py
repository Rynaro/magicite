#!/usr/bin/env python3
"""Actual Claude host read-workflow witness; run in the authenticated host terminal."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from capture_mcp_stdio import BODY_FIELDS, QUERY, READ_TOOLS, Observer, fingerprint, identity, public_id

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "mcp__magicite_fixture__"
ALLOWED = {PREFIX + name for name in READ_TOOLS}


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    paths = list((ROOT / "src").rglob("*.py"))
    paths += [
        ROOT / name
        for name in (
            "scripts/qualify_claude_host.py",
            "scripts/capture_mcp_stdio.py",
            "tests/unit/test_real_host_qualification.py",
            "tests/support/serve_with_fixture_custody.py",
            "tests/support/custody_adapter.py",
            "tests/fixtures/toy-registry/engrams/steam-runtime-repair.egr.md",
            ".spectra/plans/v1-real-host-qualification.acceptance.md",
        )
    ]
    return {str(path.relative_to(ROOT)): file_digest(path) for path in sorted(paths) if path.exists()}


def expected_tools() -> list[dict[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from magicite.mcp.app import build_mcp_tools

    tools = [tool.model_dump(mode="json", by_alias=True) for tool in build_mcp_tools()]
    return [
        {
            "name": row["name"],
            "input_schema_sha256": fingerprint(row.get("inputSchema")),
            "output_schema_sha256": fingerprint(row.get("outputSchema")),
        }
        for row in tools
    ]


def configuration_paths() -> dict[str, Path]:
    return {
        "user-config": Path.home() / ".claude.json",
        "user-settings": Path.home() / ".claude/settings.json",
        "user-local-settings": Path.home() / ".claude/settings.local.json",
        "project-mcp": ROOT / ".mcp.json",
    }


def configuration_state() -> dict[str, Any]:
    # Stat only: never open credential/configuration contents or keychain items.
    state = {}
    for role, path in configuration_paths().items():
        try:
            info = path.stat()
            state[role] = [info.st_ino, info.st_size, info.st_mtime_ns]
        except FileNotFoundError:
            state[role] = None
    return state


CANARY_CHECKS = (
    "read_allowed",
    "ordinary_write_denied",
    "atomic_replace_denied",
    "child_write_denied",
    "unrelated_write_allowed",
    "symlink_write_denied",
    "symlink_replace_denied",
)


def denial_profile(paths: list[Path]) -> str:
    # Cover both the user-visible link and its canonical target; never widen to a parent directory.
    targets = sorted(
        {str(path.absolute()) for path in paths}
        | {str(path.resolve()) for path in paths}
        | {str(path.parent.resolve() / path.name) for path in paths}
    )
    return (
        "(version 1)\n(allow default)\n"
        + "\n".join("(deny file-write* (literal " + json.dumps(target) + "))" for target in targets)
        + "\n"
    )


class GuardFailure(ValueError):
    def __init__(self, reason: str, details: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def guard_canary(work: Path) -> dict:
    owned = Path(tempfile.mkdtemp(prefix="write-guard-canary-", dir=work))
    target = owned / "protected.json"
    target.write_text('{"fixture":true}\n')
    original = target.read_bytes()
    profile = owned / "deny.sb"
    alias = owned / "alias.json"
    alias.symlink_to(target)
    profile.write_text(denial_profile([target, alias]))
    code = r"""from pathlib import Path
import json,sys,subprocess
p=Path(sys.argv[1]); rows={}; rows['read_allowed']=p.read_text()=='{"fixture":true}\n'
try: p.write_text('changed'); rows['ordinary_write_denied']=False
except PermissionError: rows['ordinary_write_denied']=True
replacement=p.parent/'replacement.tmp'; replacement.write_text('replacement')
try: replacement.replace(p); rows['atomic_replace_denied']=False
except PermissionError: rows['atomic_replace_denied']=True
other=p.parent/'unrelated.tmp'; other.write_text('allowed')
rows['unrelated_write_allowed']=other.read_text()=='allowed'
child_code='from pathlib import Path; import sys; Path(sys.argv[1]).write_text("child")'
child=subprocess.run([sys.executable,'-c',child_code,str(p)],capture_output=True,text=True,timeout=5)
rows['child_write_denied']=child.returncode!=0 and 'PermissionError' in child.stderr
alias=p.parent/'alias.json'
try: alias.write_text('changed'); rows['symlink_write_denied']=False
except PermissionError: rows['symlink_write_denied']=True
try: replacement.replace(alias); rows['symlink_replace_denied']=False
except PermissionError: rows['symlink_replace_denied']=True
print(json.dumps(rows))
"""
    failure_details = {}
    try:
        result = subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable, "-c", code, str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode:
            raise GuardFailure(
                "canary-process-nonzero",
                {"exit_code": result.returncode, "stderr_diagnostic": safe_error(result.stderr)},
            )
        try:
            parsed = json.loads(result.stdout)
            rows = {key: parsed.get(key) if type(parsed.get(key)) is bool else None for key in CANARY_CHECKS}
        except (ValueError, AttributeError):
            raise GuardFailure("canary-result-invalid") from None
        unchanged = target.read_bytes() == original
        if any(rows[key] is not True for key in CANARY_CHECKS) or not unchanged:
            raise GuardFailure(
                "canary-check-failed",
                {"exit_code": result.returncode, "checks": rows, "bytes_unchanged": unchanged},
            )
        rows["bytes_unchanged"] = True
    except GuardFailure as exc:
        failure_details = exc.details
        raise
    except subprocess.TimeoutExpired:
        failure_details = {"timeout_seconds": 10}
        raise GuardFailure("canary-timeout", failure_details) from None
    except OSError as exc:
        failure_details = {"error_class": type(exc).__name__}
        raise GuardFailure("canary-launch-error", failure_details) from None
    finally:
        shutil.rmtree(owned)
        failure_details["owned_cleanup"] = not owned.exists()
    rows["owned_cleanup"] = not owned.exists()
    return rows


def guard_binary_digest() -> str:
    return file_digest(Path("/usr/bin/sandbox-exec"))


def prepare_guard(work: Path) -> tuple[list[str], dict]:
    if sys.platform != "darwin":
        raise GuardFailure("unsupported-platform")
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise GuardFailure("native-guard-missing")
    if "CLAUDE_CONFIG_DIR" in os.environ:
        raise GuardFailure("alternate-configuration-namespace")
    proof = guard_canary(work)
    profile = work / "config-write-denial.sb"
    profile.write_text(denial_profile(list(configuration_paths().values())))
    return ["/usr/bin/sandbox-exec", "-f", str(profile)], {
        "status": "enforced-launch",
        "profile_sha256": file_digest(profile),
        "binary_sha256": guard_binary_digest(),
        "protected_roles": sorted(configuration_paths()),
        "canary": proof,
        "scope": (
            "deny writes to exact monitored configuration paths; "
            "reads, network and IPC not restricted by this profile"
        ),
    }


def save_evidence(output: Path, work: Path, report: dict, wire: list, host: list) -> int:
    for name, value in (("wire.json", wire), ("host-events.json", host), ("report.json", report)):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")
    (output / "artifacts.json").write_text(
        json.dumps(
            {name: file_digest(output / name) for name in ("wire.json", "host-events.json", "report.json")},
            indent=2,
        )
        + "\n"
    )
    shutil.rmtree(work)
    print(
        json.dumps(
            {
                "status": report["status"],
                "source_commit": report["source_commit"],
                "host_exit": report.get("host_exit"),
            }
        )
    )
    return int(report["status"] != "PASS")


def safe_error(value: Any) -> dict:
    # Interpret only fixed codes/reasons; never retain free-form error text.
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    text = json.dumps(value).lower()
    reasons = []
    for reason, words in (
        ("oauth-expired", ("oauth", "expired")),
        ("oauth-revoked", ("oauth", "revoked")),
        ("oauth-refresh-failed", ("refresh", "fail")),
        ("unauthorized", ("unauthorized",)),
        ("not-authenticated", ("not logged in",)),
        ("api-key-rejected", ("api key", "invalid")),
        ("api-key-helper", ("apikeyhelper",)),
        ("provider-configuration", ("provider", "config")),
        ("rate-limited", ("rate limit",)),
        ("network-error", ("connection", "error")),
        ("configuration-write-denied", ("operation not permitted",)),
        ("permission-denied", ("permission denied",)),
    ):
        if all(word in text for word in words):
            reasons.append(reason)
    match = re.search(r"(?:api error|http|status(?:_code| code)?)\s*[\":= ]+\s*(401|403|429|5\d\d)\b", text)
    types = [
        name
        for name in (
            "authentication_error",
            "permission_error",
            "rate_limit_error",
            "api_error",
            "overloaded_error",
            "invalid_request_error",
        )
        if name in text
    ]
    auth = any(word in text for word in ("auth", "logged", "login", "api key"))
    return {
        "type": "failure",
        "category": "authentication-request-failed" if auth else "host-run-error",
        "reason_signals": reasons,
        "http_status": int(match.group(1)) if match else None,
        "api_error_types": types,
        "error_payload_present": bool(value),
    }


def auth_preflight(binary: Path, work: Path, project: str, guard: list[str] | None = None) -> dict:
    # Invoked only by the user-run host command; no model requests or credential exports.
    modes = {
        "normal": [],
        "restricted-explicit-settings": [
            "--restricted",
            "--strict-mcp-config",
            "--mcp-config",
            str(work / "mcp.json"),
            "--settings",
            str(work / "settings.json"),
        ],
    }
    rows = {}
    for role, flags in modes.items():
        try:
            result = subprocess.run(
                [*(guard or []), str(binary), *flags, "auth", "status", "--json"],
                cwd=project,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
            value = json.loads(result.stdout)
            rows[role] = {
                "exit": result.returncode,
                "logged_in": value.get("loggedIn") if type(value.get("loggedIn")) is bool else None,
                "method": value.get("authMethod")
                if value.get("authMethod") in {"claude.ai", "oauth", "api_key", "apiKey", "none"}
                else "unexposed-or-other",
            }
        except (subprocess.TimeoutExpired, ValueError, OSError, AttributeError):
            rows[role] = {"status": "unavailable"}
    rows["comparison"] = (
        "restricted-auth-differs"
        if rows["normal"].get("logged_in") is True
        and rows["restricted-explicit-settings"].get("logged_in") is False
        else "no-established-isolation-auth-loss"
    )
    return rows


def safe_host_event(value: dict, observer: Observer) -> list[dict]:
    rows = []
    for block in value.get("message", {}).get("content", []):
        if block.get("type") == "tool_use":
            name = block.get("name", "")
            rows.append(
                {
                    "type": "tool_use",
                    "id": public_id(block.get("id")),
                    "name": name if name in ALLOWED else "[UNSCOPED_TOOL]",
                    "arguments": observer.arguments(name.removeprefix(PREFIX), block.get("input", {}))
                    if name in ALLOWED
                    else {"scope_violation": True},
                }
            )
        elif block.get("type") == "tool_result":
            rows.append(
                {
                    "type": "tool_result",
                    "tool_use_id": public_id(block.get("tool_use_id")),
                    "is_error": block.get("is_error", False) is not False,
                }
            )
    if value.get("type") == "result":
        rows.append(
            {
                "type": "result",
                "subtype": value.get("subtype")
                if value.get("subtype")
                in {
                    "success",
                    "error_during_execution",
                    "error_max_turns",
                    "error_max_budget_usd",
                    "error_max_structured_output_retries",
                }
                else "[OTHER_RESULT]",
                "is_error": value.get("is_error") is not False,
                "num_turns": value.get("num_turns") if type(value.get("num_turns")) is int else None,
            }
        )
        if value.get("is_error"):
            rows.append(
                safe_error({key: value[key] for key in ("result", "errors", "error") if key in value})
            )
    return rows


def validate(report: dict, wire: list[dict], host: list[dict], candidate: str) -> dict:
    if report.get("source_commit") != candidate or report.get("source_dirty"):
        raise ValueError("exact clean source binding required")
    if report.get("source_hashes") != source_hashes():
        raise ValueError("source artifact digest mismatch")
    if report.get("host_exit") != 0 or report.get("timed_out") or not report.get("config_preserved"):
        raise ValueError("host run, timeout or config preservation failed")
    if report.get("host", {}).get("name") != "claude-code" or not report["host"].get("version"):
        raise ValueError("actual host identity missing")
    if report.get("sdk") != importlib.metadata.version("mcp") or not report.get("host", {}).get(
        "binary_sha256"
    ):
        raise ValueError("server SDK or actual host binary identity missing")
    guard = report.get("write_guard", {})
    proof = guard.get("canary", {})
    if (
        guard.get("status") != "enforced-launch"
        or guard.get("protected_roles") != sorted(configuration_paths())
        or any(proof.get(key) is not True for key in (*CANARY_CHECKS, "bytes_unchanged", "owned_cleanup"))
    ):
        raise ValueError("validated monitored-path write guard required")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", str(guard.get(key, "")))
        for key in ("profile_sha256", "binary_sha256")
    ):
        raise ValueError("write guard profile/binary binding missing")
    if report.get("configuration_before") != report.get("configuration_after") or set(
        report.get("configuration_before", {})
    ) != set(configuration_paths()):
        raise ValueError("strict monitored configuration metadata preservation required")
    expected_profile = hashlib.sha256(
        denial_profile(list(configuration_paths().values())).encode()
    ).hexdigest()
    if guard["profile_sha256"] != expected_profile or guard["binary_sha256"] != guard_binary_digest():
        raise ValueError("write guard does not match actual monitored profile/binary")
    argv = report.get("argv", [])
    if argv[:3] != ["<WRITE_GUARD>", "-f", "<DISPOSABLE>/config-write-denial.sb"]:
        raise ValueError("guarded CLI invocation required")
    for flag in (
        "--restricted",
        "--strict-mcp-config",
        "--settings",
        "--mcp-config",
        "--no-session-persistence",
        "--permission-prompts",
        "--tools",
    ):
        if flag not in argv:
            raise ValueError("isolated host invocation missing")
    for flag, expected in (
        ("--permission-prompts", "none"),
        ("--tools", ""),
        ("--output-format", "stream-json"),
        ("--allowedTools", ",".join(sorted(ALLOWED))),
    ):
        if flag not in argv or argv.index(flag) + 1 >= len(argv) or argv[argv.index(flag) + 1] != expected:
            raise ValueError("host permission/tool/output isolation differs")
    for flag, expected in (
        ("--mcp-config", "<DISPOSABLE>/mcp.json"),
        ("--settings", "<DISPOSABLE>/settings.json"),
    ):
        if (
            argv.count(flag) != 1
            or argv.index(flag) + 1 >= len(argv)
            or argv[argv.index(flag) + 1] != expected
        ):
            raise ValueError("explicit isolated configuration binding missing")
    hashes = report.get("explicit_config_hashes", {})
    if set(hashes) != {"mcp.json", "settings.json"} or any(
        not re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in hashes.values()
    ):
        raise ValueError("explicit configuration hashes missing")
    if "--bare" in argv or "--dangerously-skip-permissions" in argv:
        raise ValueError("incompatible host invocation")
    if any(row.get("type") == "result" and row.get("is_error") for row in host):
        raise ValueError("host final result reported error")
    requests = {}
    pairs = []
    for row in wire:
        key = identity(row.get("id"))
        if (
            row.get("capture_error")
            or row.get("scope_violation")
            or row.get("params", {}).get("arguments", {}).get("scope_violation")
        ):
            raise ValueError("unscoped host call")
        if row["direction"] == "request" and row.get("id") is not None:
            if key in requests:
                raise ValueError("duplicate RPC request ID")
            requests[key] = row
        elif row["direction"] == "response":
            request = requests.get(key)
            if not request or request["method"] != row["method"] or row.get("rpc_error"):
                raise ValueError("RPC request/result identity mismatch")
            if any(identity(previous[1]["id"]) == key for previous in pairs):
                raise ValueError("duplicate RPC result ID")
            pairs.append((request, row))
    if len(pairs) != len(requests):
        raise ValueError("unpaired RPC request")
    inventory = next(
        ((request, response) for request, response in pairs if request["method"] == "tools/list"), None
    )
    if report.get("expected_tools") != expected_tools():
        raise ValueError("inventory does not match candidate server schemas")
    if inventory is None or inventory[1].get("result", {}).get("tools") != report.get("expected_tools"):
        raise ValueError("exact sixteen-tool inventory/schema evidence missing")
    if len(report["expected_tools"]) != 16 or len({row["name"] for row in report["expected_tools"]}) != 16:
        raise ValueError("sixteen unique tools required")
    init = next(
        ((request, response) for request, response in pairs if request["method"] == "initialize"), None
    )
    if init:
        info = init[0]["params"]["clientInfo"]
        version = init[1]["result"]["protocolVersion"]
        from mcp_types.version import SUPPORTED_PROTOCOL_VERSIONS

        if (
            version not in SUPPORTED_PROTOCOL_VERSIONS
            or version == "2026-07-28"
            or init[1]["result"].get("serverInfo", {}).get("name") != "magicite"
        ):
            raise ValueError("supported legacy negotiation/server identity missing")
        protocol = {"mode": "legacy-negotiated", "version": version}
    else:
        info = inventory[0].get("adoption", {}).get("clientInfo", {})
        version = inventory[0].get("adoption", {}).get("protocolVersion")
        if version != "2026-07-28" or inventory[1].get("serverInfo", {}).get("name") != "magicite":
            raise ValueError("modern version adoption evidence missing")
        protocol = {"mode": "modern-adopted", "version": version}
    if info.get("name") != "claude-code" or info.get("version") != report["host"]["version"]:
        raise ValueError("wire host identity mismatch")
    calls = [(request, response) for request, response in pairs if request["method"] == "tools/call"]
    uses = [row for row in host if row.get("type") == "tool_use"]
    results = {row.get("tool_use_id"): row for row in host if row.get("type") == "tool_result"}
    if len({row.get("id") for row in uses}) != len(uses) or len(results) != len(
        [row for row in host if row.get("type") == "tool_result"]
    ):
        raise ValueError("duplicate host tool-use/result IDs")
    if len(uses) != len(calls) or len(results) != len(calls):
        raise ValueError("unmatched host or wire tool event")
    if any(not row.get("id") for row in uses):
        raise ValueError("host tool identity missing")
    if any(row.get("name") not in ALLOWED for row in uses):
        raise ValueError("host stream used an unscoped tool")
    joined = {}
    for request, response in calls:
        args = request["params"]["arguments"]
        name = request["params"]["name"]
        matches = [row for row in uses if row["name"] == PREFIX + name and row["arguments"] == args]
        if len(matches) != 1 or matches[0].get("id") not in results:
            raise ValueError("host tool-use/result and wire causality missing")
        joined[identity(request["id"])] = matches[0]
        if results[matches[0]["id"]].get("is_error") or response.get("result", {}).get("isError"):
            raise ValueError("tool call reported error")
    route = next(
        ((request, response) for request, response in calls if request["params"]["name"] == "route"), None
    )
    if route is None or route[0]["params"]["arguments"].get("query") != QUERY:
        raise ValueError("actual fixture route missing")
    value = route[1]["result"]["value"]
    if value.get("status") != "selected" or value.get("selected_ids") != [report["fixture"]["id"]]:
        raise ValueError("fixture was not selected")
    selected = value["selected_ids"][0]
    digest = value["selected_content_digests"][selected]
    if digest != report["fixture"]["content_digest"]:
        raise ValueError("route digest mismatch")
    bodies = [
        (request, response) for request, response in calls if request["params"]["name"] == "load_skill_body"
    ]
    good = next(
        (
            (request, response)
            for request, response in bodies
            if request["params"]["arguments"].get("expected_content_digest") == digest
        ),
        None,
    )
    stale = next(
        (
            (request, response)
            for request, response in bodies
            if request["params"]["arguments"].get("expected_content_digest") == "0" * 64
        ),
        None,
    )
    if good is None or stale is None:
        raise ValueError("actual positive and stale body calls required")
    if not (wire.index(route[1]) < wire.index(good[0]) < wire.index(good[1]) < wire.index(stale[0])):
        raise ValueError("route/body/stale causal ordering missing")
    route_use = joined[identity(route[0]["id"])]
    good_use = joined[identity(good[0]["id"])]
    stale_use = joined[identity(stale[0]["id"])]
    if not (
        host.index(route_use)
        < host.index(results[route_use["id"]])
        < host.index(good_use)
        < host.index(results[good_use["id"]])
        < host.index(stale_use)
        < host.index(results[stale_use["id"]])
    ):
        raise ValueError("host route/body/stale event ordering missing")
    for request, _response in (good, stale):
        args = request["params"]["arguments"]
        if (
            args.get("name") not in {selected, report["fixture"]["name"]}
            or args.get("level", "L2") != "L2"
            or args.get("expected_policy_digest") != value["policy_digest"]
        ):
            raise ValueError("body request did not use routed identity/policy")
    body = good[1]["result"]["value"]
    if (
        body.get("status") != "ok"
        or body.get("name") != report["fixture"]["name"]
        or body.get("level") != "L2"
        or not report["fixture"]["body"]["procedure"]
    ):
        raise ValueError("successful fixture body missing")
    if any(body.get(key) != report["fixture"]["body"].get(key) for key in BODY_FIELDS):
        raise ValueError("returned fixture body differs")
    refused = stale[1]["result"]["value"]
    if (
        refused.get("status") != "stale_decision"
        or refused.get("name") != report["fixture"]["name"]
        or refused.get("level") != "L2"
        or any(refused.get(key) for key in BODY_FIELDS)
    ):
        raise ValueError("stale digest did not refuse all body fields")
    return {
        "protocol": protocol,
        "actual_calls": len(calls),
        "join": "unique tool name+arguments; exact IDs within each stream",
    }


def prepare(work: Path) -> tuple[dict, dict]:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from tests.support.custody_adapter import SERVE_LAUNCHER, FixtureCustody, attach_fixture

    from magicite.config import Config
    from magicite.core import registry, writer_guard
    from magicite.core.trust_journal import TrustJournal
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.storage import db

    project = work / "project"
    project.mkdir()
    target = project / ".magicite/engrams"
    target.mkdir(parents=True)
    source = ROOT / "tests/fixtures/toy-registry/engrams/steam-runtime-repair.egr.md"
    shutil.copyfile(source, target / source.name)
    custody = work / "custody"
    registry_id = "host-fixture"
    FixtureCustody(custody, registry_id).close()
    with attach_fixture(project, custody, registry_id):
        cfg = Config.load(project, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        cfg.ensure_dirs()
        provider = writer_guard.resolve_custody(cfg)[1]
        TrustJournal(cfg.data_dir / "trust/authority", registry_id, provider).initialize_reviewed_genesis()
        conn = db.connect(cfg.db_path)
        try:
            entry = registry.register(cfg, conn, get_embedder(256), path=".magicite/engrams").registered[0]
            digest = conn.execute("SELECT content_sha256 FROM engram WHERE id=?", (entry.id,)).fetchone()[0]
            registry.review_approve(
                cfg, conn, engram_id=entry.id, expected_digest=digest, actor="host-fixture-review"
            )
        finally:
            conn.close()
    text = source.read_text()
    procedure = text.split("## Procedure\n", 1)[1].split("## Pitfalls", 1)[0].strip()
    pitfalls = text.split("## Pitfalls\n", 1)[1].split("## Examples", 1)[0].strip()
    # Count annotations are registry metadata; L2 exposes only each pitfall text.
    pitfalls = re.sub(r"(?m)^- \(×\d+\) ", "- ", pitfalls)
    fixture = {
        "id": entry.id,
        "name": entry.name,
        "content_digest": digest,
        "body": {"procedure": procedure, "pitfalls": pitfalls, "examples": None, "provenance": None},
    }
    expected = expected_tools()
    fixture["tool_names"] = [row["name"] for row in expected]
    state = {
        "fixture": fixture,
        "wire": str(work / "wire.jsonl"),
        "server_env": {
            "MAGICITE_EMBEDDING_PROVIDER": "hashing",
            "MAGICITE_EMBEDDING_OFFLINE": "1",
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
        },
        "server_argv": [
            sys.executable,
            str(SERVE_LAUNCHER),
            str(project),
            str(custody),
            registry_id,
            "serve",
            "--project-root",
            str(project),
        ],
    }
    return state, {"fixture": fixture, "expected_tools": expected, "project": str(project)}


def run(output: Path, allow_dirty: bool = False) -> int:
    output.mkdir(parents=True, exist_ok=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True))
    if dirty and not allow_dirty:
        raise ValueError("clean candidate required; commit reviewed source first")
    binary = Path(shutil.which("claude") or "").resolve()
    work = Path(tempfile.mkdtemp(prefix="magicite-host-"))
    before = configuration_state()
    report = {
        "schema": "magicite/real-host-qualification/1",
        "status": "UNEVALUATED",
        "source_commit": head,
        "source_dirty": dirty,
        "source_hashes": source_hashes(),
        "host": {
            "name": "claude-code",
            "version": None,
            "binary_sha256": file_digest(binary),
            "sdk": "unavailable-not-exposed",
        },
        "sdk": importlib.metadata.version("mcp"),
        "python": sys.version,
        "platform": platform.platform(),
        "guard_capabilities": {
            "platform_supported": sys.platform == "darwin",
            "native_guard_exists": Path("/usr/bin/sandbox-exec").is_file(),
            "alternate_namespace_present": "CLAUDE_CONFIG_DIR" in os.environ,
        },
        "execution_context": {
            "codex_network_sandbox_marker_present": os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1"
        },
        "scope": (
            "agent-driven synthetic fixture; simulated custody; "
            "not operator/security/distribution/full protocol qualification"
        ),
    }
    state, expected = prepare(work)
    report.update({k: v for k, v in expected.items() if k != "project"})
    (work / "state.json").write_text(json.dumps(state))
    config = {
        "mcpServers": {
            "magicite_fixture": {
                "command": sys.executable,
                "args": [str(ROOT / "scripts/capture_mcp_stdio.py"), str(work / "state.json")],
                "env": state["server_env"],
            }
        }
    }
    (work / "mcp.json").write_text(json.dumps(config))
    (work / "settings.json").write_text("{}")
    prompt = (
        'Use only magicite_fixture MCP tools. First call introspect with {}. Then route with query "'
        + QUERY
        + '" and k=1. Read selected_ids[0], its selected_content_digests and policy_digest '
        "from that response. Call load_skill_body L2 with that name and the exact "
        "expected_content_digest and expected_policy_digest. Then call it again "
        "with the same name/policy digest but expected_content_digest of 64 zeros. No other tools."
    )
    argv = [
        str(binary),
        "--restricted",
        "--strict-mcp-config",
        "--mcp-config",
        str(work / "mcp.json"),
        "--settings",
        str(work / "settings.json"),
        "--no-session-persistence",
        "--permission-prompts",
        "none",
        "--tools",
        "",
        "--disable-slash-commands",
        "--allowedTools",
        ",".join(sorted(ALLOWED)),
        "--verbose",
        "--output-format",
        "stream-json",
        "--print",
        prompt,
    ]
    phase = "write-guard-setup"
    try:
        guard, report["write_guard"] = prepare_guard(work)
        phase = "guarded-host-version"
        version_output = subprocess.check_output(
            [*guard, str(binary), "--version"], stderr=subprocess.PIPE, text=True
        ).strip()
        report["host"]["version"] = version_output.split()[0]
    except (ValueError, OSError, subprocess.SubprocessError, IndexError) as exc:
        report["failed_stage"] = phase
        report["guard_failure_reason"] = (
            exc.reason if isinstance(exc, GuardFailure) else "unclassified-guard-or-version-failure"
        )
        report["guard_failure_details"] = exc.details if isinstance(exc, GuardFailure) else {}
        report["preflight_error"] = {"class": type(exc).__name__, **safe_error(getattr(exc, "stderr", None))}
        report.update(
            host_exit=None,
            qualification_failure="native configuration write guard or guarded host version unavailable",
            write_guard={"status": "unavailable"},
        )
        return save_evidence(output, work, report, [], [])
    argv = guard + argv
    report["argv"] = [
        arg.replace(str(work), "<DISPOSABLE>")
        .replace(str(binary), "<CLAUDE_BINARY>")
        .replace("/usr/bin/sandbox-exec", "<WRITE_GUARD>")
        for arg in argv
    ]
    report["explicit_config_hashes"] = {
        name: file_digest(work / name) for name in ("mcp.json", "settings.json")
    }
    report["auth_preflight"] = auth_preflight(binary, work, expected["project"], guard)
    report["invocation_auth_context"] = {
        "resolved_executable_not_shell_alias": True,
        "restricted_ignores_user_project_local_settings": True,
        "explicit_settings_empty": True,
        "environment_override_presence": {
            key: key in os.environ
            for key in (
                "ANTHROPIC_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_CONFIG_DIR",
            )
        },
    }
    start = time.monotonic()
    report["started_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    process = subprocess.Popen(
        argv,
        cwd=expected["project"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stderr_observations = []

    def read_diagnostics():
        for line in process.stderr:
            observation = safe_error(line)
            if observation["reason_signals"] or observation["http_status"] or observation["api_error_types"]:
                if observation not in stderr_observations:
                    stderr_observations.append(observation)

    diagnostic_thread = threading.Thread(target=read_diagnostics, daemon=True)
    diagnostic_thread.start()
    expired = []

    def timeout():
        expired.append(True)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    timer = threading.Timer(120, timeout)
    timer.start()
    host = []
    observer = Observer(state["fixture"])
    try:
        for line in process.stdout:
            try:
                host.extend(safe_host_event(json.loads(line), observer))
            except ValueError:
                continue
        process.wait(timeout=10)
    finally:
        timer.cancel()
        if process.poll() is None:
            timeout()
            process.wait(timeout=10)
    diagnostic_thread.join(timeout=2)
    report["stderr_diagnostics"] = stderr_observations
    wire = (
        [json.loads(line) for line in Path(state["wire"]).read_text().splitlines()]
        if Path(state["wire"]).exists()
        else []
    )
    after = configuration_state()
    report.update(
        host_exit=process.returncode,
        timed_out=bool(expired),
        config_preserved=before == after,
        configuration_before=before,
        configuration_after=after,
        seconds=round(time.monotonic() - start, 3),
    )
    report["config_preservation_scope"] = (
        "file inode/size/mtime only; credential contents and keychain never read"
    )
    try:
        report["observations"] = validate(report, wire, host, head)
        report["status"] = "PASS"
    except (ValueError, KeyError) as exc:
        report["qualification_failure"] = str(exc)
    return save_evidence(output, work, report, wire, host)


def verify_artifacts(output: Path) -> None:
    index = json.loads((output / "artifacts.json").read_text())
    if set(index) != {"wire.json", "host-events.json", "report.json"}:
        raise ValueError("exact transcript/report artifact coverage required")
    if any(file_digest(output / name) != sha for name, sha in index.items()):
        raise ValueError("observed artifact digest mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--allow-dirty-rehearsal", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify_artifacts(args.output)
        report = json.loads((args.output / "report.json").read_text())
        if report.get("status") != "PASS":
            raise ValueError("completed passing actual-host report required")
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        observations = validate(
            report,
            json.loads((args.output / "wire.json").read_text()),
            json.loads((args.output / "host-events.json").read_text()),
            candidate,
        )
        print(json.dumps(observations))
        return 0
    return run(args.output, args.allow_dirty_rehearsal)


if __name__ == "__main__":
    raise SystemExit(main())
