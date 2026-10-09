"""AC-026: GIVEN the published container image WHEN it is started with
``--cap-drop ALL --security-opt no-new-privileges`` and a mounted project
THEN the MCP handshake SHALL succeed with no network access.

VG-9 (spec §7.2): ``docker build -t magicite:verify . && uv run pytest
tests/acceptance/test_docker_smoke.py`` -- this test assumes the image was
already built by that first half; it does not build it itself (a
multi-minute, network-touching ``docker build`` has no business inside a
pytest collection run). If ``magicite:verify`` does not exist locally, every
test here is skipped with the exact build command to run first -- a missing
prerequisite is never silently reported as a pass.

**Ownership scope:** native Linux mounts retain owner permissions, so a
foreign UID cannot access private custody. Docker Desktop bind sharing can
translate ownership/access; omitting ``--user`` does not universally prevent
boot there. Explicit host UID/GID remains recommended for predictable host
file ownership. The negative test uses native container storage and verifies
the foreign-owner prerequisite, paired with a valid permitted-owner initialize.
All custody here remains simulated and does not qualify deployment custody.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

IMAGE_TAG = "magicite:verify"
_HARDENING_FLAGS = ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
_REPO_TESTS = Path(__file__).resolve().parents[1]
_CONTAINER_SRC = "/opt/magicite-src"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_exists(tag: str) -> bool:
    if not _docker_available():
        return False
    try:
        result = subprocess.run(
            ["docker", "images", "-q", tag], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


_SKIP_REASON = (
    "docker is not installed in this environment"
    if not _docker_available()
    else (
        None
        if _image_exists(IMAGE_TAG)
        else f"{IMAGE_TAG!r} image not built -- run `docker build -t {IMAGE_TAG} .` first (spec §7.2 VG-9)"
    )
)

pytestmark = [
    pytest.mark.acceptance,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]


def _rpc(method: str, params: dict | None = None, *, id: int | None = None) -> bytes:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return (json.dumps(msg) + "\n").encode("utf-8")


@pytest.fixture
def custody(project_root: Path) -> tuple[Path, str]:
    """Simulated custodian on a host mount, enrolled with a reviewed genesis.

    ``serve`` fails closed without protected custody, so the container runs
    the CLI through the fixture launcher. This does not qualify deployment
    custody (separate-UID custodian), which remains UNEVALUATED.
    """
    import hashlib

    from tests.support.custody_adapter import FixtureCustody

    from magicite.config import Config
    from magicite.core.trust_journal import TrustJournal

    parent = project_root.parent / f"{project_root.name}-custody"
    parent.mkdir(mode=0o700)
    registry_id = "docker-smoke-" + hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:24]
    provider = FixtureCustody(parent / "private", registry_id)
    try:
        cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        TrustJournal(cfg.data_dir / "trust/authority", registry_id, provider).initialize_reviewed_genesis()
    finally:
        provider.close()
    return parent / "private", registry_id


async def _spawn_container(
    *,
    project_root: Path,
    custody: tuple[Path, str],
    user: str | None = "host",
    extra_docker_args: list[str] | None = None,
) -> asyncio.subprocess.Process:
    """``user="host"`` (the default) passes ``--user <host-uid>:<host-gid>``
    for predictable host file ownership (see the module docstring).
    ``user=None`` exercises the image's configured default UID."""
    user_args: list[str] = []
    if user == "host":
        user_args = ["--user", f"{os.getuid()}:{os.getgid()}"]
    elif user is not None:
        user_args = ["--user", user]
    custody_directory, registry_id = custody

    args = [
        "docker",
        "run",
        "--rm",
        "-i",
        *_HARDENING_FLAGS,
        # Structural, not just conventional: the network interface is
        # absent, not merely "asked not to be used" -- the strongest proof
        # AC-026's "no network access" clause can get.
        "--network",
        "none",
        *user_args,
        *(extra_docker_args or []),
        "-v",
        f"{project_root}:{project_root}:z",
        "-v",
        f"{custody_directory.parent}:{custody_directory.parent}:z",
        "-v",
        f"{_REPO_TESTS}:{_CONTAINER_SRC}/tests:ro,z",
        "-w",
        str(project_root),
        "--entrypoint",
        "python",
        IMAGE_TAG,
        f"{_CONTAINER_SRC}/tests/support/serve_with_fixture_custody.py",
        str(project_root),
        str(custody_directory),
        registry_id,
        "serve",
        "--project-root",
        str(project_root),
    ]
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _initialize(proc: asyncio.subprocess.Process, *, timeout: float = 60.0) -> dict:
    """First pull of a container image layer can be slow under CI's cold
    cache; a generous timeout here is about Docker cold-start, not about
    the handshake itself (which is sub-second once the process is up)."""
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(
        _rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "vivi-docker-smoke-test", "version": "0.0.1"},
            },
            id=1,
        )
    )
    await proc.stdin.drain()
    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    resp = json.loads(raw)
    proc.stdin.write(_rpc("notifications/initialized"))
    await proc.stdin.drain()
    return resp


