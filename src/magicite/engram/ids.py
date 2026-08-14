"""Engram id computation and content hashing (spec §2.2, CR-8).

``id`` is computed **once**, at first registration, from the
identity+routing blocks, and is thereafter an immutable primary key
(CR-8). ``identity_sha256`` is the same hash recomputed on every
register/sync pass and is used only for drift/duplicate-import detection
-- it is never written back into the ``id:`` field.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

ID_PREFIX = "egr_"
ID_HEX_LEN = 8


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def identity_routing_payload(
    name: str,
    intent_does: str,
    intent_use_when: str,
    intent_not_when: str | None,
    triggers_positive: list[str],
    triggers_negative: list[str],
) -> dict[str, Any]:
    """The exact fields CR-8 defines as "identity+routing" for hashing."""
    return {
        "name": name,
        "intent": {
            "does": intent_does,
            "use_when": intent_use_when,
            "not_when": intent_not_when,
        },
        "triggers": {
            "positive": sorted(triggers_positive),
            "negative": sorted(triggers_negative),
        },
    }


def identity_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def new_engram_id(payload: dict[str, Any]) -> str:
    """Compute the first-registration id: ``egr_<8 hex>`` of the identity hash."""
    digest = identity_sha256(payload)
    return f"{ID_PREFIX}{digest[:ID_HEX_LEN]}"


def content_sha256(raw_bytes: bytes) -> str:
    """Whole-file digest -> dirty detection."""
    return hashlib.sha256(raw_bytes).hexdigest()


def body_sha256(body_text: str) -> str:
    """Body-only digest -> embedding staleness."""
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()


def is_valid_engram_id(value: str) -> bool:
    if not value.startswith(ID_PREFIX):
        return False
    suffix = value[len(ID_PREFIX) :]
    if len(suffix) != ID_HEX_LEN:
        return False
    try:
        int(suffix, 16)
    except ValueError:
        return False
    return True
