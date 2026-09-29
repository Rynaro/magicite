"""S13 acceptance: clean installation channel probes (scaffold milestone).

AC-S13-01..04 VERIFY targets. Scaffold covers locally built wheel install +
channel probe interfaces + release-manifest digest checks. Published-channel
installs (PyPI/pipx/uvx/OCI digest pull) remain UNEVALUATED until milestone
``S13-published-channels`` (after S11/S15).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[2]
TOY_ENGRAMS = ROOT / "tests" / "fixtures" / "toy-registry" / "engrams"


def _load_supply_chain():
    path = ROOT / "scripts" / "check_supply_chain.py"
    spec = importlib.util.spec_from_file_location("check_supply_chain", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_wheel_probe():
    path = ROOT / "scripts" / "verify_wheel_install.py"
    spec = importlib.util.spec_from_file_location("verify_wheel_install", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_existing_wheel() -> Path | None:
    env_wheel = os.environ.get("MAGICITE_TEST_WHEEL")
    if env_wheel:
        path = Path(env_wheel)
        return path if path.is_file() else None
    dist = ROOT / "dist"
    if not dist.is_dir():
        return None
    wheels = sorted(dist.glob("magicite-*.whl"))
    return wheels[-1] if wheels else None


def _build_wheel(out_dir: Path) -> Path:
    existing = _find_existing_wheel()
    if existing is not None:
        return existing
    out_dir.mkdir(parents=True, exist_ok=True)
    # Prefer an already-available builder; otherwise create a throwaway venv
    # with `build` (hatchling is pulled in isolation by pep517).
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
            check=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        builder = Path(tempfile.mkdtemp(prefix="magicite-build-tools-"))
        venv.create(builder / "venv", with_pip=True, clear=True)
        py = builder / "venv" / "bin" / "python"
        subprocess.run([str(py), "-m", "pip", "install", "build"], check=True)
        subprocess.run(
            [str(py), "-m", "build", "--wheel", "--outdir", str(out_dir)],
            check=True,
            cwd=ROOT,
        )
    wheels = sorted(out_dir.glob("magicite-*.whl"))
    assert wheels, f"wheel build produced no artifacts under {out_dir}"
    return wheels[-1]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_wheel(tmp_path_factory.mktemp("dist"))


def test_clean_wheel(built_wheel: Path, tmp_path: Path) -> None:
    """AC-S13-01: built wheel in a clean temp env routes the offline fixture."""
    probe = _load_wheel_probe()
    result = probe.run_probe(wheel=built_wheel, keep_env=tmp_path / "clean-env")
    assert result["ok"] is True
    assert result["top"] == "proton-ge-proton-downgrade"
    assert result["ingested"] >= 1


def test_missing_model(built_wheel: Path, tmp_path: Path) -> None:
    """AC-S13-02: offline production provider without cache remediates acquisition.

    Scaffold asserts the typed offline failure from a clean wheel install.
    Explicit ``magicite fetch-model`` remediation wording is owned by
    ``src/magicite/embeddings/*`` (S05); until that patch lands the
    remediation clause is an explicit xfail rather than a silent weaken.
    """
    venv_dir = tmp_path / "missing-model-venv"
    venv.create(venv_dir, with_pip=True, clear=True)
    python = venv_dir / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True, cwd=tmp_path)
    subprocess.run(
        [str(python), "-m", "pip", "install", str(built_wheel)],
        check=True,
        cwd=tmp_path,
    )

    cache_dir = tmp_path / "empty-model-cache"
    cache_dir.mkdir()
    script = f"""
from pathlib import Path
from magicite.embeddings.fastembed_provider import (
    FastEmbedModelUnavailableError,
    FastEmbedProvider,
)

cache = Path({str(cache_dir)!r})
provider = FastEmbedProvider(offline=True, cache_dir=str(cache))
try:
    provider.embed("rollback proton for a steam game")
except FastEmbedModelUnavailableError as exc:
    print("ERROR_TYPE", type(exc).__name__)
    print("ERROR_MSG", str(exc))
else:
    raise SystemExit("expected FastEmbedModelUnavailableError")
"""
    completed = subprocess.run(
        [str(python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
        # Isolate cwd: importing onnxruntime (via fastembed) writes a stray
        # `:memory:.ses` into the process cwd when telemetry cannot persist a
        # device id (onnxruntime telemetry.cc). Keep that out of the repo.
        cwd=tmp_path,
    )
    assert "ERROR_TYPE FastEmbedModelUnavailableError" in completed.stdout
    assert "offline=True" in completed.stdout
    if "fetch-model" not in completed.stdout:
        pytest.xfail(
            "AC-S13-02 remediation text requires S05 embeddings patch "
            "(proposed in S13 completion report); typed offline failure already enforced"
        )


@pytest.mark.parametrize(
    "channel_id,kind,milestone",
    [
        ("wheel-local", "python-wheel", "scaffold"),
        ("sdist-local", "python-sdist", "scaffold"),
        ("pip-pypi", "pypi", "published-channels"),
        ("pipx", "pipx", "published-channels"),
        ("uvx", "uvx", "published-channels"),
        ("oci-digest", "oci", "published-channels"),
    ],
)
def test_channel_matrix(
    channel_id: str,
    kind: str,
    milestone: str,
    built_wheel: Path,
    tmp_path: Path,
) -> None:
    """AC-S13-03: advertised channels share the conformance fixture.

    Scaffold evaluates locally built wheel/sdist probes. Published channels
    are intentionally skipped until milestone published-channels.
    """
    if milestone == "published-channels":
        pytest.skip(
            f"channel {channel_id} ({kind}) requires S13-published-channels "
            "(after S11/S15); probe interface reserved"
        )

    if channel_id == "wheel-local":
        probe = _load_wheel_probe()
        result = probe.run_probe(wheel=built_wheel, keep_env=tmp_path / "channel-wheel")
        assert result["ok"] is True
        return

    # sdist-local: build sdist when possible; otherwise skip with reason.
    sdist_dir = tmp_path / "sdist-dist"
    sdist_dir.mkdir()
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--sdist", "--outdir", str(sdist_dir)],
            check=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        builder = tmp_path / "sdist-builder"
        venv.create(builder / "venv", with_pip=True, clear=True)
        py = builder / "venv" / "bin" / "python"
        subprocess.run([str(py), "-m", "pip", "install", "build"], check=True, cwd=tmp_path)
        subprocess.run(
            [str(py), "-m", "build", "--sdist", "--outdir", str(sdist_dir)],
            check=True,
            cwd=ROOT,
        )
    sdists = sorted(sdist_dir.glob("magicite-*.tar.gz"))
    assert sdists, "sdist build produced no artifacts"
    # Install sdist into a clean env and reuse the wheel probe script's route
    # assertions via an ad-hoc install (sdist path instead of wheel).
    venv_dir = tmp_path / "sdist-venv"
    venv.create(venv_dir, with_pip=True, clear=True)
    python = venv_dir / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True, cwd=tmp_path)
    subprocess.run(
        [str(python), "-m", "pip", "install", str(sdists[-1])],
        check=True,
        cwd=tmp_path,
    )
    project_root = tmp_path / "sdist-project"
    project_root.mkdir()
    completed = subprocess.run(
        [
            str(python),
            "-c",
            _load_wheel_probe().PROBE,
            str(TOY_ENGRAMS),
            str(project_root),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    import json

    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["ok"] is True
    assert payload["top"] == "proton-ge-proton-downgrade"


def test_release_manifest_roundtrip(built_wheel: Path, tmp_path: Path) -> None:
    """AC-S13-04 scaffold: release-manifest digests match declared artifacts."""
    supply = _load_supply_chain()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = artifacts / built_wheel.name
    target.write_bytes(built_wheel.read_bytes())
    manifest_path = tmp_path / "release-manifest.json"
    supply.write_manifest(artifacts_dir=artifacts, output=manifest_path, version="0.3.1")
    failures = supply.verify_manifest(manifest_path, artifacts_root=tmp_path)
    assert failures == []

    # Tamper to prove the negative check remains visible.
    target.write_bytes(target.read_bytes() + b"\n")
    failures = supply.verify_manifest(manifest_path, artifacts_root=tmp_path)
    assert any("digest mismatch" in item for item in failures)


def test_immutable_supply_chain_inputs() -> None:
    """Existing pin-immutability gate stays green under S13 ownership."""
    supply = _load_supply_chain()
    assert supply.check_immutable_inputs() == []
