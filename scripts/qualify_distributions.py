#!/usr/bin/env python3
"""Source-bound local wheel/sdist rehearsal; never publishes candidate artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

import verify_wheel_install as probe

ROOT = Path(__file__).resolve().parents[1]
BROKEN_RESOURCE = "magicite/engram/schema/engram-1.0.schema.json"
LOG_NAMES = (
    "install.log",
    "prepare-stdout.log",
    "prepare-stderr.log",
    "wire.json",
    "server-origins.json",
    "server-stderr.log",
    "result.json",
    "expected.json",
    "installed-probe.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_external_empty(output: Path) -> Path:
    output = output.resolve()
    if output.is_relative_to(ROOT.resolve()):
        raise ValueError("qualification output must be outside checkout")
    if output.exists() and any(output.iterdir()):
        raise ValueError("qualification requires a fresh empty output directory")
    output.mkdir(parents=True, exist_ok=True)
    return output


def corrupt_resource(wheel: Path, directory: Path) -> Path:
    directory.mkdir()
    target = directory / wheel.name
    with zipfile.ZipFile(wheel) as original, zipfile.ZipFile(target, "w") as broken:
        if BROKEN_RESOURCE not in original.namelist():
            raise ValueError("required negative-control resource not present")
        for member in original.infolist():
            if member.filename != BROKEN_RESOURCE:
                broken.writestr(member, original.read(member.filename))
    return target


def reject_unintended_archive_paths(names: list[str]) -> None:
    forbidden = {".git", ".claude", ".eidolons", ".aws", "__pycache__", ".venv", ".pytest_cache"}
    for name in names:
        parts = Path(name).parts
        if Path(name).is_absolute() or ".." in parts or forbidden.intersection(parts):
            raise ValueError("unintended agent/user/cache/archive path")


def check_archive(artifact: Path) -> list[str]:
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
    else:
        import tarfile

        with tarfile.open(artifact) as archive:
            names = archive.getnames()
            if any(member.issym() or member.islnk() for member in archive.getmembers()):
                raise ValueError("unexpected distribution link")
    reject_unintended_archive_paths(names)
    return names


def run(output: Path) -> dict:
    output = require_external_empty(output)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    clean = not subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True)
    if not clean:
        raise ValueError("clean committed candidate required before building")
    dist = output / "dist"
    dist.mkdir()
    build_command = [sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(dist)]
    report = {
        "schema": "magicite/candidate-install/1",
        "source_commit": head,
        "source_clean": clean,
        "status": "UNEVALUATED",
        "python": sys.version,
        "platform": platform.platform(),
        "build_tool_version": importlib.metadata.version("build"),
        "build_command": build_command,
        "source_input_sha256": {
            str(path.relative_to(ROOT)): digest(path)
            for path in [
                *sorted((ROOT / "src/magicite").rglob("*.py")),
                *sorted((ROOT / "src/magicite").rglob("*.json")),
                *sorted((ROOT / "src/magicite").rglob("*.sql")),
                *sorted((ROOT / "tests/fixtures/toy-registry/engrams").glob("*.egr.md")),
                ROOT / "pyproject.toml",
                ROOT / "uv.lock",
                ROOT / "scripts/verify_wheel_install.py",
                ROOT / "scripts/qualify_distributions.py",
                ROOT / "docs/generated/runtime-reference.json",
            ]
        },
        "scope": (
            "local candidate artifacts; generic client and simulated custody; "
            "no publication/operator/deployment/GA qualification"
        ),
    }
    with (output / "build.log").open("w") as log:
        subprocess.run(
            build_command,
            check=True,
            cwd=ROOT,
            env=probe.isolated_subprocess_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "exact artifact pair required"
    artifacts = {"wheel": wheels[0], "sdist": sdists[0]}
    report["artifacts"] = {
        kind: {"name": path.name, "sha256": digest(path), "archive_inventory": check_archive(path)}
        for kind, path in artifacts.items()
    }
    cache = output / "dependency-cache"
    report["probes"] = {}
    for kind, artifact in artifacts.items():
        report["probes"][kind] = probe.run_probe(
            wheel=artifact, keep_env=output / kind, dependency_cache=cache
        )
    assert report["probes"]["wheel"]["python"] != report["probes"]["sdist"]["python"], (
        "distinct install environments required"
    )
    broken = corrupt_resource(wheels[0], output / "corrupt-artifact")
    bait = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    try:
        try:
            probe.run_probe(wheel=broken, keep_env=output / "corrupt-probe", dependency_cache=cache)
        except subprocess.CalledProcessError as exc:
            marker = "missing-installed-resource:engram/schema/engram-1.0.schema.json"
            assert marker in (exc.stderr or ""), "negative control failed for unrelated reason"
            report["negative_control"] = {
                "valid_filename": broken.name == wheels[0].name,
                "artifact_sha256": digest(broken),
                "installed_resource_failure": marker,
                "repository_path_bait_scrubbed": True,
                "outcome": "expected failure",
            }
        else:
            raise AssertionError("broken artifact unexpectedly passed")
    finally:
        if bait is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = bait
    report["source_still_clean"] = not subprocess.check_output(
        ["git", "status", "--porcelain=v1"], cwd=ROOT, text=True
    )
    assert report["source_still_clean"], "probe modified source checkout"
    report["status"] = "PASS"
    report["published_channels"] = {
        key: "UNEVALUATED"
        for key in (
            "PyPI",
            "TestPyPI",
            "pipx",
            "uvx",
            "OCI-fetched",
            "signature-attestation",
            "independent-human",
            "unrun-Python-OS-matrix",
        )
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    paths = [output / "report.json", output / "build.log", *artifacts.values(), broken]
    for directory in (output / "wheel", output / "sdist", output / "corrupt-probe"):
        paths += [directory / name for name in LOG_NAMES if (directory / name).is_file()]
    (output / "artifacts.json").write_text(
        json.dumps({str(path.relative_to(output)): digest(path) for path in paths}, indent=2) + "\n"
    )
    return report


def verify(output: Path) -> dict:
    output = output.resolve()
    report = json.loads((output / "report.json").read_text())
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if (
        report["source_commit"] != current
        or report["status"] != "PASS"
        or subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True)
    ):
        raise ValueError("exact clean passing candidate report required")
    if any(digest(ROOT / name) != expected for name, expected in report["source_input_sha256"].items()):
        raise ValueError("candidate source input changed")
    index = json.loads((output / "artifacts.json").read_text())
    required = {"report.json", "build.log"}
    for kind in ("wheel", "sdist"):
        required.add("dist/" + report["artifacts"][kind]["name"])
        required.update(kind + "/" + name for name in LOG_NAMES)
    required.add("corrupt-artifact/" + report["artifacts"]["wheel"]["name"])
    required.update(
        "corrupt-probe/" + name
        for name in (
            "install.log",
            "prepare-stdout.log",
            "prepare-stderr.log",
            "expected.json",
            "installed-probe.py",
        )
    )
    if set(index) != required:
        raise ValueError("complete bounded artifact evidence coverage required")
    for name, expected in index.items():
        path = (output / name).resolve()
        if not path.is_relative_to(output) or not path.is_file() or digest(path) != expected:
            raise ValueError("artifact evidence binding mismatch")
    assert set(report["probes"]) == set(report["artifacts"]) == {"wheel", "sdist"}, (
        "both artifact rows required"
    )
    prefixes = []
    for kind in ("wheel", "sdist"):
        work = output / kind
        artifact = output / "dist" / report["artifacts"][kind]["name"]
        row = json.loads((work / "result.json").read_text())
        expected = json.loads((work / "expected.json").read_text())
        assert (
            row == report["probes"][kind]
            and row["ok"]
            and row["artifact_sha256"] == digest(artifact) == report["artifacts"][kind]["sha256"]
        ), "artifact/probe mismatch"
        candidate = probe.package_expectations()
        assert all(expected[key] == value for key, value in candidate.items()), (
            "candidate expectations mismatch"
        )
        assert expected["artifact_sha256"] == digest(artifact), "expected artifact binding"
        assert check_archive(artifact) == report["artifacts"][kind]["archive_inventory"], (
            "archive inventory mismatch"
        )
        prefix = (work / "venv").resolve()
        prefixes.append(prefix)
        assert Path(row["pkg_file"]).resolve().is_relative_to(prefix), "installed origin outside environment"
        assert row["runtime"]["prefix"] == str(prefix) and row["runtime"]["isolated_site_packages"], (
            "isolated runtime metadata mismatch"
        )
        assert all(
            not Path(value).is_absolute() and ".." not in Path(value).parts
            for value in row["module_origins"].values()
        ), "module origin escape"
        assert row["protocol"]["server_exit"] == 0 and row["protocol"]["server_module_origins"] == json.loads(
            (work / "server-origins.json").read_text()
        ), "server origin/exit mismatch"
        assert (
            len(row["cli_commands"]) == 3
            and row["module_origins"]
            and row["protocol"]["server_module_origins"]
        ), "observed CLI/origin evidence missing"
        assert set(row["command_records"]) == {"install", "pip_check", "dependencies", "prepare", "server"}, (
            "observed command evidence missing"
        )
        for command in row["cli_commands"]:
            probe.validate_cli_output(command["argv"][1:], command["exit"], command["stdout"], expected)
        assert all(
            not Path(value).is_absolute() and ".." not in Path(value).parts
            for value in row["protocol"]["server_module_origins"].values()
        ), "server module origin escape"
        assert all(command["exit"] == 0 for command in row["command_records"].values()), (
            "observed command failed"
        )
        assert row["command_records"]["install"]["argv"] == row["install_command"] and row["install_command"][
            -1
        ] == str(artifact), "explicit install command mismatch"
        assert (
            row["scrubbed_environment"]["PYTHONPATH"] == ""
            and row["scrubbed_environment"]["PYTHONNOUSERSITE"] == "1"
        ), "ambient source leakage"
        assert (
            row["offline_network_attempts"] == 0
            and "magicite fetch-model" in row["offline_model_error"]["message"]
            and "protected custody enrollment required" in row["missing_custody_error"]["message"]
        ), "installed typed negative missing"
        assert (
            hashlib.sha256(json.dumps(row["dependency_inventory"], sort_keys=True).encode()).hexdigest()
            == row["dependency_inventory_sha256"]
        ), "dependency inventory binding"
        probe.validate_wire(json.loads((work / "wire.json").read_text()), expected)
    assert prefixes[0] != prefixes[1], "independent environment identities required"
    marker = "missing-installed-resource:engram/schema/engram-1.0.schema.json"
    assert (
        marker in (output / "corrupt-probe/prepare-stderr.log").read_text()
        and report["negative_control"]["installed_resource_failure"] == marker
    ), "meaningful broken runtime evidence missing"
    broken = output / "corrupt-artifact" / report["artifacts"]["wheel"]["name"]
    assert digest(broken) == report["negative_control"]["artifact_sha256"], "corrupt artifact binding"
    assert all(value == "UNEVALUATED" for value in report["published_channels"].values()), (
        "unqualified publication promotion"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = verify(args.output) if args.verify else run(args.output)
    print(json.dumps({"status": report["status"], "source_commit": report["source_commit"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
