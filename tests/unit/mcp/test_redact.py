"""MCP error envelope path/secret redaction."""

from __future__ import annotations

from magicite.errors import InvalidInputError
from magicite.mcp.app import _error_result
from magicite.mcp.redact import redact_absolute_paths, redact_error_payload


def test_redact_absolute_paths() -> None:
    raw = "marker at /Users/henrique/workspace/oss/magicite/.magicite/recovery/marker.json"
    scrubbed = redact_absolute_paths(raw)
    assert "/Users/" not in scrubbed
    assert "<redacted-path>" in scrubbed


def test_error_result_redacts_details_paths() -> None:
    err = InvalidInputError(
        "reconciliation required",
        details={
            "marker_path": "/Users/henrique/proj/.magicite/recovery/marker",
            "reason": "reconciliation_required",
            "custody_key": "super-secret-bytes",
        },
    )
    result = _error_result(err)
    assert result.is_error is True
    details = result.structured_content["details"]
    assert details["marker_path"] == "<redacted-path>"
    assert details["custody_key"] == "<redacted>"
    assert "/Users/" not in json_dumps(result.structured_content)


def json_dumps(obj: object) -> str:
    import json

    return json.dumps(obj)


def test_redact_error_payload_nested() -> None:
    payload = {
        "code": "invalid_input",
        "message": "failed under /tmp/magicite/db.sqlite",
        "details": {"nested": {"path": "/var/lib/magicite/x"}},
    }
    out = redact_error_payload(payload)
    assert "/tmp/" not in out["message"] or "<redacted-path>" in out["message"]
    assert out["details"]["nested"]["path"] == "<redacted-path>"
