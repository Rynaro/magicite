"""Installer acceptance: real bytes/path/socket checks, mocked OS deployment.

No host accounts, root files, launchd units or systemd services are installed.
Actual OS service deployment remains outside this test evidence.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import socket
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from magicite import install
from magicite import install_privileged as privileged

pytestmark = pytest.mark.acceptance


def test_manifest_pins_verified_official_artifacts():
    assert privileged.RELEASE == "20261009"
    assert privileged.PYTHON_VERSION == "3.12.15"
    assert set(privileged.RUNTIMES) == {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "aarch64-unknown-linux-gnu",
        "x86_64-unknown-linux-gnu",
    }
    assert all(len(v) == 64 and int(v, 16) for v in privileged.RUNTIMES.values())


@pytest.mark.parametrize("host", ["darwin", "linux"])
def test_os_service_plans_are_separate_identity_and_protected_runtime(host):
    label, raw = privileged.service_spec(
        host,
        "a" * 64,
        Path("/opt/magicite/python/bin/python"),
        Path("/var/lib/magicite/id/private/authority"),
        Path("/var/lib/magicite/id/profile/profile.json"),
        "_magicite",
    )
    assert label.startswith("org.magicite.custody.")
    if host == "darwin":
        value = plistlib.loads(raw)
        assert value["UserName"] == "_magicite"
        assert value["ProgramArguments"][1:4] == ["-I", "-m", "magicite.install_service"]
        assert value["WorkingDirectory"] == "/opt/magicite"
        assert value["EnvironmentVariables"]["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    else:
        text = raw.decode()
        assert "User=_magicite" in text and "-I -m magicite.install_service" in text
        assert "WorkingDirectory=/opt/magicite" in text
        assert "NoNewPrivileges=true" in text
    assert b"/Users/" not in raw and b"/home/" not in raw


def test_actual_actor_and_exact_default_policy_approval(monkeypatch, capsys):
    monkeypatch.setattr(install.sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="accept-default-policy"):
        install.approved_policy(False, "actual-user")
    value, digest = install.approved_policy(True, "actual-user")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert digest == hashlib.sha256(raw).hexdigest()
    assert value["schema"] == "TrustPolicy/1" and value["roots"] == []
    assert "actual-user" in capsys.readouterr().out


@pytest.mark.parametrize("host,uid,message", [("win32", 501, "supports macOS"), ("linux", 0, "nonroot")])
def test_unsupported_identity_and_platform_fail_before_writes(tmp_path, monkeypatch, host, uid, message):
    monkeypatch.setattr(install.os, "getuid", lambda: uid)
    monkeypatch.setattr(install.sys, "platform", host)
    monkeypatch.setattr(install.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_uid=uid, pw_name="user"))
    with pytest.raises(RuntimeError, match=message):
        install.preflight(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["fetch-model", "sync", "smoke"])
def test_activation_last_and_no_activation_on_prerequisite_failure(tmp_path, monkeypatch, failure):
    trace = []
    monkeypatch.setattr(install, "wait_custody", lambda *args: {"head_sequence": 1})

    def command(argv, **kwargs):
        trace.append(argv[3])
        if argv[3] == failure:
            raise subprocess.CalledProcessError(1, argv)
        return SimpleNamespace(stdout="{}")

    async def smoke(*args):
        trace.append("smoke")
        if failure == "smoke":
            raise RuntimeError("Handshake failed")

    monkeypatch.setattr(install, "run", command)
    monkeypatch.setattr(install, "smoke", smoke)
    with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
        install.finish_client(Path("/client/bin/python"), tmp_path, "all")
    assert trace[0] == "custody" and "setup" not in trace


def test_existing_history_never_reset_and_custom_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGICITE_DATA_DIR", ".custom")
    monkeypatch.setattr(install, "wait_custody", lambda *args: {"head_sequence": 9})
    monkeypatch.setattr(
        install, "run", lambda *args, **kwargs: pytest.fail("must not mutate missing journal")
    )
    with pytest.raises(RuntimeError, match="never reset"):
        install.finish_client(Path("/client/bin/python"), tmp_path, "all")


def test_finish_resumes_existing_journal_and_zero_registry(tmp_path, monkeypatch):
    trace = []
    cfg = install.Config.load(tmp_path)
    journal = cfg.data_dir / "trust/authority/journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("existing history")
    monkeypatch.setattr(install, "wait_custody", lambda *args: {"head_sequence": 9})
    monkeypatch.setattr(install, "run", lambda argv, **kwargs: trace.append(argv[3]))

    async def smoke(*args):
        trace.append("smoke")

    monkeypatch.setattr(install, "smoke", smoke)
    result = install.finish_client(Path("/client/bin/python"), tmp_path, "codex")
    assert trace == ["fetch-model", "sync", "smoke", "setup"]
    assert journal.read_text() == "existing history"
    assert result["runtime_ready"] and result["registry"] == "operational; zero skills"


def test_verified_download_rejects_mismatched_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(install.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong bytes"))
    with pytest.raises(RuntimeError, match="digest mismatch"):
        install.download_runtime("aarch64-apple-darwin", tmp_path / "python.tar.gz")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name,link", [("../escape", None), ("python/escape", "/tmp/escape"), ("python/escape", "../../escape")]
)
def test_archive_escaping_members_refused(tmp_path, name, link):
    archive = tmp_path / "python.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo(name)
        if link:
            info.type, info.linkname = tarfile.SYMTYPE, link
        stream.addfile(info)
    with pytest.raises(RuntimeError, match="Escaping"):
        privileged.extract_runtime(archive, tmp_path / "runtime")


def test_protected_path_refuses_symlink_and_writable_ancestor(tmp_path):
    destination = tmp_path / "unsafe"
    destination.mkdir()
    destination.chmod(0o777)
    with pytest.raises(RuntimeError, match="Unprotected"):
        privileged.protected(destination, os.getuid())
    link = tmp_path / "link"
    link.symlink_to(destination)
    with pytest.raises(RuntimeError, match="Unprotected"):
        privileged.protected(link, os.getuid())


def test_socket_cleanup_only_proven_owned_stale_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(privileged, "protected", lambda *args: None)
    path = Path("/tmp") / ("magicite-test-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16])
    path.write_text("user file")
    with pytest.raises(RuntimeError, match="foreign"):
        privileged.remove_stale_socket(path, os.getuid())
    assert path.read_text() == "user file"
    path.unlink()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen(1)
        with pytest.raises(RuntimeError, match="already listening"):
            privileged.remove_stale_socket(path, os.getuid())
        assert path.exists()
    privileged.remove_stale_socket(path, os.getuid())
    assert not path.exists()


@pytest.fixture
def isolated_provisioning(tmp_path, monkeypatch):
    base = tmp_path / "opt/magicite"
    base.parent.mkdir()
    state = tmp_path / "var/lib/magicite"
    state.parent.mkdir(parents=True)
    etc = tmp_path / "etc/magicite"
    etc.parent.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    for key, value in (("BASE", base), ("STATE_BASE", state), ("ETC_BASE", etc), ("LINUX_UNITS", units)):
        monkeypatch.setattr(privileged, key, value)
    monkeypatch.setattr(privileged, "protected", lambda *args: None)
    monkeypatch.setattr(privileged.os, "getuid", lambda: 0)
    monkeypatch.setattr(privileged.os, "chown", lambda *args: None)
    monkeypatch.setattr(privileged.sys, "platform", "linux")
    monkeypatch.setattr(privileged.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        privileged.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_uid=501, pw_name="operator")
    )
    user = SimpleNamespace(pw_uid=450, pw_name="magicite-custody")
    monkeypatch.setattr(privileged, "identity", lambda host: user)
    staged = tmp_path / "staged"
    staged.mkdir()
    archive = staged / "python.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo("python/bin/python3")
        info.mode, info.size = 0o755, 6
        stream.addfile(info, io.BytesIO(b"python"))
    target = "x86_64-unknown-linux-gnu"
    runtime_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setitem(privileged.RUNTIMES, target, runtime_sha)
    wheel = staged / "magicite-0.3.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    policy = install.default_policy().to_dict()
    manifest = {
        "project_root": str(tmp_path.resolve()),
        "client_uid": 501,
        "actor": "operator",
        "target": target,
        "runtime_sha256": runtime_sha,
        "wheels": {wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()},
        "package_wheel": wheel.name,
        "policy": policy,
        "policy_sha256": hashlib.sha256(
            (json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
        "accept_policy": True,
    }
    history, trace = [], []

    def execute(argv, **kwargs):
        args = list(map(str, argv))
        trace.append(args)
        if "init" in args:
            Path(args[-1]).mkdir()
        elif "-c" in args:
            return SimpleNamespace(stdout=json.dumps(history))
        elif "enroll" in args:
            history.append([args[args.index("--registry-id") + 1]])
        elif "profile" in args:
            profile = Path(args[args.index("--profile-path") + 1])
            value = {
                "registry_id": history[0][0],
                "client_uid": 501,
                "socket_path": args[args.index("--socket-path") + 1],
            }
            profile.write_text(json.dumps(value))
            descriptor = {
                "schema": "CustodyEnrollment/1",
                "project_root": manifest["project_root"],
                "registry_id": history[0][0],
                "custodian_uid": 450,
                "client_uid": 501,
                "profile_path": str(profile),
            }
            return SimpleNamespace(stdout=json.dumps(descriptor))
        return SimpleNamespace(stdout="{}")

    monkeypatch.setattr(privileged, "run", execute)
    return manifest, staged, trace, history, etc


def test_provision_resume_preserves_enrollment_and_public_profile(isolated_provisioning):
    manifest, staged, trace, history, etc = isolated_provisioning
    first = privileged.provision(manifest, staged)
    assert len(history) == 1
    assert any("enroll" in argv for argv in trace)
    descriptor = json.loads((etc / "registries" / (first["registry_id"] + ".json")).read_text())
    profile = Path(descriptor["profile_path"])
    assert profile.parent.name == "profile" and profile.parent.stat().st_mode & 0o777 == 0o755
    trace.clear()
    manifest["accept_policy"] = False
    second = privileged.provision(manifest, staged)
    assert second == first and not any("init" in a or "enroll" in a or "profile" in a for a in trace)
    assert any("-c" in a and "-m" not in a for a in trace)


def test_new_enrollment_requires_exact_actor_digest_and_approval(isolated_provisioning):
    manifest, staged, trace, _, _ = isolated_provisioning
    manifest["actor"] = "invented"
    with pytest.raises(RuntimeError, match="actual nonroot"):
        privileged.provision(manifest, staged)
    assert not trace
    manifest["actor"] = "operator"
    manifest["accept_policy"] = False
    with pytest.raises(RuntimeError, match="Explicit reviewed"):
        privileged.provision(manifest, staged)
    assert not any("enroll" in a for a in trace)


def test_provision_does_not_replace_edited_unit(isolated_provisioning):
    manifest, staged, trace, _, _ = isolated_provisioning
    first = privileged.provision(manifest, staged)
    unit = privileged.LINUX_UNITS / (first["service"] + ".service")
    unit.write_text("user changed service")
    with pytest.raises(RuntimeError, match="Foreign/edited"):
        privileged.provision(manifest, staged)
    assert unit.read_text() == "user changed service"


def test_wrapper_selects_this_invocation_wheel_and_delegates_full_install(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "install_wrapper", Path(__file__).parents[2] / "scripts/install_magicite.py"
    )
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    target = tmp_path / ".magicite/install-wheels"
    target.mkdir(parents=True)
    (target / "magicite-99-py3-none-any.whl").write_bytes(b"stale unrelated version")
    trace = []

    def execute(argv, **kwargs):
        trace.append(list(map(str, argv)))
        if "wheel" in argv:
            directory = Path(argv[argv.index("--wheel-dir") + 1])
            (directory / "magicite-1-py3-none-any.whl").write_bytes(b"exact requested version")

    monkeypatch.setattr(wrapper.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        wrapper.sys,
        "argv",
        [
            "install_magicite.py",
            "--project-root",
            str(tmp_path),
            "--package",
            "magicite==1",
            "--accept-default-policy",
        ],
    )
    monkeypatch.setattr(wrapper.venv, "create", lambda *args, **kwargs: None)
    monkeypatch.setattr(wrapper.subprocess, "run", execute)
    wrapper.main()
    assert trace[1][-1].endswith("magicite-1-py3-none-any.whl")
    assert trace[2][2] == "magicite.install"
    assert "--accept-default-policy" in trace[2]
    assert "setup" not in trace[2]


def test_declined_policy_stops_before_download_elevation_or_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "preflight", lambda root: {"actor": "real-user"})
    monkeypatch.setattr(install.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(install, "download_runtime", lambda *args: pytest.fail("must not download"))
    monkeypatch.setattr(install, "run", lambda *args, **kwargs: pytest.fail("must not elevate or activate"))
    with pytest.raises(RuntimeError, match="accept-default-policy"):
        install.install(tmp_path, tmp_path / "package.whl", "all", False)


def test_upgrade_changes_runtime_without_reenrollment(isolated_provisioning):
    manifest, staged, trace, history, _ = isolated_provisioning
    first = privileged.provision(manifest, staged)
    trace.clear()
    extra = staged / "dependency-2-py3-none-any.whl"
    extra.write_bytes(b"upgraded dependency")
    manifest["wheels"][extra.name] = hashlib.sha256(extra.read_bytes()).hexdigest()
    second = privileged.provision(manifest, staged)
    assert first["runtime"] != second["runtime"]
    assert first["registry_id"] == second["registry_id"]
    assert len(history) == 1 and not any("enroll" in a or "init" in a for a in trace)
    assert any("restart" in a for a in trace)


def test_interrupted_provisioning_reuses_created_authority(isolated_provisioning, monkeypatch):
    manifest, staged, trace, history, _ = isolated_provisioning
    original = privileged.run

    def interrupt(argv, **kwargs):
        if "profile" in argv:
            raise RuntimeError("simulated interruption after enrollment")
        return original(argv, **kwargs)

    monkeypatch.setattr(privileged, "run", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        privileged.provision(manifest, staged)
    assert len(history) == 1
    trace.clear()
    monkeypatch.setattr(privileged, "run", original)
    privileged.provision(manifest, staged)
    assert len(history) == 1 and not any("init" in a or "enroll" in a for a in trace)


@pytest.mark.parametrize("collision", ["reserved_uid_taken", "target_uid_changed"])
def test_pending_mac_identity_refuses_uid_collision(tmp_path, monkeypatch, collision):
    monkeypatch.setattr(privileged, "BASE", tmp_path)
    monkeypatch.setattr(privileged, "protected", lambda *args: None)
    (tmp_path / "identity.json").write_text(
        json.dumps({"account": "_magicite", "home": "/var/empty", "uid": 450, "complete": False})
    )

    def account(name):
        if collision == "target_uid_changed":
            return SimpleNamespace(pw_uid=451, pw_name=name)
        raise KeyError(name)

    monkeypatch.setattr(privileged.pwd, "getpwnam", account)
    monkeypatch.setattr(privileged.pwd, "getpwall", lambda: [SimpleNamespace(pw_uid=450, pw_name="foreign")])
    monkeypatch.setattr(privileged, "run", lambda *args, **kwargs: pytest.fail("must not mutate OS identity"))
    with pytest.raises(RuntimeError, match="Pending custodian"):
        privileged.identity("darwin")
    assert json.loads((tmp_path / "identity.json").read_text())["complete"] is False


def test_official_runtime_build_modes_are_normalized(tmp_path):
    archive = tmp_path / "python.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        for name, mode in (("python/bin/python3.12", 0o775), ("python/lib/example.py", 0o664)):
            member = tarfile.TarInfo(name)
            member.mode, member.size = mode, 4
            stream.addfile(member, io.BytesIO(b"data"))
    root = tmp_path / "runtime"
    privileged.extract_runtime(archive, root)
    assert (root / "python/bin/python3.12").stat().st_mode & 0o777 == 0o755
    assert (root / "python/lib/example.py").stat().st_mode & 0o777 == 0o644
    assert all(not (node.stat().st_mode & 0o022) for node in root.rglob("*"))
