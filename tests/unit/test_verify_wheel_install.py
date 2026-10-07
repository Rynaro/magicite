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


def test_resolve_wheel_relative_path_is_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_isolated_environment_scrubs_checkout_and_framework_launcher():
    probe = _load_wheel_probe()
    env = probe.isolated_subprocess_env(
        {
            "PYTHONPATH": "/repo/src",
            "PYTHONHOME": "/repo",
            "VIRTUAL_ENV": "/old",
            "__PYVENV_LAUNCHER__": "/old/python",
        }
    )
    assert env["PYTHONPATH"] == "" and env["PYTHONNOUSERSITE"] == "1"
    assert all(key not in env for key in ("PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"))


def test_package_expectations_include_all_current_schema_and_migration_resources():
    probe = _load_wheel_probe()
    expected = probe.package_expectations()
    files = expected["package_files"]
    assert {name for name in files if name.endswith(".schema.json")} == {
        "engram/schema/engram-0.2.schema.json",
        "engram/schema/engram-1.0.schema.json",
    }
    assert len([name for name in files if name.endswith(".sql")]) == 6
    assert len(expected["tools"]) == 16
    assert expected["entrypoints"] == {
        "magicite": "magicite.__main__:cli",
        "magicite-bench": "magicite.eval.bench:main",
    }


def test_installed_harness_is_standalone_and_contains_actual_ancestry_guard():
    probe = _load_wheel_probe()
    compile(probe.PROBE, "installed-probe.py", "exec")
    assert "tests.support" not in probe.PROBE and "sys.path.insert" not in probe.PROBE
    assert "pkg_file.is_relative_to(prefix)" in probe.PROBE


@pytest.mark.parametrize(
    "arguments,exit_code,stdout",
    [
        (["--version"], 0, "magicite, version 9.9.9"),
        (["--help"], 0, ""),
        (["doctor", "--project-root", "/fixture"], 1, "{}"),
    ],
)
def test_cli_success_exit_cannot_hide_wrong_observed_contract(arguments, exit_code, stdout):
    probe = _load_wheel_probe()
    with pytest.raises((AssertionError, KeyError)):
        probe.validate_cli_output(arguments, exit_code, stdout, {"version": "0.3.1"})


def test_probe_rejects_checkout_working_directory_before_install(tmp_path, monkeypatch):
    probe = _load_wheel_probe()
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    fixture = tmp_path / "fixtures"
    fixture.mkdir()
    monkeypatch.setattr(probe, "TOY_ENGRAMS", fixture)
    with pytest.raises(ValueError, match="outside checkout"):
        probe.run_probe(wheel=tmp_path / "candidate.whl", keep_env=tmp_path / "inside")


def test_installed_origins_validate_namespace_search_path(tmp_path):
    import ast
    from types import SimpleNamespace

    probe = _load_wheel_probe()
    tree = ast.parse(probe.PROBE)
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "origins"
    )
    directory = tmp_path / "magicite/storage/migrations"
    directory.mkdir(parents=True)
    paths = [str(directory)]
    namespace = SimpleNamespace(
        __file__=None, __path__=paths, __spec__=SimpleNamespace(origin=None, submodule_search_locations=paths)
    )
    scope = {
        "sys": SimpleNamespace(modules={"magicite.storage.migrations": namespace}),
        "Path": Path,
        "prefix": tmp_path.resolve(),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "installed-origins", "exec"), scope)
    assert scope["origins"]() == {"magicite.storage.migrations [namespace]": "magicite/storage/migrations"}
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    for invalid in ([str(outside)], [str(directory), str(outside)], []):
        namespace.__path__ = invalid
        namespace.__spec__.submodule_search_locations = invalid
        with pytest.raises(AssertionError):
            scope["origins"]()
    namespace.__path__ = paths
    namespace.__spec__.submodule_search_locations = None
    with pytest.raises(AssertionError, match="namespace-module-origin"):
        scope["origins"]()
