"""Translate declared corpus compatibility facts into the domain route context."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from magicite.core.context import ArtifactInventoryEntry, HostFact, RouteContext


def corpus_route_context(mapping: dict[str, Any]) -> RouteContext:
    allowed = {field.name for field in fields(RouteContext)}
    # no_match and similar label annotations are not runtime grants.
    data = {key: value for key, value in mapping.items() if key in allowed}
    if isinstance(data.get("host"), dict):
        data["host"] = HostFact(**data["host"])
    if data.get("artifact_inventory") is not None:
        data["artifact_inventory"] = tuple(
            ArtifactInventoryEntry(**item) for item in data["artifact_inventory"]
        )
    for key in ("permission_grants", "allowed_tools", "excluded_engram_ids"):
        if data.get(key) is not None:
            data[key] = frozenset(data[key])
    return RouteContext(**data)
