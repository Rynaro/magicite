"""Version-constraint grammar validation (C1).

SemVer: conjunctions of ``< <= = >= >`` comparators separated by commas,
exact ``major.minor.patch`` (optional prerelease), no ``^``/``~``/wildcard/OR.
PEP 440: standard ``packaging.specifiers.SpecifierSet`` semantics.
"""

from __future__ import annotations

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from magicite.engram.model_v1 import VersionConstraint

# Exact SemVer core: MAJOR.MINOR.PATCH with optional -prerelease / +build
_SEMVER_CORE = (
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))"
    r"?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)
_SEMVER_EXACT = re.compile(rf"^{_SEMVER_CORE}$")
_SEMVER_COMPARATOR = re.compile(rf"^(<=|>=|<|>|=)?\s*({_SEMVER_CORE})$")


class VersionConstraintError(ValueError):
    """Unsupported scheme or invalid range (fail closed)."""


def validate_version_constraint(constraint: VersionConstraint) -> None:
    """Raise :class:`VersionConstraintError` if the constraint is invalid."""
    if constraint.scheme == "semver":
        _validate_semver_range(constraint.range)
    elif constraint.scheme == "pep440":
        _validate_pep440_range(constraint.range)
    else:
        raise VersionConstraintError(f"unsupported version scheme: {constraint.scheme!r}")


def _validate_semver_range(range_text: str) -> None:
    text = range_text.strip()
    if not text:
        raise VersionConstraintError("empty semver range")
    # Reject explicitly excluded syntax early.
    if any(tok in text for tok in ("^", "~", "*", "||", " - ", " -")):
        raise VersionConstraintError(
            f"unsupported semver syntax in {range_text!r} (V1 excludes ^, ~, wildcard, and OR)"
        )
    if "||" in text or " or " in text.lower():
        raise VersionConstraintError(f"unsupported semver OR syntax in {range_text!r}")

    # Exact version alone is allowed.
    if _SEMVER_EXACT.match(text):
        return

    parts = [p.strip() for p in text.split(",")]
    if not parts or any(not p for p in parts):
        raise VersionConstraintError(f"invalid semver range: {range_text!r}")
    for part in parts:
        m = _SEMVER_COMPARATOR.match(part)
        if not m:
            raise VersionConstraintError(f"invalid semver comparator: {part!r}")


def _validate_pep440_range(range_text: str) -> None:
    text = range_text.strip()
    if not text:
        raise VersionConstraintError("empty pep440 range")
    try:
        SpecifierSet(text)
        return
    except InvalidSpecifier:
        pass
    # Bare exact versions are valid PEP 440 pins for our purposes.
    try:
        Version(text)
    except InvalidVersion as exc:
        raise VersionConstraintError(f"invalid pep440 range: {range_text!r}") from exc
