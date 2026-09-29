"""External asset containment and digest validation (C1).

Resolve beneath registry root; reject absolute paths, ``..``, backslashes,
symlink escape, case-insensitive duplicate keys, duplicate resolve targets,
and digest mismatch. No remote fetch. Host FS case-sensitivity does not
affect duplicate detection (casefold is always applied).

V1 path alphabet (deliberate bound): authored asset keys must be relative
POSIX paths whose segments match ``[A-Za-z0-9._-]+`` (see
``engram-1.0.schema.json`` ``assets.propertyNames``). Unicode / non-ASCII
asset paths are rejected fail-closed in V1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from magicite.engram.digests import asset_bytes_digest
from magicite.engram.model_v1 import AssetDescriptor


class AssetValidationError(ValueError):
    """Asset path or digest failed closed (C1 / AC-S02-04)."""


@dataclass(frozen=True)
class AssetValidationIssue:
    path: str
    reason: str


def _normalize_relpath(rel: str) -> str:
    if not rel or not isinstance(rel, str):
        raise AssetValidationError("asset path must be a non-empty relative string")
    if "\\" in rel:
        raise AssetValidationError(f"backslash in asset path rejected: {rel!r}")
    if rel.startswith("/") or PurePosixPath(rel).is_absolute():
        raise AssetValidationError(f"absolute asset path rejected: {rel!r}")
    # Windows drive / UNC style
    if len(rel) >= 2 and rel[1] == ":":
        raise AssetValidationError(f"absolute asset path rejected: {rel!r}")
    parts = PurePosixPath(rel).parts
    if any(p == ".." for p in parts):
        raise AssetValidationError(f"path traversal rejected: {rel!r}")
    if any(p == "" for p in parts):
        raise AssetValidationError(f"empty path segment rejected: {rel!r}")
    normalized = PurePosixPath(*[p for p in parts if p != "."])
    if normalized == PurePosixPath(".") or str(normalized) in ("", "."):
        raise AssetValidationError(f"empty/normalized-away asset path rejected: {rel!r}")
    text = normalized.as_posix()
    if text.startswith("../") or text == "..":
        raise AssetValidationError(f"path traversal rejected: {rel!r}")
    return text


def resolve_asset_path(registry_root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``registry_root``; raise on escape or bad symlink.

    Checks ``is_symlink()`` on each path component **before** following links,
    so a leaf symlink that points outside the registry is rejected even when
    ``Path.resolve()`` would otherwise erase the symlink bit.
    """
    normalized = _normalize_relpath(rel)
    root = registry_root.resolve()
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.is_symlink():
            target = current.resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise AssetValidationError(f"symlink escapes registry root: {rel!r}") from exc
            current = target
    try:
        current.relative_to(root)
    except ValueError as exc:
        raise AssetValidationError(f"asset path escapes registry root: {rel!r}") from exc
    return current


def _as_descriptor(value: Any) -> AssetDescriptor | None:
    if isinstance(value, AssetDescriptor):
        return value
    if isinstance(value, dict):
        try:
            return AssetDescriptor.model_validate(value)
        except Exception:  # noqa: BLE001 — surface as path issue later
            return None
    return None


def validate_assets(
    assets: dict[str, Any],
    *,
    registry_root: Path | None = None,
    require_files: bool = True,
) -> list[AssetValidationIssue]:
    """Validate containment + digests. Returns issues; empty means pass.

    Always applies path-shape, casefold-duplicate, and (when files are
    resolved) same-resolve-target checks. When ``require_files`` is True and
    ``registry_root`` is set, missing files and digest mismatches are issues.
    Callers that only want path-shape checks can set ``require_files=False``.
    """
    issues: list[AssetValidationIssue] = []
    seen_normalized: dict[str, str] = {}
    seen_casefold: dict[str, str] = {}
    seen_resolved: dict[Path, str] = {}

    for raw_path, raw_desc in assets.items():
        try:
            normalized = _normalize_relpath(raw_path)
        except AssetValidationError as exc:
            issues.append(AssetValidationIssue(path=raw_path, reason=str(exc)))
            continue

        if normalized in seen_normalized:
            issues.append(
                AssetValidationIssue(
                    path=raw_path,
                    reason=(
                        f"duplicate normalized path {normalized!r} (also {seen_normalized[normalized]!r})"
                    ),
                )
            )
            continue
        seen_normalized[normalized] = raw_path

        folded = normalized.casefold()
        if folded in seen_casefold:
            issues.append(
                AssetValidationIssue(
                    path=raw_path,
                    reason=(
                        f"case-insensitive duplicate path {normalized!r} (also {seen_casefold[folded]!r})"
                    ),
                )
            )
            continue
        seen_casefold[folded] = raw_path

        descriptor = _as_descriptor(raw_desc)
        if descriptor is None and require_files and registry_root is not None:
            issues.append(AssetValidationIssue(path=raw_path, reason="invalid asset descriptor"))
            continue

        if registry_root is None:
            continue

        try:
            resolved = resolve_asset_path(registry_root, normalized)
        except AssetValidationError as exc:
            issues.append(AssetValidationIssue(path=raw_path, reason=str(exc)))
            continue

        if resolved in seen_resolved:
            issues.append(
                AssetValidationIssue(
                    path=raw_path,
                    reason=(f"duplicate resolve target {resolved} (also {seen_resolved[resolved]!r})"),
                )
            )
            continue
        seen_resolved[resolved] = raw_path

        if not require_files:
            continue

        if descriptor is None:
            issues.append(AssetValidationIssue(path=raw_path, reason="invalid asset descriptor"))
            continue

        if not resolved.is_file():
            issues.append(AssetValidationIssue(path=raw_path, reason=f"asset file missing: {normalized}"))
            continue

        raw = resolved.read_bytes()
        digest = asset_bytes_digest(raw)
        if digest != descriptor.sha256:
            issues.append(
                AssetValidationIssue(
                    path=raw_path,
                    reason=f"digest mismatch: expected {descriptor.sha256}, got {digest}",
                )
            )
        if len(raw) != descriptor.size:
            issues.append(
                AssetValidationIssue(
                    path=raw_path,
                    reason=f"size mismatch: expected {descriptor.size}, got {len(raw)}",
                )
            )

    return issues


def assert_assets_valid(
    assets: dict[str, Any],
    *,
    registry_root: Path | None = None,
    require_files: bool = True,
) -> None:
    """Raise :class:`AssetValidationError` on the first issue."""
    issues = validate_assets(assets, registry_root=registry_root, require_files=require_files)
    if issues:
        first = issues[0]
        raise AssetValidationError(f"{first.path}: {first.reason}")
