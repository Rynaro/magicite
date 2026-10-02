"""Platform-adapter fixtures for the custodian peer/ACL boundary.

Witnesses AC-TH-04 VERIFY sub-clause "Linux SO_PEERCRED/macOS getpeereid adapter
fixtures, both-peer UID rejection" (and the unsupported-OS fail-closed clause)
on any host by faking ``sys.platform`` and the kernel-credential primitives.
The AC-TH-04 row as a whole stays UNEVALUATED: the separate-UID deployment has
not been run. These fixtures witness only the adapter-branch sub-clause.
"""

from __future__ import annotations

import ctypes
import socket
import struct
from pathlib import Path
from typing import Any

import pytest

from magicite.core import trust_custodian_transport as transport
from magicite.core.trust_custodian import CustodianError

CUSTODIAN_UID = 4001
CLIENT_UID = 4002


class _PeercredSocket:
    def __init__(self, uid: int) -> None:
        self.uid = uid
        self.calls: list[tuple[int, int, int]] = []

    def getsockopt(self, level: int, opt: int, size: int) -> bytes:
        self.calls.append((level, opt, size))
        return struct.pack("3i", 1234, self.uid, 77)


class _FdSocket:
    def fileno(self) -> int:
        return 99


def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport.sys, "platform", "linux")
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)


def _fake_libc(monkeypatch: pytest.MonkeyPatch, uid: int, rc: int = 0) -> list[int]:
    seen: list[int] = []

    class Libc:
        def getpeereid(self, fd: int, uid_ref: Any, gid_ref: Any) -> int:
            seen.append(fd)
            uid_ref._obj.value = uid
            gid_ref._obj.value = 5
            return rc

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: Libc())
    return seen


@pytest.mark.parametrize("expected", [CUSTODIAN_UID, CLIENT_UID])
def test_linux_so_peercred_accepts_matching_uid_for_both_peer_roles(
    monkeypatch: pytest.MonkeyPatch, expected: int
) -> None:
    """AC-TH-04 'Linux SO_PEERCRED adapter fixtures': kernel uid match is accepted (positive control)."""
    _linux(monkeypatch)
    sock = _PeercredSocket(expected)
    transport.check_peer(sock, expected)  # type: ignore[arg-type]
    assert sock.calls == [(socket.SOL_SOCKET, 17, struct.calcsize("3i"))]


@pytest.mark.parametrize(("actual", "expected"), [(CLIENT_UID, CUSTODIAN_UID), (CUSTODIAN_UID, CLIENT_UID)])
def test_linux_so_peercred_rejects_uid_mismatch_both_directions(
    monkeypatch: pytest.MonkeyPatch, actual: int, expected: int
) -> None:
    """AC-TH-04 'both-peer UID rejection': client-side and server-side mismatch."""
    _linux(monkeypatch)
    with pytest.raises(CustodianError, match="peer identity mismatch"):
        transport.check_peer(_PeercredSocket(actual), expected)  # type: ignore[arg-type]


@pytest.mark.parametrize("expected", [CUSTODIAN_UID, CLIENT_UID])
def test_macos_getpeereid_accepts_matching_uid(monkeypatch: pytest.MonkeyPatch, expected: int) -> None:
    """AC-TH-04 'macOS getpeereid adapter fixtures': match accepted via fileno-based getpeereid."""
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    seen = _fake_libc(monkeypatch, expected)
    transport.check_peer(_FdSocket(), expected)  # type: ignore[arg-type]
    assert seen == [99]


@pytest.mark.parametrize(("actual", "expected"), [(CLIENT_UID, CUSTODIAN_UID), (CUSTODIAN_UID, CLIENT_UID)])
def test_macos_getpeereid_rejects_uid_mismatch_both_directions(
    monkeypatch: pytest.MonkeyPatch, actual: int, expected: int
) -> None:
    """AC-TH-04 'both-peer UID rejection' on the getpeereid branch."""
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    seen = _fake_libc(monkeypatch, actual)
    with pytest.raises(CustodianError, match="peer identity mismatch"):
        transport.check_peer(_FdSocket(), expected)  # type: ignore[arg-type]
    assert seen == [99]


def test_macos_getpeereid_syscall_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-TH-04 'unavailable transport': getpeereid error never yields acceptance, even if uid would match."""
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    _fake_libc(monkeypatch, CUSTODIAN_UID, rc=-1)
    with pytest.raises(CustodianError, match="peer identity unavailable"):
        transport.check_peer(_FdSocket(), CUSTODIAN_UID)  # type: ignore[arg-type]


@pytest.mark.parametrize("platform", ["win32", "freebsd14", "openbsd7", "sunos5"])
def test_unsupported_platform_peer_check_fails_closed(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """AC-TH-04 'unsupported on the current OS': no credential source -> reject."""
    monkeypatch.setattr(transport.sys, "platform", platform)
    sock = _PeercredSocket(CUSTODIAN_UID)
    with pytest.raises(CustodianError, match="peer verification unsupported"):
        transport.check_peer(sock, CUSTODIAN_UID)  # type: ignore[arg-type]
    assert sock.calls == []


@pytest.mark.parametrize("xattr", ["system.posix_acl_access", "system.posix_acl_default"])
def test_linux_reject_acl_rejects_posix_acl_xattr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, xattr: str
) -> None:
    """AC-TH-04 'protected-parent/path checks' (Linux ACL branch): POSIX ACL xattr is rejected."""
    monkeypatch.setattr(transport.sys, "platform", "linux")
    asked: list[Path] = []

    def listxattr(path: Path) -> list[str]:
        asked.append(path)
        return ["user.other", xattr]

    monkeypatch.setattr(transport.os, "listxattr", listxattr, raising=False)
    with pytest.raises(CustodianError, match="extended custody ACL"):
        transport._reject_acl(tmp_path)
    assert asked == [tmp_path]


def test_linux_reject_acl_accepts_without_acl_xattr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-TH-04 Linux ACL branch positive control: no ACL xattr accepted."""
    monkeypatch.setattr(transport.sys, "platform", "linux")
    for names in ([], ["user.comment", "security.selinux"]):
        monkeypatch.setattr(transport.os, "listxattr", lambda path, n=names: n, raising=False)
        transport._reject_acl(tmp_path)


def test_unsupported_platform_reject_acl_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-TH-04 'unsupported on the current OS': ACL inspection unavailable."""
    monkeypatch.setattr(transport.sys, "platform", "win32")
    with pytest.raises(CustodianError, match="path verification unsupported"):
        transport._reject_acl(tmp_path)


def test_protected_path_surfaces_linux_acl_rejection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-TH-04 protected-path check: a 0700 own-UID dir is rejected only via ACL xattr."""
    import os

    monkeypatch.setattr(transport.sys, "platform", "linux")
    path = tmp_path / "custody"
    path.mkdir(mode=0o700)
    xattrs = {"value": []}  # type: dict[str, list[str]]
    monkeypatch.setattr(
        transport.os,
        "listxattr",
        lambda p: xattrs["value"] if Path(p) == path else [],
        raising=False,
    )
    xattrs["value"] = ["system.posix_acl_access"]
    with pytest.raises(CustodianError, match="extended custody ACL"):
        transport.protected_path(path, os.getuid(), directory=True)
