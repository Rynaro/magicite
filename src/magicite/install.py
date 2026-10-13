"""Resumable supported installer: protected custody, client runtime, live MCP.

Privileged provisioning is opt-in to this installer command; ordinary setup and
server startup never install accounts or alter trust. Harness activation is last.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from magicite.config import Config
from magicite.core.trust import default_policy
from magicite.install_privileged import PYTHON_VERSION, RELEASE, RUNTIMES, extract_runtime, protected

ENV_DROP = ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__")


def environment():
    return {k: v for k, v in os.environ.items() if k not in ENV_DROP}


def run(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, env=environment(), **kwargs)


def preflight(root: Path):
    if os.getuid() == 0:
        raise RuntimeError(
            "Run the installer as the actual nonroot harness user; it requests sudo for custody"
        )
    user = pwd.getpwuid(os.getuid())
    if sys.platform == "darwin":
        host = "apple-darwin"
        if not Path("/bin/launchctl").is_file():
            raise RuntimeError("Operational launchd is required")
    elif sys.platform == "linux":
        host = "unknown-linux-gnu"
        if platform.libc_ver()[0] != "glibc":
            raise RuntimeError("Only glibc Linux is supported; musl is unsupported")
        if not Path("/run/systemd/system").is_dir() or not shutil.which("systemctl"):
            raise RuntimeError(
                "An operational systemd host is required (containers without systemd unsupported)"
            )
    else:
        raise RuntimeError("Automated custody installation supports macOS launchd and Linux systemd only")
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(
        platform.machine()
    )
    target = str(machine) + "-" + host
    if target not in RUNTIMES:
        raise RuntimeError("Unsupported Python runtime architecture")
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Project root must be an existing directory")
    for path in (root / ".magicite", root / ".magicite/venv"):
        if path.is_symlink():
            raise RuntimeError("Refusing symlinked installation directory")
    # A root-owned bootstrap interpreter needs stdlib 3.9+, but not package
    # dependencies. It never becomes the custodian's mutable Python runtime.
    bootstrap = Path("/usr/bin/python3").resolve()
    protected(bootstrap)
    roots = run(
        [bootstrap, "-I", "-c", "import json,sys; print(json.dumps(sys.path))"],
        capture_output=True,
        text=True,
    )
    for import_path in json.loads(roots.stdout):
        path = Path(import_path)
        protected(path if path.exists() else path.parent)
    info = run(
        [bootstrap, "-I", "-c", "import json,sys; print(json.dumps(list(sys.version_info[:2])))"],
        capture_output=True,
        text=True,
    )
    if tuple(json.loads(info.stdout)) < (3, 9):
        raise RuntimeError("Protected /usr/bin/python3 >=3.9 is required for the bootstrap")
    protected(Path("/opt") if Path("/opt").exists() else Path("/"))
    if not shutil.which("sudo"):
        raise RuntimeError("sudo is required for the reviewed protected installation")
    return {"target": target, "client_uid": user.pw_uid, "actor": user.pw_name, "bootstrap": str(bootstrap)}


def download_runtime(target: str, destination: Path):
    filename = f"cpython-{PYTHON_VERSION}%2B{RELEASE}-{target}-install_only.tar.gz"
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/{filename}"
    temporary = destination.with_suffix(".download")
    try:
        with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as output:
            total = 0
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > 300 * 1024**2:
                    raise RuntimeError("Runtime download exceeds limits")
                output.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != RUNTIMES[target]:
            raise RuntimeError("Pinned Python runtime digest mismatch")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def approved_policy(accept: bool, actor: str):
    value = default_policy().to_dict()
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    print("Initial custody policy (only for a new registry):")
    print(raw.decode(), end="")
    print("SHA256:", digest, "Approving OS user:", actor)
    if not accept:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "Review the printed baseline policy, then use --accept-default-policy explicitly"
            )
        if input("Approve these exact baseline policy bytes for new enrollment? [yes/no] ").strip() != "yes":
            raise RuntimeError("Policy was not approved; no authority or harness was changed")
    return value, digest


async def smoke(command: str, args: list[str]):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async with asyncio.timeout(30):
        async with stdio_client(
            StdioServerParameters(command=command, args=args, env=environment())
        ) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                expected = {"route", "load_skill_body", "signal_use", "signal_outcome"}
                if not expected <= {tool.name for tool in tools.tools}:
                    raise RuntimeError("Installed MCP server lacks required routing tools")


def wait_custody(python: Path, root: Path):
    last = None
    for _ in range(15):
        try:
            completed = run(
                [python, "-m", "magicite", "custody", "status", "--project-root", root],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return json.loads(completed.stdout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(
        "Custodian did not become authenticated and live; rerun installer after service repair"
    ) from last


def finish_client(python: Path, root: Path, harness: str):
    head = wait_custody(python, root)
    data_dir = Config.load(root).data_dir
    journal = data_dir / "trust/authority/journal.jsonl"
    if not journal.exists():
        if head.get("head_sequence") != 1:
            raise RuntimeError(
                "Existing authority history has no local journal; use recovery, never reset genesis"
            )
        run([python, "-m", "magicite", "custody", "initialize-journal", "--project-root", root])
    run([python, "-m", "magicite", "fetch-model"], cwd=root)
    synced = run(
        [python, "-m", "magicite", "sync", "--project-root", root], cwd=root, capture_output=True, text=True
    )
    try:
        outcome = json.loads(synced.stdout)
    except (ValueError, AttributeError, TypeError) as exc:
        raise RuntimeError("Registry sync result could not be verified; harness activation stopped") from exc
    if not isinstance(outcome, dict) or not isinstance(outcome.get("validation_errors"), list):
        raise RuntimeError("Registry sync result is incomplete; harness activation stopped")
    if outcome["validation_errors"]:
        raise RuntimeError(
            "Registry sync reported validation errors; review/enroll existing artifacts "
            "before harness activation"
        )
    args = ["-m", "magicite", "serve", "--project-root", str(root)]
    asyncio.run(smoke(str(python), args))
    # Interpreter is THIS persistent client venv: subprocess activation uses the
    # exact interpreter/argv successfully probed above and preserves opt-outs.
    run([python, "-m", "magicite", "setup", "--project-root", root, "--harness", harness])
    engrams = Config.load(root).registry_dir
    count = len(list(engrams.rglob("*.egr.md"))) if engrams.exists() else 0
    return {
        "runtime_ready": True,
        "mcp_stdio_verified": True,
        "registry_skill_files": count,
        "registry": "operational; zero skills" if not count else "operational",
        "next_step": "Approve/trust Magicite in the harness and restart/reload its session.",
    }


def install(root: Path, package_wheel: Path, harness: str, accept_default_policy: bool):
    root = root.expanduser().resolve()
    identity = preflight(root)
    registry_id = hashlib.sha256(str(root).encode()).hexdigest()
    existing = Path("/etc").resolve() / "magicite/registries" / (registry_id + ".json")
    if existing.exists():
        protected(existing)
        print("Existing protected enrollment is authoritative; preserving its policy and keys.")
        policy = default_policy().to_dict()
        policy_sha = hashlib.sha256(
            (json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        acceptance = False
    else:
        policy, policy_sha = approved_policy(accept_default_policy, identity["actor"])
        acceptance = True
    client_python = Path(sys.executable).absolute()
    print("Preparing pinned protected Python and dependency wheels before requesting elevation...")
    with tempfile.TemporaryDirectory(prefix="magicite-provision-") as temporary:
        stage = Path(temporary)
        archive = stage / "python.tar.gz"
        download_runtime(identity["target"], archive)
        unpacked = stage / "build-python"
        extract_runtime(archive, unpacked)
        builder = (unpacked / "python/bin/python3").resolve()
        wheelhouse = stage / "wheelhouse"
        wheelhouse.mkdir()
        run(
            [
                builder,
                "-I",
                "-m",
                "pip",
                "wheel",
                "--only-binary=:all:",
                "--wheel-dir",
                wheelhouse,
                package_wheel,
            ],
            cwd=stage,
        )
        wheels = {}
        for wheel in sorted(wheelhouse.glob("*.whl")):
            shutil.copyfile(wheel, stage / wheel.name)
            wheels[wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        manifest = {
            "project_root": str(root),
            "client_uid": identity["client_uid"],
            "actor": identity["actor"],
            "target": identity["target"],
            "runtime_sha256": RUNTIMES[identity["target"]],
            "wheels": wheels,
            "package_wheel": package_wheel.name,
            "policy": policy,
            "policy_sha256": policy_sha,
            "accept_policy": acceptance,
        }
        raw = json.dumps(manifest, sort_keys=True).encode()
        manifest_path = stage / "manifest.json"
        manifest_path.write_bytes(raw)
        helper_source = Path(__file__).with_name("install_privileged.py")
        helper_sha = hashlib.sha256(helper_source.read_bytes()).hexdigest()
        helper = Path("/opt/magicite/bootstrap") / (helper_sha + ".py")
        # Only fixed root paths and a stdlib-only helper are elevated. No mutable
        # project package is imported by root or the separate service identity.
        print("Requesting elevation to create protected runtime, dedicated custodian and OS service.")
        run(["sudo", "/bin/mkdir", "-p", helper.parent])
        protected(helper.parent)
        if helper.exists() or helper.is_symlink():
            protected(helper)
            if hashlib.sha256(helper.read_bytes()).hexdigest() != helper_sha:
                raise RuntimeError("Conflicting protected bootstrap helper")
        else:
            run(["sudo", "/usr/bin/install", "-o", "root", "-m", "0644", helper_source, helper])
        protected(helper)
        if hashlib.sha256(helper.read_bytes()).hexdigest() != helper_sha:
            raise RuntimeError("Protected helper changed while staging")
        run(
            [
                "sudo",
                "/usr/bin/env",
                "-i",
                "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
                identity["bootstrap"],
                "-I",
                helper,
                manifest_path,
                hashlib.sha256(raw).hexdigest(),
            ]
        )
    outcome = finish_client(client_python, root, harness)
    print(json.dumps(outcome, indent=2))
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--package-wheel", required=True, type=Path)
    parser.add_argument("--harness", choices=("all", "claude", "codex", "opencode"), default="all")
    parser.add_argument("--accept-default-policy", action="store_true")
    args = parser.parse_args()
    install(args.project_root, args.package_wheel.resolve(), args.harness, args.accept_default_policy)


if __name__ == "__main__":
    main()
