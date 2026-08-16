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

**M7 finding (privilege boundary, close-out item #4), discovered by actually
running this suite rather than assumed:** ``mcp/app.py::build_state`` calls
``Config.ensure_dirs()`` unconditionally at *every* server boot -- creating
``.magicite/{archive,approvals,runtime}`` if absent -- so the container needs
WRITE access to the mounted project directory just to complete the
``initialize`` handshake, not only for a later ``register()``/``sync()``
call. A bind mount preserves the HOST's file ownership; a container running
as the image's baked-in default (UID 10001, no ``--user`` override) hits a
bare ``PermissionError`` at boot against any normal host-owned project
directory that isn't pre-chowned/world-writable -- confirmed empirically
below (``test_without_uid_override_the_server_cannot_even_boot``). This
makes ``--user "$(id -u):$(id -g)"`` (the house pattern this project's own
``.mcp.json``/``docs/adapters/claude-code.md`` already use) **mandatory for
this image against a real project, not merely an ownership-hygiene
recommendation** -- every other test below passes it for that reason.
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


async def _spawn_container(
    *,
    project_root: Path,
    user: str | None = "host",
    extra_docker_args: list[str] | None = None,
) -> asyncio.subprocess.Process:
    """``user="host"`` (the default) passes ``--user <host-uid>:<host-gid>``
    -- the REQUIRED invocation against a normal host-owned mount (see the
    module docstring). ``user=None`` deliberately omits ``--user``,
    exercising the image's baked-in default (UID 10001) -- used only by
    the one test that exists to prove that omission breaks server boot."""
    user_args: list[str] = []
    if user == "host":
        user_args = ["--user", f"{os.getuid()}:{os.getgid()}"]
    elif user is not None:
        user_args = ["--user", user]

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
        "-w",
        str(project_root),
        IMAGE_TAG,
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
async def test_offline_handshake(project_root: Path) -> None:
    """AC-026: hardened flags + a mounted project + no network -> the
    initialize handshake succeeds. Uses ``--user "$(id -u):$(id -g)"``
    (the module docstring's finding: this is required, not optional, for
    the container to even complete `ensure_dirs()` at boot against a
    normal host-owned mount)."""
    proc = await _spawn_container(project_root=project_root)
    try:
        resp = await _initialize(proc)
        assert "error" not in resp, resp
        assert resp["result"]["serverInfo"]["name"] == "magicite"
    finally:
        await _terminate(proc)


@pytest.mark.asyncio
async def test_offline_register_uses_the_baked_model_with_egress_denied(project_root: Path) -> None:
    """The fuller R4/AC-026 claim: the fastembed ONNX model baked at build
    time actually embeds real content -- with the network structurally
    denied (--network none) and MAGICITE_EMBEDDING_OFFLINE=1 as the
    image's own default, never overridden to `hashing` here."""
    proc = await _spawn_container(project_root=project_root)
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
async def test_offline_register_and_route_cycle(project_root: Path) -> None:
    """v0.1.0 release verification (VIVI): the AC-026 offline guarantee is
    only as good as what it actually lets a client *do* -- the tests above
    prove the handshake and a bare ``register()`` call, but never a
    ``route()`` (the tool that actually exercises the baked embedding model
    against a live query, end to end). With ``--network none`` and no
    embedding-provider override, THEN a full ``register()`` -> ``route()``
    cycle SHALL complete and return a real candidate ranked against the
    baked ``bge-small-en-v1.5`` model."""
    proc = await _spawn_container(project_root=project_root)
    try:
        resp = await _initialize(proc)
        assert "error" not in resp, resp

        register_call = await _call_tool(proc, "register", {"path": ".magicite/engrams"}, id=2)
        assert "error" not in register_call, register_call
        register_result = register_call["result"]
        assert register_result["isError"] is False, register_result
        assert register_result["structuredContent"]["ingested"] >= 1, register_result

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
async def test_uid_override_preserves_host_file_ownership(project_root: Path) -> None:
    """M7 close-out item #4 (privilege-boundary finding), made mechanical:
    invoking with `--user <host-uid>:<host-gid>` (the house pattern this
    project's own .mcp.json/docs/adapters/claude-code.md use) makes the
    files magicite writes under .magicite/ owned by the SAME OS principal
    as the host user who ran docker -- no privilege boundary between
    client and server, matching FORGE's threat-model assumption."""
    host_uid = os.getuid()
    proc = await _spawn_container(project_root=project_root)
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
async def test_without_uid_override_the_server_cannot_even_boot(project_root: Path) -> None:
    """The empirically-discovered half of the privilege-boundary finding
    (module docstring): WITHOUT --user, the container runs as the image's
    baked-in default (UID 10001). `build_state()` calls
    `Config.ensure_dirs()` unconditionally at boot, and a bind mount
    preserves host ownership -- so against `project_root` (owned by
    whatever host user pytest ran as, NOT uid 10001), directory creation
    hits a bare PermissionError before the server can even accept
    `initialize`. This is a harder failure than a mere file-ownership
    mismatch: the container does not merely leave the wrong owner behind,
    it cannot function at all. `--user "$(id -u):$(id -g)"` is therefore
    REQUIRED for this image against a real, host-owned project directory,
    not an optional hardening nicety."""
    proc = await _spawn_container(project_root=project_root, user=None)  # no --user override
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=30.0)
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
    finally:
        await _terminate(proc)

    assert returncode != 0, (
        "expected the container to fail to boot without --user against a host-owned mount "
        "it does not own (UID 10001 vs the host uid) -- it exited 0 instead. Either the "
        "privilege-boundary finding no longer holds (re-verify against a fresh host uid), or "
        "this environment's tmp_path happens to already be uid-10001-writable."
    )
    assert "PermissionError" in stderr, (
        f"expected a PermissionError from Config.ensure_dirs() in stderr; got:\n{stderr}"
    )