async def _call_tool(proc: asyncio.subprocess.Process, name: str, arguments: dict, *, id: int) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(_rpc("tools/call", {"name": name, "arguments": arguments}, id=id))
    await proc.stdin.drain()
    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=60.0)
    return json.loads(raw)


def _review_registered_toys(project_root: Path, custody: tuple[Path, str]) -> None:
    """Operator review out of band; no MCP tool grants admission."""
    from tests.conftest import TOY_ENGRAM_NAMES
    from tests.support.custody_adapter import attach_fixture, review_toy_sources

    from magicite.config import Config
    from magicite.storage import db

    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    with attach_fixture(project_root, *custody):
        conn = db.connect(cfg.db_path)
        try:
            review_toy_sources(cfg, conn, names=list(TOY_ENGRAM_NAMES))
        finally:
            conn.close()


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None and not proc.stdin.is_closing():
        proc.stdin.close()
    if proc.returncode is not None:
        return  # already exited (e.g. the container itself crashed at boot)
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


@pytest.mark.asyncio
async def test_offline_handshake(project_root: Path, custody: tuple[Path, str]) -> None:
    """AC-026: hardened flags + a mounted project + no network -> the
    initialize handshake succeeds. Uses ``--user "$(id -u):$(id -g)"``
    (the module docstring's finding: this is required, not optional, for
    the container to even complete `ensure_dirs()` at boot against a
    normal host-owned mount)."""
    proc = await _spawn_container(project_root=project_root, custody=custody)
    try:
        resp = await _initialize(proc)
        assert "error" not in resp, resp
        assert resp["result"]["serverInfo"]["name"] == "magicite"
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
async def test_offline_register_uses_the_baked_model_with_egress_denied(
    project_root: Path, custody: tuple[Path, str]
) -> None:
    """The fuller R4/AC-026 claim: the fastembed ONNX model baked at build
    time actually embeds real content -- with the network structurally
    denied (--network none) and MAGICITE_EMBEDDING_OFFLINE=1 as the
    image's own default, never overridden to `hashing` here."""
    proc = await _spawn_container(project_root=project_root, custody=custody)
    try:
        resp = await _initialize(proc)
        assert "error" not in resp, resp

        call = await _call_tool(proc, "register", {"path": ".magicite/engrams"}, id=2)
        assert "error" not in call, call
        result = call["result"]
        assert result["isError"] is False, result
        registered = result["structuredContent"]
        assert registered["ingested"] >= 1, registered
        assert len(registered["registered"]) >= 1, registered
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
async def test_offline_register_and_route_cycle(project_root: Path, custody: tuple[Path, str]) -> None:
    """v0.1.0 release verification (VIVI): the AC-026 offline guarantee is
    only as good as what it actually lets a client *do* -- the tests above
    prove the handshake and a bare ``register()`` call, but never a
    ``route()`` (the tool that actually exercises the baked embedding model
    against a live query, end to end). With ``--network none`` and no
    embedding-provider override, THEN a full ``register()`` -> ``route()``
    cycle SHALL complete and return a real candidate ranked against the
    baked ``bge-small-en-v1.5`` model."""
    proc = await _spawn_container(project_root=project_root, custody=custody)
    try:
        resp = await _initialize(proc)
        assert "error" not in resp, resp

        register_call = await _call_tool(proc, "register", {"path": ".magicite/engrams"}, id=2)
        assert "error" not in register_call, register_call
        register_result = register_call["result"]
        assert register_result["isError"] is False, register_result
        assert register_result["structuredContent"]["ingested"] >= 1, register_result
        _review_registered_toys(project_root, custody)

        route_call = await _call_tool(
            proc, "route", {"query": "steam game broke after a proton update"}, id=3
        )
        assert "error" not in route_call, route_call
        route_result = route_call["result"]
        assert route_result["isError"] is False, route_result
        routed = route_result["structuredContent"]
        assert routed["candidates"], "route() returned no candidates against the baked model"
        assert routed["registry_size"] >= 1
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
async def test_uid_override_preserves_host_file_ownership(
    project_root: Path, custody: tuple[Path, str]
) -> None:
    """M7 close-out item #4 (privilege-boundary finding), made mechanical:
    invoking with `--user <host-uid>:<host-gid>` (the house pattern this
    project's own .mcp.json/docs/adapters/claude-code.md use) makes the
    files magicite writes under .magicite/ owned by the SAME OS principal
    as the host user who ran docker -- no privilege boundary between
    client and server, matching FORGE's threat-model assumption."""
    host_uid = os.getuid()
    proc = await _spawn_container(project_root=project_root, custody=custody)
    try:
        resp = await _initialize(proc)
        assert "error" not in resp, resp
        call = await _call_tool(proc, "register", {"path": ".magicite/engrams"}, id=2)
        assert "error" not in call, call
        assert call["result"]["isError"] is False, call["result"]
    finally:
        await _terminate(proc)

    db_path = project_root / ".magicite" / "engrams" / "skill-graph.db"
    assert db_path.is_file(), "register() should have created skill-graph.db on the host mount"
    assert db_path.stat().st_uid == host_uid, (
        f"skill-graph.db is owned by uid {db_path.stat().st_uid}, not the invoking host uid "
        f"{host_uid} -- the --user override should have made the container process (and "
        f"therefore every file it creates) the SAME OS principal as the host client."
    )


