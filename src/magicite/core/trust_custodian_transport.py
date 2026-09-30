"""Bounded Unix-socket transport for independently held trust authority.

No same-account mode or environment fallback exists. Test doubles must be
explicitly injected by callers rather than weakening this production boundary.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import socket
import stat
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from magicite.core.trust_custodian import CustodianError, CustodianStore, _bytes

RECEIPT_DOMAIN = b"magicite-trust-custodian/1\x00"
MAX_FRAME = 4 * 1024 * 1024
MAX_LOGICAL = 2 * MAX_FRAME + 64 * 1024
FRAGMENT_BYTES = MAX_FRAME - 24
TIMEOUT = 5.0


def check_peer(connection: socket.socket, expected_uid: int) -> None:
    """Kernel credentials, never a caller-supplied identity claim."""
    if sys.platform.startswith("linux"):
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", raw)
    elif sys.platform == "darwin":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        uid_value = ctypes.c_uint()
        gid_value = ctypes.c_uint()
        if libc.getpeereid(connection.fileno(), ctypes.byref(uid_value), ctypes.byref(gid_value)) != 0:
            raise CustodianError("custodian peer identity unavailable")
        uid = uid_value.value
    else:
        raise CustodianError("custodian peer verification unsupported")
    if uid != expected_uid:
        raise CustodianError("custodian peer identity mismatch")


def _reject_acl(path: Path) -> None:
    """Conservative deployment boundary: extended ACLs require removal/review."""
    if sys.platform == "darwin":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        libc.acl_get_fd_np.restype = ctypes.c_void_p
        libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        libc.acl_to_text.restype = ctypes.c_void_p
        libc.acl_free.argtypes = [ctypes.c_void_p]
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            ctypes.set_errno(0)
            acl = libc.acl_get_fd_np(descriptor, 0x100)  # ACL_TYPE_EXTENDED
            if not acl:
                if ctypes.get_errno() == errno.ENOENT:
                    # Darwin returns ENOENT for absent extended ACL; the open
                    # descriptor proves that this is not an absent pathname.
                    os.fstat(descriptor)
                    return
                raise CustodianError("custody ACL inspection unavailable")
        finally:
            os.close(descriptor)
        text = None
        try:
            text = libc.acl_to_text(acl, None)
            if not text:
                raise CustodianError("custody ACL inspection unavailable")
            lines = ctypes.string_at(text).decode("utf-8").splitlines()
            if any(line.strip() and not line.startswith("!#acl") for line in lines):
                raise CustodianError("extended custody ACL is unsupported")
        finally:
            if text:
                libc.acl_free(text)
            libc.acl_free(acl)
    elif sys.platform.startswith("linux"):
        if any(name.startswith("system.posix_acl_") for name in os.listxattr(path)):
            raise CustodianError("extended custody ACL is unsupported")
    else:
        raise CustodianError("custody path verification unsupported")


def protected_path(path: Path, owner_uid: int, *, directory: bool = False) -> None:
    """All ancestors must prevent the registry principal replacing authority."""
    if not path.is_absolute():
        raise CustodianError("custody path must be absolute")
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in {0, owner_uid}:
            raise CustodianError("unprotected custody path")
        _reject_acl(candidate)
        if info.st_mode & 0o022:
            raise CustodianError("writable custody path")
    if directory and not path.is_dir():
        raise CustodianError("custody directory required")


@dataclass(frozen=True)
class CustodyProfile:
    registry_id: str
    epoch: int
    socket_path: Path
    custodian_uid: int
    client_uid: int
    public_key: str
    profile_path: Path | None = None

    def __post_init__(self) -> None:
        if (
            type(self.custodian_uid) is not int
            or type(self.client_uid) is not int
            or self.custodian_uid < 0
            or self.client_uid < 0
            or self.custodian_uid == self.client_uid
        ):
            raise CustodianError("independent custody requires distinct OS identities")
        if not self.registry_id or type(self.epoch) is not int or self.epoch < 1:
            raise CustodianError("invalid protected enrollment")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public_key))
        except (ValueError, TypeError, RecursionError) as exc:
            raise CustodianError("invalid custodian public pin") from exc

    @classmethod
    def load(cls, path: Path, *, expected_owner_uid: int) -> CustodyProfile:
        # The expected owner comes from the protected deployment entrypoint, never
        # from the unverified profile itself or from a registry-local setting.
        protected_path(path, expected_owner_uid)
        try:
            data = json.loads(path.read_bytes())
            if data.get("state") != "ACTIVE":
                raise CustodianError("custody maintenance requires reconciliation")
            profile = cls(
                registry_id=data["registry_id"],
                epoch=data["epoch"],
                socket_path=Path(data["socket_path"]),
                custodian_uid=data["custodian_uid"],
                client_uid=data["client_uid"],
                public_key=data["public_key"],
                profile_path=path,
            )
            if (expected_owner_uid != 0 and profile.custodian_uid != expected_owner_uid) or data[
                "minimum_epoch"
            ] != profile.epoch:
                raise CustodianError("protected custody identity mismatch")
            protected_path(profile.socket_path.parent, profile.custodian_uid, directory=True)
            return profile
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CustodianError("protected custody profile unavailable") from exc

    @classmethod
    def from_enrollment(cls, path: Path, *, project_root: Path) -> CustodyProfile:
        """Root enrollment delegates only a pinned identity to mutable custody."""
        protected_path(path, 0)
        try:
            descriptor = json.loads(path.read_bytes())
            required = {
                "schema",
                "project_root",
                "registry_id",
                "custodian_uid",
                "client_uid",
                "profile_path",
            }
            if (
                not isinstance(descriptor, dict)
                or set(descriptor) != required
                or descriptor["schema"] != "CustodyEnrollment/1"
                or descriptor["project_root"] != str(project_root.resolve())
                or type(descriptor["custodian_uid"]) is not int
                or type(descriptor["client_uid"]) is not int
            ):
                raise CustodianError("invalid protected enrollment descriptor")
            profile = cls.load(
                Path(descriptor["profile_path"]), expected_owner_uid=descriptor["custodian_uid"]
            )
            if any(
                getattr(profile, key) != descriptor[key]
                for key in ("registry_id", "custodian_uid", "client_uid")
            ):
                raise CustodianError("protected enrollment/profile identity mismatch")
            return profile
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise CustodianError("protected custody enrollment unavailable") from exc

    def refresh(self) -> CustodyProfile:
        if self.profile_path is None:
            return self
        current = self.load(self.profile_path, expected_owner_uid=self.custodian_uid)
        if (current.registry_id, current.custodian_uid, current.client_uid, current.socket_path) != (
            self.registry_id,
            self.custodian_uid,
            self.client_uid,
            self.socket_path,
        ):
            raise CustodianError("protected custody identity changed")
        if current.epoch < self.epoch:
            raise CustodianError("protected custody epoch rollback")
        return current


def _read_exact(connection: socket.socket, size: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CustodianError("custodian frame deadline exceeded")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(size - len(chunks))
        except TimeoutError as exc:
            raise CustodianError("custodian frame deadline exceeded") from exc
        if not chunk:
            raise CustodianError("incomplete custodian frame")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_frame(connection: socket.socket) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT
    size = int.from_bytes(_read_exact(connection, 4, deadline), "big")
    if not 1 <= size <= MAX_FRAME:
        raise CustodianError("custodian frame exceeds limit")
    try:
        data = json.loads(_read_exact(connection, size, deadline))
        if not isinstance(data, dict):
            raise ValueError("object required")
        _bytes(data)
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise CustodianError("invalid custodian frame") from exc


def send_frame(connection: socket.socket, data: dict[str, Any]) -> None:
    encoded = _bytes(data)
    if len(encoded) > MAX_FRAME:
        raise CustodianError("custodian frame exceeds limit")
    connection.sendall(len(encoded).to_bytes(4, "big") + encoded)


def _message_bytes(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise CustodianError("invalid custody message") from exc
    if not 0 < len(raw) <= MAX_LOGICAL:
        raise CustodianError("custody message exceeds limit")
    return raw


def send_message(connection: socket.socket, data: dict[str, Any], *, deadline: float | None = None) -> None:
    deadline = deadline if deadline is not None else time.monotonic() + TIMEOUT
    raw = _message_bytes(data)
    # Fixed binary framing: magic, logical length, fragment count, random
    # transfer ID, digest; then transfer ID/index/length for each raw chunk.
    count = (len(raw) + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
    transfer = secrets.token_bytes(16)
    header = b"MTC2" + struct.pack("!II", len(raw), count) + transfer + hashlib.sha256(raw).digest()

    def send(value: bytes) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CustodianError("custody message deadline exceeded")
        connection.settimeout(remaining)
        try:
            connection.sendall(value)
        except TimeoutError as exc:
            raise CustodianError("custody message deadline exceeded") from exc

    send(header)
    for index in range(count):
        chunk = raw[index * FRAGMENT_BYTES : (index + 1) * FRAGMENT_BYTES]
        send(transfer + struct.pack("!II", index, len(chunk)) + chunk)
    connection.shutdown(socket.SHUT_WR)


def receive_message(connection: socket.socket, *, deadline: float | None = None) -> dict[str, Any]:
    deadline = deadline if deadline is not None else time.monotonic() + TIMEOUT
    header = _read_exact(connection, 60, deadline)
    total, count = struct.unpack("!II", header[4:12])
    if (
        header[:4] != b"MTC2"
        or not 0 < total <= MAX_LOGICAL
        or count != (total + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
    ):
        raise CustodianError("invalid custody message framing")
    transfer, digest = header[12:28], header[28:60]
    raw = bytearray()
    for index in range(count):
        part = _read_exact(connection, 24, deadline)
        actual_index, size = struct.unpack("!II", part[16:24])
        if part[:16] != transfer or actual_index != index or size != min(FRAGMENT_BYTES, total - len(raw)):
            raise CustodianError("invalid custody fragment")
        raw.extend(_read_exact(connection, size, deadline))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CustodianError("custody message deadline exceeded")
    connection.settimeout(remaining)
    try:
        if connection.recv(1):
            raise CustodianError("extra custody fragment")
    except TimeoutError as exc:
        raise CustodianError("custody message deadline exceeded") from exc
    if len(raw) != total or hashlib.sha256(raw).digest() != digest:
        raise CustodianError("invalid custody message digest")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("object required")
        _message_bytes(value)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise CustodianError("invalid custody message") from exc


def sign_receipt(key: Ed25519PrivateKey, payload: dict[str, Any]) -> dict[str, Any]:
    # Copy via encoding so mutable caller structures cannot change a signed value.
    raw = _message_bytes(payload)
    return {"payload": json.loads(raw), "signature": key.sign(RECEIPT_DOMAIN + raw).hex()}


def verify_receipt(
    receipt: dict[str, Any],
    public_key: str,
    *,
    nonce: str,
    registry_id: str,
    epoch: int,
    operation: str,
    request_id: str,
) -> Any:
    try:
        payload = receipt["payload"]
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(receipt["signature"]), RECEIPT_DOMAIN + _message_bytes(payload)
        )
        expected = {
            "nonce": nonce,
            "registry_id": registry_id,
            "epoch": epoch,
            "operation": operation,
            "request_id": request_id,
        }
        if any(_bytes(payload.get(k)) != _bytes(v) for k, v in expected.items()):
            raise CustodianError("custodian receipt binding mismatch")
        if payload.get("error") is not None:
            raise CustodianError("custodian rejected operation")
        return payload["result"]
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise CustodianError("invalid custodian receipt") from exc


class CustodianClient:
    def __init__(self, profile: CustodyProfile):
        self.profile = profile

    def call(self, operation: str, **arguments: Any) -> Any:
        profile = self.profile.refresh()
        self.profile = profile
        if os.getuid() != profile.client_uid:
            raise CustodianError("unauthorized custody client identity")
        nonce, request_id = secrets.token_hex(32), secrets.token_hex(16)
        request = {
            "version": "trust-custodian/1",
            "nonce": nonce,
            "request_id": request_id,
            "registry_id": profile.registry_id,
            "epoch": profile.epoch,
            "operation": operation,
            "arguments": arguments,
        }
        try:
            protected_path(profile.socket_path.parent, profile.custodian_uid, directory=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(TIMEOUT)
                connection.connect(str(profile.socket_path))
                check_peer(connection, profile.custodian_uid)
                deadline = time.monotonic() + TIMEOUT
                send_message(connection, request, deadline=deadline)
                receipt = receive_message(connection, deadline=deadline)
            return verify_receipt(
                receipt,
                profile.public_key,
                nonce=nonce,
                registry_id=profile.registry_id,
                epoch=profile.epoch,
                operation=operation,
                request_id=request_id,
            )
        except (OSError, ValueError) as exc:
            raise CustodianError("custody unavailable; reconciliation required") from exc


class CustodianService:
    """One bounded request per connection; caller runs this as custodian UID.

    Enrollment and key/profile maintenance are administrative store operations,
    deliberately absent from this application socket's dispatch allowlist.
    """

    def __init__(self, store: CustodianStore, profile: CustodyProfile):
        self.store, self.profile = store, profile

    def handle(self, connection: socket.socket) -> None:
        profile = self.profile.refresh()
        self.profile = profile
        if os.getuid() != profile.custodian_uid:
            raise CustodianError("custodian service identity mismatch")
        connection.settimeout(TIMEOUT)
        check_peer(connection, profile.client_uid)
        deadline = time.monotonic() + TIMEOUT
        request = receive_message(connection, deadline=deadline)
        fields = ("nonce", "request_id", "registry_id", "epoch", "operation")
        if (
            request.get("version") != "trust-custodian/1"
            or request.get("registry_id") != profile.registry_id
            or type(request.get("epoch")) is not int
            or request["epoch"] != profile.epoch
            or not isinstance(request.get("nonce"), str)
            or len(request["nonce"]) != 64
            or not isinstance(request.get("request_id"), str)
            or len(request["request_id"]) != 32
            or not isinstance(request.get("operation"), str)
            or not isinstance(request.get("arguments"), dict)
        ):
            raise CustodianError("invalid custody request binding")
        payload = {k: request[k] for k in fields}
        dispatch: dict[str, Callable[..., Any]] = {
            "read_current": self.store.read_current,
            "register_fence": self.store.register_fence,
            "prepare_record": self.store.prepare_record,
            "commit_record": self.store.commit_record,
            "history_page": self.store.history_page,
            "prepared_record": self.store.prepared_record,
        }
        try:
            operation = request["operation"]
            if operation not in dispatch or not isinstance(request.get("arguments"), dict):
                raise CustodianError("unsupported custody operation")
            payload["result"] = dispatch[operation](profile.registry_id, **request["arguments"])
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            payload["error"] = "reconciliation_required"
        send_message(connection, sign_receipt(self.store.signing_key, payload), deadline=deadline)

    def serve(self) -> None:
        profile = self.profile.refresh()
        self.profile = profile
        if os.getuid() != profile.custodian_uid:
            raise CustodianError("custodian service identity mismatch")
        protected_path(self.store.directory, profile.custodian_uid, directory=True)
        for filename in ("journal.key", "signing.key", "authority.sqlite"):
            protected_path(self.store.directory / filename, profile.custodian_uid)
            info = (self.store.directory / filename).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise CustodianError("custody state must remain private")
        protected_path(profile.socket_path.parent, profile.custodian_uid, directory=True)
        # Never unlink a caller-selected preexisting path or an active service.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(profile.socket_path))
            os.chmod(profile.socket_path, 0o666)  # kernel peer UID remains authoritative
            listener.listen(8)
            while True:
                connection, _ = listener.accept()
                with connection:
                    try:
                        self.handle(connection)
                    except (CustodianError, OSError):
                        continue
