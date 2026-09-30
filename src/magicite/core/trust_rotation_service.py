"""Custodian-owned durable profile updates for explicit rotation operations."""

from __future__ import annotations

import os
import secrets
from typing import Any

from magicite.core.trust_custodian import HEAD_FIELDS, CustodianError, _bytes, _equal, _match
from magicite.core.trust_rotation import verify_transition_record

ROTATION_OPERATIONS = frozenset(
    {
        "rotation_status",
        "rotate_prepare",
        "rotate_register",
        "rotate_commit",
        "rotate_finish",
    }
)


def _write_profile(service: Any, status: dict[str, Any], *, active: bool) -> None:
    from magicite.core.trust_custodian_transport import protected_path

    profile = service.profile.refresh(maintenance=True)
    if os.getuid() != profile.custodian_uid or profile.profile_path is None:
        raise CustodianError("protected custody maintenance identity required")
    certificate, record = status["transition"], status["record"]
    verify_transition_record(certificate, record)
    body = certificate["body"]
    if body["registry_id"] != profile.registry_id:
        raise CustodianError("rotation registry mismatch")
    old = (body["old_epoch"], body["old_public_key"])
    new = (body["new_epoch"], body["new_public_key"])
    identity = (profile.epoch, profile.public_key)
    if identity not in (old, new) or (
        profile.transition is not None and not _equal(profile.transition, certificate)
    ):
        raise CustodianError("protected rotation pin mismatch")
    if identity == new:
        if profile.transition is not None or status["phase"] != "COMMITTED":
            raise CustodianError("invalid advanced rotation profile")
        return  # Crash after profile fsync, before clearing durable maintenance.
    if active and (
        status["phase"] != "COMMITTED" or not _match(status["head"], body["new_head"], HEAD_FIELDS)
    ):
        raise CustodianError("rotation not durably committed")
    epoch, pin = new if active else old
    value = {
        "state": "ACTIVE" if active else "ROTATION_PENDING",
        "registry_id": profile.registry_id,
        "epoch": epoch,
        "minimum_epoch": epoch,
        "custodian_uid": profile.custodian_uid,
        "client_uid": profile.client_uid,
        "public_key": pin,
        "socket_path": str(profile.socket_path),
    }
    if not active:
        value["transition"] = certificate
    path = profile.profile_path
    protected_path(path, profile.custodian_uid)
    protected_path(path.parent, profile.custodian_uid, directory=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".rotation-" + secrets.token_hex(16)
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=directory)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    service.profile = profile.refresh(maintenance=True)


def rotation_operation(service: Any, operation: str, arguments: dict[str, Any]) -> Any:
    """Only the peer-checked service dispatch calls these maintenance methods."""
    registry, store = service.profile.registry_id, service.store
    if operation not in ROTATION_OPERATIONS:
        raise CustodianError("unsupported rotation operation")
    if operation == "rotate_prepare":
        store.rotate_prepare(registry, **arguments)
    elif operation == "rotation_status":
        if arguments:
            raise CustodianError("unexpected rotation status arguments")
    elif operation == "rotate_register":
        result = store.rotate_register(registry, **arguments)
        _write_profile(service, store.rotation_status(registry), active=False)
        return result
    elif operation == "rotate_commit":
        _write_profile(service, store.rotation_status(registry), active=False)
        store.rotate_commit(registry, **arguments)
    elif operation == "rotate_finish":
        status = store.rotation_status(registry)
        if (
            set(arguments) != {"transition_id", "expected_head"}
            or arguments["transition_id"] != status["transition_id"]
            or not _match(arguments["expected_head"], status["head"], HEAD_FIELDS)
        ):
            raise CustodianError("rotation completion mismatch")
        # Advance the protected pin durably BEFORE reopening ordinary authority.
        _write_profile(service, status, active=True)
        return store.rotate_finish(registry, **arguments)
    status = store.rotation_status(registry)
    _write_profile(service, status, active=False)
    return status
