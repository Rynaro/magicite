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
    assert out["message"] == "failed under <redacted-path>"
    assert out["details"]["nested"]["path"] == "<redacted-path>"


def test_error_result_redacts_raw_query() -> None:
    err = InvalidInputError(
        "bad route",
        details={"query": "rollback proton for my secret project", "reason": "x"},
    )
    envelope = _error_result(err).structured_content
    assert envelope["details"]["query"] == "<redacted>"
    assert "secret project" not in json_dumps(envelope)
