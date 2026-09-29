"""Unit tests for scripts/verify_wheel_install.py helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_wheel_probe():
    path = ROOT / "scripts" / "verify_wheel_install.py"
    spec = importlib.util.spec_from_file_location("verify_wheel_install", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_wheel_relative_path_is_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative --wheel paths must resolve before probe cwd changes."""
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "x.whl"
    wheel.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    monkeypatch.chdir(tmp_path)
    probe = _load_wheel_probe()
    resolved = probe._resolve_wheel(Path("dist/x.whl"))

    assert resolved.is_absolute()
    assert resolved == wheel.resolve()
