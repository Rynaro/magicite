"""0.3 release-version and package-metadata acceptance checks."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _project() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]


def test_package_and_release_notes_identify_030() -> None:
    project = _project()
    assert project["version"] == "0.3.0"
    assert (ROOT / "docs" / "releases" / "0.3.0.md").is_file()


def test_package_metadata_has_no_self_digest() -> None:
    project = _project()
    serialized = repr(project).lower()
    assert "ghcr.io/rynaro/magicite@sha256:" not in serialized
    assert "<digest-from-v0.3.0-release>" not in serialized
