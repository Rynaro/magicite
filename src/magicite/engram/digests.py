"""Canonical digests for Engram 1.0 manifests and projections (C1).

Asset digests cover raw bytes. Metadata / projection digests use canonical
sorted JSON without signatures or runtime state. No Unicode normalization
of artifact bytes (C10 alignment for authored content hashing).

Normative ``routing.body_digest`` (engram/1.0)
---------------------------------------------
``routing_body_digest(body_text)`` = hex SHA-256 of UTF-8 bytes of the
Markdown body that follows the closing frontmatter fence, after normalizing
newlines to LF (``\\r\\n`` / ``\\r`` → ``\\n``). No Unicode NFC, no
trimming, no path rewriting, no prose rewrite.

This is **distinct** from ``ids.body_sha256`` (engram/0.2), which hashes
``body_text.encode("utf-8")`` with **no** newline normalization and remains
the 0.2 dirty/staleness digest for existing callers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Keys stripped from metadata digests: signatures and volatile runtime state.
_METADATA_STRIP_KEYS = frozenset(
    {
        "signature",
        "signatures",
        "signer",
        "plasticity",
        "synapses",
        "peak_storage_strength",
        "embedding",
    }
)


def canonical_json_bytes(value: Any) -> bytes:
    """UTF-8 JSON, keys lexically sorted, compact separators, no NaN/Infinity."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asset_bytes_digest(raw: bytes) -> str:
    """SHA-256 of raw asset bytes (C1)."""
    return sha256_hex(raw)


def normalize_body_newlines(body_text: str) -> str:
    """LF-normalize body text for routing.body_digest (no other transforms)."""
    return body_text.replace("\r\n", "\n").replace("\r", "\n")


def routing_body_digest(body_text: str) -> str:
    """Normative engram/1.0 ``routing.body_digest`` — see module docstring."""
    return sha256_hex(normalize_body_newlines(body_text).encode("utf-8"))


def _strip_for_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_for_metadata(v) for k, v in value.items() if k not in _METADATA_STRIP_KEYS}
    if isinstance(value, list):
        return [_strip_for_metadata(v) for v in value]
    return value


def metadata_digest(frontmatter: dict[str, Any]) -> str:
    """Canonical frontmatter digest excluding signatures/runtime state (C1)."""
    return sha256_hex(canonical_json_bytes(_strip_for_metadata(frontmatter)))


def projection_digest(*, body_text: str, frontmatter: dict[str, Any]) -> str:
    """Digest binding projected routing text: LF-normalized body + stripped FM."""
    payload = {
        "body_digest": routing_body_digest(body_text),
        "frontmatter": _strip_for_metadata(frontmatter),
    }
    return sha256_hex(canonical_json_bytes(payload))


def assets_manifest_digest(assets: dict[str, dict[str, Any]]) -> str:
    """Canonical digest over the relative-path → descriptor map."""
    return sha256_hex(canonical_json_bytes(assets))
