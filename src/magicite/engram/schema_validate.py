"""JSON-Schema and semantic validation for Engram frontmatter (0.2 / 1.0)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from magicite.engram.model_v1 import (
    KNOWN_EXTENSIONS,
    Compatibility,
    EngramFrontmatterV1,
    VersionConstraint,
)
from magicite.engram.version_constraints import VersionConstraintError, validate_version_constraint

SpecName = Literal["engram/0.2", "engram/1.0"]

_SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


class EngramSchemaError(ValueError):
    """Frontmatter failed schema or semantic validation (fail closed)."""


@dataclass
class SchemaValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


@lru_cache(maxsize=4)
def load_schema(spec: SpecName) -> dict[str, Any]:
    """Load the shipped JSON Schema for ``engram/0.2`` or ``engram/1.0``."""
    filename = {
        "engram/0.2": "engram-0.2.schema.json",
        "engram/1.0": "engram-1.0.schema.json",
    }[spec]
    path = _SCHEMA_DIR / filename
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_frontmatter_dict(
    data: dict[str, Any],
    *,
    spec: SpecName | None = None,
    known_extensions: frozenset[str] | None = None,
) -> SchemaValidationResult:
    """Validate a frontmatter mapping against JSON Schema + C1 semantics."""
    errors: list[str] = []
    resolved_spec: SpecName
    if spec is not None:
        resolved_spec = spec
    else:
        raw = data.get("spec")
        if raw == "engram/1.0":
            resolved_spec = "engram/1.0"
        elif raw == "engram/0.2":
            resolved_spec = "engram/0.2"
        else:
            return SchemaValidationResult(ok=False, errors=[f"unsupported or missing spec: {raw!r}"])

    schema = load_schema(resolved_spec)
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")

    if resolved_spec == "engram/1.0" and not errors:
        errors.extend(_semantic_v1_errors(data, known_extensions=known_extensions))

    return SchemaValidationResult(ok=not errors, errors=errors)


def validate_frontmatter_v1(
    frontmatter: EngramFrontmatterV1,
    *,
    known_extensions: frozenset[str] | None = None,
) -> SchemaValidationResult:
    """Validate a typed 1.0 model (schema round-trip + semantic gates)."""
    data = frontmatter.model_dump(mode="json", exclude_none=False)
    # Drop nulls that JSON Schema optional objects treat as absent-friendly,
    # but keep structure for required fields.
    return validate_frontmatter_dict(data, spec="engram/1.0", known_extensions=known_extensions)


def assert_valid_frontmatter(
    data: dict[str, Any],
    *,
    spec: SpecName | None = None,
    known_extensions: frozenset[str] | None = None,
) -> None:
    result = validate_frontmatter_dict(data, spec=spec, known_extensions=known_extensions)
    if not result.ok:
        raise EngramSchemaError("; ".join(result.errors))


def _semantic_v1_errors(
    data: dict[str, Any],
    *,
    known_extensions: frozenset[str] | None,
) -> list[str]:
    errors: list[str] = []
    known = KNOWN_EXTENSIONS if known_extensions is None else known_extensions

    extensions = data.get("extensions") or {}
    if isinstance(extensions, dict):
        for key, value in extensions.items():
            if not isinstance(value, dict):
                errors.append(f"extensions.{key}: must be an object")
                continue
            if value.get("required") is True and key not in known:
                errors.append(f"extensions.{key}: unknown required extension rejected (fail closed)")

    # Walk every VersionConstraint-shaped object.
    for label, constraint in _iter_version_constraints(data):
        try:
            validate_version_constraint(VersionConstraint.model_validate(constraint))
        except (VersionConstraintError, Exception) as exc:  # noqa: BLE001 — surface as schema error
            errors.append(f"{label}: {exc}")

    # Produced capabilities must be artifact-kind (schema const, but double-check).
    caps = data.get("capabilities") or {}
    if isinstance(caps, dict):
        for i, prod in enumerate(caps.get("produces") or []):
            if isinstance(prod, dict) and prod.get("kind") not in (None, "artifact"):
                errors.append(f"capabilities.produces[{i}].kind: must be 'artifact'")
            if isinstance(prod, dict) and "kind" not in prod:
                errors.append(f"capabilities.produces[{i}].kind: missing kind is invalid")
        for i, req in enumerate(caps.get("requires") or []):
            if isinstance(req, dict) and "kind" not in req:
                errors.append(f"capabilities.requires[{i}].kind: missing kind is invalid")

    # Optional: re-validate Compatibility via pydantic for key normalization certainty.
    if data.get("compatibility") is not None:
        try:
            Compatibility.model_validate(data["compatibility"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"compatibility: {exc}")

    return errors


def _iter_version_constraints(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    compat = data.get("compatibility") or {}
    if isinstance(compat, dict):
        for dim in ("languages", "frameworks", "package_managers"):
            mapping = compat.get(dim) or {}
            if isinstance(mapping, dict):
                for key, constraint in mapping.items():
                    if isinstance(constraint, dict):
                        found.append((f"compatibility.{dim}.{key}", constraint))
        for i, host in enumerate(compat.get("hosts") or []):
            if isinstance(host, dict) and isinstance(host.get("version"), dict):
                found.append((f"compatibility.hosts[{i}].version", host["version"]))

    caps = data.get("capabilities") or {}
    if isinstance(caps, dict):
        for i, req in enumerate(caps.get("requires") or []):
            if isinstance(req, dict) and isinstance(req.get("version"), dict):
                found.append((f"capabilities.requires[{i}].version", req["version"]))
    return found


def schema_path(spec: SpecName) -> Path:
    """Filesystem path of the shipped schema (for fixture / packaging checks)."""
    name = "engram-1.0.schema.json" if spec == "engram/1.0" else "engram-0.2.schema.json"
    return _SCHEMA_DIR / name


# Re-export for callers that catch jsonschema errors directly.
JsonSchemaValidationError = JsonSchemaValidationError
