"""Transparent fixture stdio relay; persist only explicitly scoped observations."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

READ_TOOLS = {"introspect", "route", "load_skill_body"}
QUERY = "steam will not open or crashes immediately on startup"
BODY_FIELDS = ("procedure", "pitfalls", "examples", "provenance")


def identity(value: Any) -> str:
    return type(value).__name__ + ":" + json.dumps(value, sort_keys=True)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_id(value: Any) -> Any:
    # Retain integer identity; project opaque strings by typed hash, never raw text.
    if value is None or type(value) is int:
        return value
    return "sha256:" + fingerprint({"type": type(value).__name__, "value": value})


def hex_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def public_info(info: dict) -> dict:
    return {
        "name": info.get("name") if info.get("name") in {"claude-code", "magicite"} else "[OTHER_CLIENT]",
        "version": info.get("version")
        if isinstance(info.get("version"), str) and re.fullmatch(r"\d+\.\d+\.\d+", info["version"])
        else "[OTHER_VERSION]",
    }


class Observer:
    def __init__(self, fixture: dict[str, Any]):
        self.fixture = fixture
        self.pending: dict[str, tuple[str, str | None]] = {}
        self.rows: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    @staticmethod
    def protocol(value):
        from mcp_types.version import SUPPORTED_PROTOCOL_VERSIONS

        return value if value in SUPPORTED_PROTOCOL_VERSIONS else "[UNSUPPORTED_PROTOCOL]"

    def arguments(self, name: str, arguments: dict) -> dict:
        allowed = {
            "introspect": {"include_health"},
            "route": {"query", "k"},
            "load_skill_body": {
                "name",
                "level",
                "expected_content_digest",
                "expected_policy_digest",
                "max_bytes",
                "cursor",
            },
        }
        result = {}
        for key, value in arguments.items():
            if key not in allowed.get(name, set()):
                result["scope_violation"] = True
                continue
            if key == "query" and value != QUERY:
                value = "[NON_FIXTURE_QUERY]"
            if key == "name" and value not in {self.fixture["id"], self.fixture["name"]}:
                value = "[NON_FIXTURE_NAME]"
            if key.startswith("expected_") and not hex_digest(value):
                value = "[INVALID_DIGEST]"
            if key == "cursor":
                value = public_id(value)
            if key == "level" and value not in {"L1", "L2", "L3"}:
                value = "[INVALID_LEVEL]"
            if key in {"k", "max_bytes"} and type(value) is not int:
                value = "[INVALID_INTEGER]"
            if key == "include_health" and type(value) is not bool:
                value = "[INVALID_BOOLEAN]"
            result[key] = value
        return result

    def observe(self, raw: bytes, direction: str) -> dict | None:
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        row = {
            "direction": direction,
            "id": public_id(value.get("id")),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
        with self.lock:
            if direction == "request":
                method = value.get("method")
                if method not in {"initialize", "tools/list", "tools/call", "notifications/initialized"}:
                    return None
                params = value.get("params", {})
                name = params.get("name")
                self.pending[identity(value.get("id"))] = (method, name)
                row["method"] = method
                if method == "initialize":
                    info = params.get("clientInfo", {})
                    row["params"] = {
                        "protocolVersion": self.protocol(params.get("protocolVersion")),
                        "clientInfo": public_info(info),
                    }
                elif method == "tools/call":
                    row["params"] = {
                        "name": name if name in READ_TOOLS else "[UNSCOPED_TOOL]",
                        "arguments": self.arguments(name, params.get("arguments", {})),
                    }
                    if name not in READ_TOOLS:
                        row["scope_violation"] = True
                meta = params.get("_meta", {})
                if "io.modelcontextprotocol/protocolVersion" in meta:
                    info = meta.get("io.modelcontextprotocol/clientInfo", {})
                    row["adoption"] = {
                        "protocolVersion": self.protocol(meta["io.modelcontextprotocol/protocolVersion"]),
                        "clientInfo": public_info(info),
                    }
            else:
                method, name = self.pending.get(identity(value.get("id")), (None, None))
                if method is None:
                    return None
                row["method"] = method
                result = value.get("result", {})
                if "error" in value:
                    row["rpc_error"] = True
                if method == "initialize":
                    row["result"] = {
                        "protocolVersion": self.protocol(result.get("protocolVersion")),
                        "serverInfo": public_info(result.get("serverInfo", {})),
                    }
                elif method == "tools/list":
                    row["result"] = {
                        "tools": [
                            {
                                "name": item["name"]
                                if item["name"] in self.fixture.get("tool_names", [])
                                else "[OTHER_TOOL]",
                                "input_schema_sha256": fingerprint(item.get("inputSchema")),
                                "output_schema_sha256": fingerprint(item.get("outputSchema")),
                            }
                            for item in result.get("tools", [])
                        ]
                    }
                elif method == "tools/call" and name in READ_TOOLS:
                    payload = result.get("structuredContent")
                    if payload is None:
                        try:
                            payload = json.loads(result["content"][0]["text"])
                        except (KeyError, ValueError, IndexError):
                            payload = {}
                    keys = {
                        "route": (
                            "status",
                            "selected_ids",
                            "selected_content_digests",
                            "policy_digest",
                            "decision_id",
                        ),
                        "load_skill_body": ("status", "name", "level", *BODY_FIELDS, "reason_codes"),
                        "introspect": (),
                    }[name]
                    safe = {key: payload[key] for key in keys if key in payload}
                    if "status" in safe and safe["status"] not in {"selected", "ok", "stale_decision"}:
                        safe["status"] = "[OTHER_STATUS]"
                    if "selected_ids" in safe:
                        safe["selected_ids"] = [
                            v if v == self.fixture["id"] else "[OTHER_ID]" for v in safe["selected_ids"]
                        ]
                    if "selected_content_digests" in safe:
                        safe["selected_content_digests"] = {
                            k if k == self.fixture["id"] else "[OTHER_ID]": v
                            if hex_digest(v)
                            else "[INVALID_DIGEST]"
                            for k, v in safe["selected_content_digests"].items()
                        }
                    if "policy_digest" in safe and not hex_digest(safe["policy_digest"]):
                        safe["policy_digest"] = "[INVALID_DIGEST]"
                    if "decision_id" in safe:
                        safe["decision_id"] = public_id(safe["decision_id"])
                    if "name" in safe and safe["name"] not in {self.fixture["id"], self.fixture["name"]}:
                        safe["name"] = "[OTHER_NAME]"
                    if "level" in safe and safe["level"] not in {"L1", "L2", "L3"}:
                        safe["level"] = "[OTHER_LEVEL]"
                    if "reason_codes" in safe:
                        safe["reason_codes"] = public_id(safe["reason_codes"])
                    for key in BODY_FIELDS:
                        if key in safe and safe[key] not in (None, "", self.fixture["body"].get(key)):
                            safe[key] = "[NON_FIXTURE_BODY]"
                    row["result"] = {"isError": result.get("isError", False) is not False, "value": safe}
                meta = result.get("_meta", {})
                if "io.modelcontextprotocol/serverInfo" in meta:
                    info = meta["io.modelcontextprotocol/serverInfo"]
                    row["serverInfo"] = public_info(info)
            self.rows.append(row)
        return row


def relay(source, target, observer: Observer, direction: str, log: Path | None = None) -> None:
    for raw in iter(source.readline, b""):
        try:
            row = observer.observe(raw, direction)
        except (ValueError, TypeError, KeyError, AttributeError):
            row = {
                "direction": direction,
                "capture_error": True,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
        if row is not None and log is not None:
            with observer.lock:
                with log.open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
        target.write(raw)
        target.flush()


def main() -> int:
    state = json.loads(Path(sys.argv[1]).read_text())
    observer = Observer(state["fixture"])
    process = subprocess.Popen(
        state["server_argv"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )

    def incoming():
        try:
            relay(sys.stdin.buffer, process.stdin, observer, "request", Path(state["wire"]))
        finally:
            process.stdin.close()

    thread = threading.Thread(target=incoming, daemon=True)
    thread.start()
    try:
        relay(process.stdout, sys.stdout.buffer, observer, "response", Path(state["wire"]))
    finally:
        process.terminate()
        process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
