"""Narrow, stdlib-only elevated installer. Copied to root ownership before use.

Only root runs this module. The client nominates exact, digest-bound installation
bytes; no source builds, network, user import paths, shell strings or project code
are executed here. Protected custody remains owned by a separate OS principal.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import platform
import plistlib
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

RELEASE = "20261009"
PYTHON_VERSION = "3.12.15"
RUNTIMES = {
    "aarch64-apple-darwin": "83dcb04777c798bd95f3687acf9ae84b09860272ee6bdfb05be8bcf9447f75b8",
    "x86_64-apple-darwin": "4c84ef5abe4bc145646175fce9b881936d60126b9a3f9a24c6308e2d035ca5c6",
    "aarch64-unknown-linux-gnu": "b342ccfb577fda349d0237c98b24848234ca767c10f034628a5ff9fc858d662f",
    "x86_64-unknown-linux-gnu": "5864001bf969db329fadc2b654c4878c6027d170c6bd77e28f88666260bcaf63",
}
BASE = Path("/opt/magicite")
STATE_BASE = Path("/var/lib/magicite")
ETC_BASE = Path("/etc/magicite")
DARWIN_UNITS = Path("/Library/LaunchDaemons")
LINUX_UNITS = Path("/etc/systemd/system")
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1"}


def run(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, env=ENV, cwd="/", **kwargs)


def digest(path):
    with open(path, "rb") as stream:
        return (
            hashlib.file_digest(stream, "sha256").hexdigest()
            if hasattr(hashlib, "file_digest")
            else (hashlib.sha256(stream.read()).hexdigest())
        )


def protected(path, uid=0):
    # Canonical OS aliases (/var -> /private/var on macOS) are resolved BEFORE
    # installing fixed paths. User-controlled links are rejected separately.
    from_path = Path(path)
    for current in (from_path, *from_path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022:
            raise RuntimeError("Unprotected installation path: " + str(current))
        if sys.platform.startswith("linux"):
            if any(n.startswith("system.posix_acl_") for n in os.listxattr(current)):
                raise RuntimeError("Extended ACL unsupported: " + str(current))
        elif sys.platform == "darwin":
            result = run(["/bin/ls", "-lde", current], capture_output=True, text=True)
            if len(result.stdout.splitlines()) != 1:
                raise RuntimeError("Extended ACL unsupported: " + str(current))


def mkdir(path, uid=0, mode=0o755):
    if path.exists():
        protected(path, uid)
        if not path.is_dir():
            raise RuntimeError("Expected directory: " + str(path))
        return
    protected(path.parent, uid)
    path.mkdir(mode=mode)
    os.chmod(path, mode)
    os.chown(path, uid, -1)
    protected(path, uid)


def write(path, value, uid=0, mode=0o644):
    if path.exists() or path.is_symlink():
        protected(path, uid)
        if path.read_bytes() != value:
            raise RuntimeError("Conflicting protected file: " + str(path))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(path, uid, -1)


def extract_runtime(archive, destination):
    """Extract finite regular files and safe internal links; no archive owners."""
    destination.mkdir(mode=0o755)
    os.chmod(destination, 0o755)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 100000 or sum(m.size for m in members) > 2 * 1024**3:
            raise RuntimeError("Runtime archive exceeds limits")
        for member in members:
            relative = Path(member.name)
            target = destination / relative
            if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("python",):
                raise RuntimeError("Escaping runtime member")
            if (
                member.mode & 0o6000 or ((member.isdir() or member.isfile()) and member.mode & 0o022)
            ) or not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise RuntimeError("Unsafe runtime archive member")
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Escaping runtime link ancestor")
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
                os.chmod(target, 0o755)
            elif member.isfile():
                with bundle.extractfile(member) as source, open(target, "xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o755)
            else:
                link = Path(member.linkname)
                resolved = (target.parent / link if member.issym() else destination / link).resolve()
                if link.is_absolute() or not resolved.is_relative_to(destination.resolve()):
                    raise RuntimeError("Escaping runtime link")
                if member.issym():
                    target.symlink_to(member.linkname)
                else:
                    os.link(resolved, target)


def remove_stale_socket(path, uid):
    if not path.exists() and not path.is_symlink():
        return
    protected(path.parent, uid)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
        raise RuntimeError("Refusing foreign service socket")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1)
        try:
            client.connect(str(path))
        except OSError as exc:
            if exc.errno != errno.ECONNREFUSED:
                raise RuntimeError("Cannot prove service socket is stale") from exc
        else:
            raise RuntimeError("Custodian is already listening")
    if path.lstat().st_ino != info.st_ino:
        raise RuntimeError("Service socket changed")
    path.unlink()


def identity(host):
    name = "_magicite" if host == "darwin" else "magicite-custody"
    marker = BASE / "identity.json"
    if marker.exists():
        protected(marker)
        receipt = json.loads(marker.read_text())
        if receipt.get("account") != name or receipt.get("home") != "/var/empty":
            raise RuntimeError("Foreign service identity marker")
    else:
        try:
            pwd.getpwnam(name)
        except KeyError:
            occupied = {p.pw_uid for p in pwd.getpwall()}
            uid = next((i for i in range(450, 500) if i not in occupied), None) if host == "darwin" else None
            if host == "darwin" and uid is None:
                raise RuntimeError("No available daemon UID") from None
            receipt = {"account": name, "home": "/var/empty", "uid": uid, "complete": False}
            write(marker, json.dumps(receipt).encode())
        else:
            raise RuntimeError("Existing account is not installer-owned")
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        user = None
    if not receipt["complete"]:
        if host == "darwin":
            if user is not None and user.pw_uid != receipt["uid"]:
                raise RuntimeError("Pending custodian account UID changed")
            if any(p.pw_uid == receipt["uid"] and p.pw_name != name for p in pwd.getpwall()):
                raise RuntimeError("Pending custodian UID is occupied by another account")
            # A pending root receipt pins the intended UID before the first
            # dscl mutation. Reapplying these fields resumes a partial account.
            for key, value in (
                ("UniqueID", str(receipt["uid"])),
                ("PrimaryGroupID", "20"),
                ("UserShell", "/usr/bin/false"),
                ("NFSHomeDirectory", "/var/empty"),
                ("Password", "*"),
                ("IsHidden", "1"),
            ):
                run(["/usr/bin/dscl", ".", "-create", "/Users/" + name, key, value])
        elif user is None:
            run(
                [
                    "/usr/sbin/useradd",
                    "--system",
                    "--no-create-home",
                    "--home-dir",
                    "/var/empty",
                    "--shell",
                    "/usr/sbin/nologin",
                    name,
                ]
            )
        user = pwd.getpwnam(name)
    if (
        user is None
        or not 0 < user.pw_uid < 1000
        or user.pw_dir != "/var/empty"
        or user.pw_shell not in ("/usr/bin/false", "/usr/sbin/nologin", "/sbin/nologin")
        or (receipt["uid"] is not None and user.pw_uid != receipt["uid"])
    ):
        raise RuntimeError("Unsafe custodian identity")
    if not receipt["complete"]:
        receipt.update(uid=user.pw_uid, complete=True)
        temporary = marker.with_suffix(".new")
        write(temporary, json.dumps(receipt).encode())
        os.replace(temporary, marker)
    return user


def service_spec(host, registry_id, python, directory, profile, user):
    argv = [str(python), "-I", "-m", "magicite.install_service", str(directory), str(profile)]
    label = "org.magicite.custody." + registry_id
    if host == "darwin":
        return label, plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": argv,
                "UserName": user,
                "WorkingDirectory": str(BASE),
                "EnvironmentVariables": ENV,
                "RunAtLoad": True,
                "KeepAlive": True,
                "Umask": 0o077,
            }
        )
    # All interpolated fields are installer-generated fixed paths/identifiers.
    text = (
        "[Unit]\nDescription=Magicite protected custody\nAfter=local-fs.target\n[Service]\n"
        "Type=simple\nUser=" + user + "\nWorkingDirectory=" + str(BASE) + "\n"
        "ExecStart=" + " ".join(argv) + "\nEnvironment=PYTHONNOUSERSITE=1\n"
        "Environment=PATH=/usr/bin:/bin\nUMask=0077\nRestart=on-failure\n"
        "NoNewPrivileges=true\nPrivateTmp=true\n[Install]\nWantedBy=multi-user.target\n"
    )
    return label, text.encode()


def provision(manifest, staged):
    if os.getuid() != 0:
        raise RuntimeError("Elevated helper requires root")
    allowed = {
        "project_root",
        "client_uid",
        "actor",
        "target",
        "runtime_sha256",
        "wheels",
        "package_wheel",
        "policy",
        "policy_sha256",
        "accept_policy",
    }
    if set(manifest) != allowed:
        raise RuntimeError("Unexpected provisioning manifest fields")
    host = sys.platform
    if host not in ("darwin", "linux"):
        raise RuntimeError("Unsupported host")
    client = pwd.getpwuid(manifest["client_uid"])
    if client.pw_uid == 0 or manifest["actor"] != client.pw_name:
        raise RuntimeError("Approval actor must match actual nonroot OS identity")
    project = Path(manifest["project_root"])
    if not project.is_absolute() or str(project.resolve()) != str(project):
        raise RuntimeError("Project root must be canonical")
    target = manifest["target"]
    if target not in RUNTIMES or manifest["runtime_sha256"] != RUNTIMES[target]:
        raise RuntimeError("Unrecognized pinned Python artifact")
    expected_os = "apple-darwin" if host == "darwin" else "unknown-linux-gnu"
    machine = "aarch64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    if target != machine + "-" + expected_os:
        raise RuntimeError("Runtime architecture does not match host")
    if not BASE.parent.exists():
        mkdir(BASE.parent)
    protected(BASE.parent)
    mkdir(BASE)
    registry_id = hashlib.sha256(str(project).encode()).hexdigest()
    existing_descriptor = ETC_BASE.parent.resolve() / ETC_BASE.name / "registries" / (registry_id + ".json")
    if manifest["accept_policy"] is not True and not existing_descriptor.exists():
        raise RuntimeError("Explicit reviewed policy approval required before provisioning a new registry")
    policy_bytes = (json.dumps(manifest["policy"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(policy_bytes).hexdigest() != manifest["policy_sha256"]:
        raise RuntimeError("Reviewed policy digest mismatch")
    # Copy every nominated file to a root-owned staging directory before any
    # parser/interpreter handles it. Rehash the copied bytes, not mutable inputs.
    package_sha = hashlib.sha256(json.dumps(manifest["wheels"], sort_keys=True).encode()).hexdigest()
    stage = BASE / ("stage-" + registry_id + "-" + package_sha[:16])
    mkdir(stage)
    files = dict(manifest["wheels"])
    files["python.tar.gz"] = RUNTIMES[target]
    if manifest["package_wheel"] not in manifest["wheels"]:
        raise RuntimeError("Package wheel missing")
    for filename, expected in files.items():
        if Path(filename).name != filename or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise RuntimeError("Invalid staged filename/digest")
        if filename != "python.tar.gz" and not filename.endswith(".whl"):
            raise RuntimeError("Only reviewed wheel artifacts accepted")
        destination = stage / filename
        if destination.exists():
            protected(destination)
            if digest(destination) != expected:
                raise RuntimeError("Conflicting staged artifact")
            continue
        source_fd = os.open(staged / filename, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise RuntimeError("Staged artifact must be regular file")
            with os.fdopen(source_fd, "rb", closefd=False) as source, open(destination, "xb") as output:
                shutil.copyfileobj(source, output)
        finally:
            os.close(source_fd)
        if digest(destination) != expected:
            destination.unlink()
            raise RuntimeError("Staged artifact digest mismatch")
        os.chmod(destination, 0o644)
    runtime = BASE / ("python-" + RELEASE + "-" + target + "-" + package_sha[:16])
    complete = runtime / ".complete"
    if runtime.exists() and not complete.exists():
        protected(runtime)
        shutil.rmtree(runtime)  # exact root-owned incomplete runtime only
    if not runtime.exists():
        extract_runtime(stage / "python.tar.gz", runtime)
        python = (runtime / "python/bin/python3").resolve()
        protected(python)
        run(
            [
                python,
                "-I",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--only-binary=:all:",
                "--find-links",
                stage,
                stage / manifest["package_wheel"],
            ]
        )
        write(complete, b"complete\n")
    python = (runtime / "python/bin/python3").resolve()
    protected(python)
    # Full package tree and interpreter import roots are protected against the
    # client; wheel symlinks are not accepted by pip. Internal runtime symlinks
    # resolve only within the root-owned runtime or protected system paths.
    for node in runtime.rglob("*"):
        protected(node.resolve())
    user = identity(host)
    if user.pw_uid == client.pw_uid:
        raise RuntimeError("Custodian and client must have distinct OS UIDs")
    state_base = STATE_BASE.parent.resolve() / STATE_BASE.name
    if not state_base.parent.exists():
        mkdir(state_base.parent)
    mkdir(state_base)
    directory = state_base / registry_id[:24]
    mkdir(directory)
    private = directory / "private"
    sockets = directory / "socket"
    mkdir(private, user.pw_uid, 0o700)
    mkdir(sockets, user.pw_uid, 0o755)
    store = private / "authority"
    public = directory / "profile"
    mkdir(public, user.pw_uid, 0o755)
    profile = public / "profile.json"
    socket_path = sockets / "custody.sock"
    policy_bytes = (json.dumps(manifest["policy"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(policy_bytes).hexdigest() != manifest["policy_sha256"]:
        raise RuntimeError("Reviewed policy digest mismatch")
    service_python = [
        "/usr/bin/sudo",
        "-u",
        user.pw_name,
        "/usr/bin/env",
        "-i",
        *[k + "=" + v for k, v in ENV.items()],
        str(python),
        "-I",
    ]
    as_user = [*service_python, "-m", "magicite", "custody"]
    existing_descriptor = ETC_BASE.parent.resolve() / ETC_BASE.name / "registries" / (registry_id + ".json")
    if not store.exists():
        if existing_descriptor.exists() or profile.exists():
            raise RuntimeError("Existing authority is missing; use recovery instead of recreating keys")
        run([*as_user, "init", "--directory", store])
    protected(store, user.pw_uid)
    # Status directly through a maintenance store: authenticate prior enrollment
    # before choosing genesis; never reenroll an existing registry.
    check_code = (
        "import json,sys; from pathlib import Path; "
        "from magicite.core.trust_custodian import CustodianStore,CustodianError; "
        "s=CustodianStore.open(Path(sys.argv[1])); "
        "print(json.dumps(s._conn.execute('SELECT id FROM registry').fetchall()))"
    )
    enrolled = run([*service_python, "-c", check_code, store], capture_output=True, text=True)
    ids = json.loads(enrolled.stdout)
    if not ids:
        if existing_descriptor.exists():
            raise RuntimeError("Existing enrollment has lost authority; use recovery")
        if manifest["accept_policy"] is not True or manifest["policy"].get("schema") != "TrustPolicy/1":
            raise RuntimeError("Explicit reviewed policy approval required")
        policy_path = private / "reviewed-policy.json"
        write(policy_path, policy_bytes, user.pw_uid, 0o600)
        run(
            [
                *as_user,
                "enroll",
                "--directory",
                store,
                "--registry-id",
                registry_id,
                "--policy",
                policy_path,
                "--actor",
                manifest["actor"],
                "--reviewed-sha256",
                manifest["policy_sha256"],
            ]
        )
    elif ids != [[registry_id]]:
        raise RuntimeError("Conflicting registry authority")
    if not profile.exists():
        result = run(
            [
                *as_user,
                "profile",
                "--directory",
                store,
                "--registry-id",
                registry_id,
                "--project-root",
                project,
                "--client-uid",
                client.pw_uid,
                "--socket-path",
                socket_path,
                "--profile-path",
                profile,
            ],
            capture_output=True,
            text=True,
        )
        descriptor = json.loads(result.stdout)
    else:
        protected(profile, user.pw_uid)
        p = json.loads(profile.read_text())
        if (p["registry_id"], p["client_uid"], p["socket_path"]) != (
            registry_id,
            client.pw_uid,
            str(socket_path),
        ):
            raise RuntimeError("Conflicting custody profile")
        descriptor = {
            "schema": "CustodyEnrollment/1",
            "project_root": str(project),
            "registry_id": registry_id,
            "custodian_uid": user.pw_uid,
            "client_uid": client.pw_uid,
            "profile_path": str(profile),
        }
    enrollment_dir = ETC_BASE.parent.resolve() / ETC_BASE.name
    mkdir(enrollment_dir)
    mkdir(enrollment_dir / "registries")
    write(
        enrollment_dir / "registries" / (registry_id + ".json"),
        json.dumps(descriptor, sort_keys=True).encode(),
    )
    label, spec = service_spec(host, registry_id, python, store, profile, user.pw_name)
    receipt = directory / "service-install.json"
    prior = None
    if receipt.exists():
        protected(receipt)
        prior = json.loads(receipt.read_text())

    def update_service(unit):
        if unit.exists():
            protected(unit)
            if unit.read_bytes() == spec:
                return False
            if prior is None or hashlib.sha256(unit.read_bytes()).hexdigest() != prior["unit_sha256"]:
                raise RuntimeError("Foreign/edited service configuration")
            replacement = unit.with_suffix(unit.suffix + ".magicite-new")
            write(replacement, spec)
            os.replace(replacement, unit)
            return True
        write(unit, spec)
        return False

    if host == "darwin":
        unit = DARWIN_UNITS / (label + ".plist")
        changed = update_service(unit)
        active = (
            subprocess.run(
                ["/bin/launchctl", "print", "system/" + label], env=ENV, capture_output=True
            ).returncode
            == 0
        )
        if active and changed:
            run(["/bin/launchctl", "bootout", "system/" + label])
            active = False
        if not active:
            run(["/bin/launchctl", "bootstrap", "system", unit])
        run(["/bin/launchctl", "enable", "system/" + label])
    else:
        unit = LINUX_UNITS / (label + ".service")
        changed = update_service(unit)
        run(["/usr/bin/systemctl", "daemon-reload"])
        run(["/usr/bin/systemctl", "enable", "--now", label + ".service"])
        if changed:
            run(["/usr/bin/systemctl", "restart", label + ".service"])
    receipt_new = receipt.with_suffix(".new")
    write(receipt_new, json.dumps({"unit_sha256": hashlib.sha256(spec).hexdigest()}).encode())
    os.replace(receipt_new, receipt)
    return {
        "registry_id": registry_id,
        "service": label,
        "custodian_uid": user.pw_uid,
        "runtime": str(python),
        "enrollment": str(enrollment_dir / "registries" / (registry_id + ".json")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("manifest_sha256")
    args = parser.parse_args()
    path = Path(args.manifest)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != args.manifest_sha256:
        raise RuntimeError("Provisioning manifest changed")
    print(json.dumps(provision(json.loads(raw), path.parent)))


if __name__ == "__main__":
    main()