@pytest.mark.asyncio
async def test_without_uid_override_the_server_cannot_even_boot(
    project_root: Path, custody: tuple[Path, str]
) -> None:
    """A verified foreign-private native fixture refuses the default UID.

    The same valid fixture must initialize under its owner; Desktop host-bind
    ownership translation is intentionally excluded from this prerequisite.
    """
    import hashlib
    import io
    import stat
    import tarfile
    import uuid

    volume = "magicite-smoke-" + uuid.uuid4().hex
    owner_name, default_name = volume + "-owner", volume + "-default"
    evidence: dict = {}

    def checked(*args: str, payload: bytes | None = None) -> str:
        done = subprocess.run(["docker", *args], input=payload, capture_output=True, timeout=30)
        assert done.returncode == 0, (args, done.returncode, done.stdout, done.stderr)
        return done.stdout.decode()

    custody_directory, registry_id = custody
    native_project, native_custody = "/fixture/project", "/fixture/custody"
    configured_user = checked("image", "inspect", IMAGE_TAG, "--format", "{{.Config.User}}").strip()
    checked("volume", "create", volume)
    created_containers: list[str] = []
    try:
        common = [*_HARDENING_FLAGS, "--network", "none", "-v", f"{volume}:/fixture",
                  "-v", f"{_REPO_TESTS}:{_CONTAINER_SRC}/tests:ro", "--entrypoint", "python"]
        # The host owner reads its seeds; a cap-dropped container root cannot
        # read another Linux UID's private host mount. Stream bytes instead.
        archive_bytes = io.BytesIO()
        inventory: dict[str, str] = {}
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            for prefix, root in (("project", project_root), ("custody", custody_directory)):
                for path in [root, *sorted(root.rglob("*"))]:
                    mode = path.lstat().st_mode
                    assert stat.S_ISDIR(mode) or stat.S_ISREG(mode), path
                    relative = Path(prefix) / path.relative_to(root)
                    assert not relative.is_absolute() and ".." not in relative.parts
                    member = tarfile.TarInfo(relative.as_posix())
                    member.mode = stat.S_IMODE(mode)
                    if stat.S_ISDIR(mode):
                        member.type = tarfile.DIRTYPE
                        archive.addfile(member)
                    else:
                        raw = path.read_bytes()
                        member.size = len(raw)
                        archive.addfile(member, io.BytesIO(raw))
                        inventory[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
        setup = """
import hashlib, io, json, os, pathlib, sys, tarfile
root = pathlib.Path('/fixture').resolve()
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()), mode='r:') as archive:
    for member in archive.getmembers():
        name = pathlib.PurePosixPath(member.name)
        assert not name.is_absolute() and '..' not in name.parts
        assert name.parts and name.parts[0] in {'project', 'custody'}
        assert member.isdir() or member.isreg()
        assert (root / member.name).resolve().is_relative_to(root)
    archive.extractall(root, filter='data')
for name in ('project', 'custody'):
    os.chmod(root / name, 0o700)
print(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for name in ('project', 'custody') for p in sorted((root / name).rglob('*'))
                  if p.is_file()}, sort_keys=True))
"""
        native_inventory = json.loads(checked("run", "--rm", "-i", "--user", "0:0", *common,
                                              IMAGE_TAG, "-c", setup, payload=archive_bytes.getvalue()))
        assert native_inventory == dict(sorted(inventory.items()))
        evidence["host_seed_sha256"] = dict(sorted(inventory.items()))
        evidence["native_seed_sha256"] = native_inventory
        evidence["seed_transport"] = "host-owner tar stdin; data filter; no archived UID restoration"
        probe = (
            "import json,os; p='/fixture/custody';s=os.stat(p);"
            "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),"
            "'custody_uid':s.st_uid,'custody_mode':s.st_mode&511,"
            "'traverse':os.access(p,os.X_OK),'read':os.access(p,os.R_OK),"
            "'key_read':os.access(p+'/journal.key',os.R_OK)}))"
        )
        evidence["configured_user"] = configured_user
        evidence["default_access"] = json.loads(checked("run", "--rm", *common, IMAGE_TAG, "-c", probe))
        access = evidence["default_access"]
        assert configured_user and access["uid"] != access["custody_uid"]
        assert access["custody_uid"] == 0 and access["custody_mode"] == 0o700
        assert not access["traverse"] and not access["read"] and not access["key_read"]
        evidence["owner_access"] = json.loads(checked("run", "--rm", "--user", "0:0", *common,
                                                     IMAGE_TAG, "-c", probe))
        assert evidence["owner_access"]["key_read"]

        async def launch(name: str, owner: bool) -> asyncio.subprocess.Process:
            created_containers.append(name)
            return await asyncio.create_subprocess_exec(
                "docker", "run", "--rm", "--name", name, "-i",
                *(["--user", "0:0"] if owner else []), *common,
                IMAGE_TAG, f"{_CONTAINER_SRC}/tests/support/serve_with_fixture_custody.py",
                native_project, native_custody, registry_id, "serve", "--project-root", native_project,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

        owner = await launch(owner_name, True)
        try:
            response = await _initialize(owner, timeout=30)
            assert response.get("result", {}).get("protocolVersion") == "2025-11-25", response
            stdout, stderr = await asyncio.wait_for(owner.communicate(), timeout=30)
            evidence["owner_initialize"] = response
            evidence["owner_exit"] = owner.returncode
            evidence["owner_stdout"] = stdout.decode(errors="replace")
            evidence["owner_stderr"] = stderr.decode(errors="replace")
            assert owner.returncode == 0, evidence
        finally:
            await _terminate(owner)

        default = await launch(default_name, False)
        try:
            stdout, stderr = await asyncio.wait_for(default.communicate(input=b""), timeout=30)
            evidence["default_exit"] = default.returncode
            evidence["default_stdout"] = stdout.decode(errors="replace")
            evidence["default_stderr"] = stderr.decode(errors="replace")
            assert default.returncode != 0, evidence
            assert "PermissionError" in evidence["default_stderr"], evidence
            assert native_custody + "/journal.key" in evidence["default_stderr"], evidence
            assert "ModuleNotFoundError" not in evidence["default_stderr"], evidence
        finally:
            await _terminate(default)
    finally:
        for name in created_containers:
            done = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30)
            assert done.returncode == 0 or "No such container" in done.stderr, done.stderr
        checked("volume", "rm", volume)
        evidence["owned_volume_removed"] = volume
        (project_root.parent / "native-permission-evidence.json").write_text(json.dumps(evidence, indent=2))
