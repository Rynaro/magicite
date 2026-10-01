"""JSON-Schema and semantic validation for Engram frontmatter (0.2 / 1.0).

Parse vs admit
--------------
Structural parse (``parser.parse_artifact(..., admit=False)``) only builds
typed models. Admission (``load_artifact`` / ``parse_artifact(..., admit=True)``)
runs this module: JSON Schema, version-constraint grammar, unknown required
extensions, asset path containment, and ``routing.body_digest`` match.
Invalid caret ranges (``^1.2.3``) therefore parse structurally but fail admit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from magicite.engram.assets import validate_assets
from magicite.engram.digests import routing_body_digest
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


def clear_schema_cache() -> None:
    """Test helper after schema file edits in-process."""
    load_schema.cache_clear()


def validate_frontmatter_dict(
    data: dict[str, Any],
    *,
    spec: SpecName | None = None,
    known_extensions: frozenset[str] | None = None,
    registry_root: Path | None = None,
    body_text: str | None = None,
    require_asset_files: bool = False,
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

    if resolved_spec == "engram/1.0":
        # Always run semantic gates (asset paths, constraints) even when JSON
        # Schema already failed — fail closed with full diagnostics.
        errors.extend(
            _semantic_v1_errors(
                data,
                known_extensions=known_extensions,
                registry_root=registry_root,
                body_text=body_text,
                require_asset_files=require_asset_files,
            )
        )

    return SchemaValidationResult(ok=not errors, errors=errors)


def validate_frontmatter_v1(
    frontmatter: EngramFrontmatterV1,
    *,
    known_extensions: frozenset[str] | None = None,
    registry_root: Path | None = None,
    body_text: str | None = None,
    require_asset_files: bool = False,
) -> SchemaValidationResult:
    """Validate a typed 1.0 model (schema round-trip + semantic gates)."""
    data = frontmatter.model_dump(mode="json", exclude_none=False)
    return validate_frontmatter_dict(
        data,
        spec="engram/1.0",
        known_extensions=known_extensions,
        registry_root=registry_root,
        body_text=body_text,
        require_asset_files=require_asset_files,
    )


def assert_valid_frontmatter(
    data: dict[str, Any],
    *,
    spec: SpecName | None = None,
    known_extensions: frozenset[str] | None = None,
    registry_root: Path | None = None,
    body_text: str | None = None,
    require_asset_files: bool = False,
) -> None:
    result = validate_frontmatter_dict(
        data,
        spec=spec,
        known_extensions=known_extensions,
        registry_root=registry_root,
        body_text=body_text,
        require_asset_files=require_asset_files,
    )
    if not result.ok:
        raise EngramSchemaError("; ".join(result.errors))


def _semantic_v1_errors(
    data: dict[str, Any],
    *,
    known_extensions: frozenset[str] | None,
    registry_root: Path | None,
    body_text: str | None,
    require_asset_files: bool,
) -> list[str]:
    errors: list[str] = []
    known = KNOWN_EXTENSIONS if known_extensions is None else known_extensions

    extensions = data.get("extensions") or {}
    if isinstance(extensions, dict):
        for key, value in extensions.items():
            if not isinstance(value, dict):
                errors.append(f"extensions.{key}: must be an object")
                continue
            if key == "magicite.trust_journal" and key in known:
                if (value.get("required") is not True
                        or value.get("journal_version") != "trust-journal/1"
                        or not isinstance(value.get("registry_id"), str)
                        or not value["registry_id"]
                        or set(value) != {"required", "registry_id", "journal_version"}):
                    errors.append("extensions.magicite.trust_journal: invalid enrollment marker")
            if value.get("required") is True and key not in known:
                errors.append(f"extensions.{key}: unknown required extension rejected (fail closed)")

    for label, constraint in _iter_version_constraints(data):
        try:
            validate_version_constraint(VersionConstraint.model_validate(constraint))
        except (VersionConstraintError, Exception) as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")

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

    if data.get("compatibility") is not None:
        try:
            Compatibility.model_validate(data["compatibility"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"compatibility: {exc}")

    assets = data.get("assets") or {}
    if isinstance(assets, dict) and assets:
        for issue in validate_assets(
            assets,
            registry_root=registry_root,
            require_files=require_asset_files and registry_root is not None,
        ):
            errors.append(f"assets.{issue.path}: {issue.reason}")

    if body_text is not None:
        routing = data.get("routing") or {}
        declared = routing.get("body_digest") if isinstance(routing, dict) else None
        if isinstance(declared, str):
            expected = routing_body_digest(body_text)
            if declared != expected:
                errors.append(
                    f"routing.body_digest: mismatch (declared {declared}, "
                    f"expected {expected} from LF-normalized body)"
                )

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


JsonSchemaValidationError = JsonSchemaValidationError
