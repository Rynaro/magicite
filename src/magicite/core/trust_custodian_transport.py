"""Bounded Unix-socket transport for independently held trust authority.

No same-account mode or environment fallback exists. Test doubles must be
explicitly injected by callers rather than weakening this production boundary.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from magicite.core.trust_custodian import CustodianError, CustodianStore, _bytes

RECEIPT_DOMAIN = b"magicite-trust-custodian/1\x00"
MAX_FRAME = 4 * 1024 * 1024
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


def protected_path(path: Path, owner_uid: int, *, directory: bool = False) -> None:
    """All ancestors must prevent the registry principal replacing authority."""
    if not path.is_absolute():
        raise CustodianError("custody path must be absolute")
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in {0, owner_uid}:
            raise CustodianError("unprotected custody path")
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
        except (ValueError, TypeError) as exc:
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
            )
            if profile.custodian_uid != expected_owner_uid or data["minimum_epoch"] != profile.epoch:
                raise CustodianError("protected custody identity mismatch")
            protected_path(profile.socket_path.parent, expected_owner_uid, directory=True)
            return profile
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CustodianError("protected custody profile unavailable") from exc


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise CustodianError("incomplete custodian frame")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_frame(connection: socket.socket) -> dict[str, Any]:
    size = int.from_bytes(_read_exact(connection, 4), "big")
    if not 1 <= size <= MAX_FRAME:
        raise CustodianError("custodian frame exceeds limit")
    try:
        data = json.loads(_read_exact(connection, size))
        if not isinstance(data, dict):
            raise ValueError("object required")
        _bytes(data)
        return data
    except (ValueError, TypeError) as exc:
        raise CustodianError("invalid custodian frame") from exc


def send_frame(connection: socket.socket, data: dict[str, Any]) -> None:
    encoded = _bytes(data)
    if len(encoded) > MAX_FRAME:
        raise CustodianError("custodian frame exceeds limit")
    connection.sendall(len(encoded).to_bytes(4, "big") + encoded)


def sign_receipt(key: Ed25519PrivateKey, payload: dict[str, Any]) -> dict[str, Any]:
    # Copy via encoding so mutable caller structures cannot change a signed value.
    raw = _bytes(payload)
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
            bytes.fromhex(receipt["signature"]), RECEIPT_DOMAIN + _bytes(payload)
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
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        raise CustodianError("invalid custodian receipt") from exc


class CustodianClient:
    def __init__(self, profile: CustodyProfile):
        self.profile = profile

    def call(self, operation: str, **arguments: Any) -> Any:
        profile = self.profile
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
                send_frame(connection, request)
                receipt = receive_frame(connection)
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
        profile = self.profile
        if os.getuid() != profile.custodian_uid:
            raise CustodianError("custodian service identity mismatch")
        connection.settimeout(TIMEOUT)
        check_peer(connection, profile.client_uid)
        request = receive_frame(connection)
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
        ):
            raise CustodianError("invalid custody request binding")
        payload = {k: request[k] for k in fields}
        dispatch: dict[str, Callable[..., Any]] = {
            "read_current": self.store.read_current,
            "register_fence": self.store.register_fence,
            "prepare_record": self.store.prepare_record,
            "commit_record": self.store.commit_record,
            "committed_records": self.store.committed_records,
            "prepared_record": self.store.prepared_record,
        }
        try:
            operation = request["operation"]
            if operation not in dispatch or not isinstance(request.get("arguments"), dict):
                raise CustodianError("unsupported custody operation")
            payload["result"] = dispatch[operation](profile.registry_id, **request["arguments"])
        except (ValueError, TypeError, KeyError):
            payload["error"] = "reconciliation_required"
        send_frame(connection, sign_receipt(self.store.signing_key, payload))

    def serve(self) -> None:
        profile = self.profile
        if os.getuid() != profile.custodian_uid:
            raise CustodianError("custodian service identity mismatch")
        protected_path(self.store.directory, profile.custodian_uid, directory=True)
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
