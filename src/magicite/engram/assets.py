"""External asset containment and digest validation (C1).

Resolve beneath registry root; reject absolute paths, ``..``, symlink escape,
duplicate normalized paths and digest mismatch. No remote fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
    if rel.startswith("/") or PurePosixPath(rel).is_absolute():
        raise AssetValidationError(f"absolute asset path rejected: {rel!r}")
    # Windows drive / UNC style
    if len(rel) >= 2 and rel[1] == ":":
        raise AssetValidationError(f"absolute asset path rejected: {rel!r}")
    parts = PurePosixPath(rel).parts
    if ".." in parts or parts[:1] == ("..",):
        raise AssetValidationError(f"path traversal rejected: {rel!r}")
    if any(p == "" or p == "." for p in parts if p not in (".",)):
        # collapse via PurePosixPath
        pass
    normalized = PurePosixPath(*[p for p in parts if p not in ("", ".")])
    if normalized == PurePosixPath(".") or str(normalized) == ".":
        raise AssetValidationError(f"empty/normalized-away asset path rejected: {rel!r}")
    text = normalized.as_posix()
    if text.startswith("../") or text == "..":
        raise AssetValidationError(f"path traversal rejected: {rel!r}")
    return text


def resolve_asset_path(registry_root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``registry_root``; raise on escape."""
    normalized = _normalize_relpath(rel)
    root = registry_root.resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AssetValidationError(f"asset path escapes registry root: {rel!r}") from exc
    # Symlink escape: if any parent under root is a symlink pointing outside,
    # resolve() already followed it; relative_to check above catches that.
    return candidate


def validate_assets(
    assets: dict[str, AssetDescriptor],
    *,
    registry_root: Path,
    require_files: bool = True,
) -> list[AssetValidationIssue]:
    """Validate containment + digests. Returns issues; empty means pass.

    When ``require_files`` is True (load path), missing files and digest
    mismatches are issues. Callers that only want path-shape checks can
    set ``require_files=False``.
    """
    issues: list[AssetValidationIssue] = []
    seen: dict[str, str] = {}

    for raw_path, descriptor in assets.items():
        try:
            normalized = _normalize_relpath(raw_path)
        except AssetValidationError as exc:
            issues.append(AssetValidationIssue(path=raw_path, reason=str(exc)))
            continue

        if normalized in seen:
            issues.append(
                AssetValidationIssue(
                    path=raw_path,
                    reason=f"duplicate normalized path {normalized!r} (also {seen[normalized]!r})",
                )
            )
            continue
        seen[normalized] = raw_path

        try:
            resolved = resolve_asset_path(registry_root, normalized)
        except AssetValidationError as exc:
            issues.append(AssetValidationIssue(path=raw_path, reason=str(exc)))
            continue

        if not require_files:
            continue

        if not resolved.is_file():
            issues.append(AssetValidationIssue(path=raw_path, reason=f"asset file missing: {normalized}"))
            continue

        # Reject if the path itself is a symlink that escaped (already resolved)
        # but also reject symlink-at-leaf that points outside via relative_to.
        if resolved.is_symlink():
            try:
                resolved.resolve().relative_to(registry_root.resolve())
            except ValueError:
                issues.append(AssetValidationIssue(path=raw_path, reason="symlink escapes registry root"))
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
    assets: dict[str, AssetDescriptor],
    *,
    registry_root: Path,
    require_files: bool = True,
) -> None:
    """Raise :class:`AssetValidationError` on the first issue."""
    issues = validate_assets(assets, registry_root=registry_root, require_files=require_files)
    if issues:
        first = issues[0]
        raise AssetValidationError(f"{first.path}: {first.reason}")
