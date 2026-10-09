"""Proposed preview release-version and package-metadata acceptance checks."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _project() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]


def test_package_and_release_notes_identify_preview() -> None:
    project = _project()
    assert project["version"] == "1.0.0rc4"
    assert (ROOT / "docs" / "releases" / "1.0.0-rc.4.md").is_file()
    assert (ROOT / "docs" / "releases" / "1.0.0-rc.3.md").is_file()
    assert (ROOT / "docs" / "releases" / "1.0.0-rc.2.md").is_file()
    assert (ROOT / "docs" / "releases" / "1.0.0-rc.1.md").is_file()
    assert (ROOT / "docs" / "releases" / "0.3.1.md").is_file()


def test_package_metadata_has_no_self_digest() -> None:
    project = _project()
    serialized = repr(project).lower()
    assert "ghcr.io/rynaro/magicite@sha256:" not in serialized
    assert "<digest-from-v0.3.0-release>" not in serialized
