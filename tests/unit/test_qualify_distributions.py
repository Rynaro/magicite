from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import qualify_distributions as qualification  # noqa: E402


def test_corrupt_control_preserves_valid_wheel_name_and_metadata(tmp_path):
    wheel = tmp_path / "magicite-0.3.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(qualification.BROKEN_RESOURCE, "required")
        archive.writestr("magicite-0.3.1.dist-info/METADATA", "Name: magicite\nVersion: 0.3.1\n")
    broken = qualification.corrupt_resource(wheel, tmp_path / "broken")
    assert broken.name == wheel.name
    with zipfile.ZipFile(broken) as archive:
        assert qualification.BROKEN_RESOURCE not in archive.namelist()
        assert archive.read("magicite-0.3.1.dist-info/METADATA")


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "magicite/.aws/token", "magicite/__pycache__/x.pyc"]
)
def test_unintended_archive_paths_fail(name):
    with pytest.raises(ValueError):
        qualification.reject_unintended_archive_paths([name])


def test_output_inside_checkout_and_reused_directory_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification, "ROOT", tmp_path / "repo")
    with pytest.raises(ValueError):
        qualification.require_external_empty(tmp_path / "repo/output")
    external = tmp_path / "external"
    external.mkdir()
    (external / "old").touch()
    with pytest.raises(ValueError):
        qualification.require_external_empty(external)
