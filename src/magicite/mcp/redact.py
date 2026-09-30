"""Path/secret redaction for MCP error envelopes (S11 / C6).

MCP clients must never see absolute filesystem paths, key material, or
raw query text in structured error ``details``. Privacy-sensitive keys are
owned by ``obs.events.privacy_sensitive_argument_keys`` (reuse, don't copy).
"""

from __future__ import annotations

import re
from typing import Any

from magicite.obs.events import privacy_sensitive_argument_keys

#: Absolute POSIX / Windows paths in free-form strings.
_ABS_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_])/(?:Users|home|var|tmp|private|opt|usr|etc|Volumes)[^\s\"']+"
    r"|(?<![A-Za-z0-9_])/[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+)"
    r"|(?:[A-Za-z]:\\[^\s\"']+)"
)

_EXTRA_SECRET_HINTS = frozenset(
    {
        "key",
        "password",
        "private_key",
        "fingerprint_key",
        "custody_key",
        "mac",
        "integrity_mac",
        "token",
    }
)


def _sensitive_keys() -> frozenset[str]:
    return privacy_sensitive_argument_keys() | _EXTRA_SECRET_HINTS


def redact_absolute_paths(value: str) -> str:
    """Replace absolute filesystem paths with a stable placeholder."""
    return _ABS_PATH_RE.sub("<redacted-path>", value)


def redact_error_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy an error envelope, scrubbing paths and secret/privacy keys."""
    sensitive = _sensitive_keys()

    def _walk(node: Any, *, key_hint: str | None = None) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                lk = str(k).lower()
                if lk in sensitive or any(
                    h in lk for h in ("secret", "private_key", "token", "password")
                ):
                    out[k] = "<redacted>"
                else:
                    out[k] = _walk(v, key_hint=lk)
            return out
        if isinstance(node, list):
            return [_walk(v, key_hint=key_hint) for v in node]
        if isinstance(node, str):
            if key_hint and (
                key_hint in sensitive
                or "path" in key_hint
                or key_hint.endswith("_path")
                or key_hint.endswith("_dir")
            ):
                if node.startswith("/") or (len(node) > 2 and node[1] == ":"):
                    return "<redacted-path>"
                if key_hint in privacy_sensitive_argument_keys():
                    return "<redacted>"
            return redact_absolute_paths(node)
        return node

    return _walk(payload)
