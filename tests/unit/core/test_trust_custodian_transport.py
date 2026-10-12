"""Production boundary anchors, before the UDS transport implementation."""

from __future__ import annotations

import os
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from magicite.core.trust_custodian import CustodianError
from magicite.core.trust_custodian_transport import (
    CustodyProfile,
    check_peer,
    receive_frame,
    sign_receipt,
    verify_receipt,
)


def test_same_uid_production_profile_denied(tmp_path):
    with pytest.raises(CustodianError):
        CustodyProfile(
            registry_id="r",
            epoch=1,
            socket_path=tmp_path / "socket",
            custodian_uid=os.getuid(),
            client_uid=os.getuid(),
            public_key="00" * 32,
        )


def test_actual_peer_credentials_match_current_uid():
    a, b = socket.socketpair()
    try:
        check_peer(a, os.getuid())
        with pytest.raises(CustodianError):
            check_peer(a, os.getuid() + 10000)
    finally:
        a.close()
        b.close()


def test_oversized_frame_rejected_before_body_read():
    a, b = socket.socketpair()
    try:
        b.sendall((8 * 1024 * 1024).to_bytes(4, "big"))
        with pytest.raises(CustodianError):
            receive_frame(a)
    finally:
        a.close()
        b.close()


def test_receipt_requires_fresh_nonce_identity_epoch_and_signature():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    payload = {
        "nonce": "fresh",
        "registry_id": "r",
        "epoch": 1,
        "operation": "read_current",
        "request_id": "request",
        "result": {"head_sequence": 1},
    }
    receipt = sign_receipt(key, payload)
    assert (
        verify_receipt(
            receipt,
            public,
            nonce="fresh",
            registry_id="r",
            epoch=1,
            operation="read_current",
            request_id="request",
        )
        == payload["result"]
    )
    for changed in (
        {"nonce": "cached"},
        {"registry_id": "other"},
        {"epoch": 2},
        {"operation": "commit_record"},
        {"request_id": "other"},
    ):
        expected = dict(
            nonce="fresh", registry_id="r", epoch=1, operation="read_current", request_id="request"
        )
        expected.update(changed)
        with pytest.raises(CustodianError):
            verify_receipt(receipt, public, **expected)
    receipt["payload"]["result"]["head_sequence"] = 9
    with pytest.raises(CustodianError):
        verify_receipt(
            receipt,
            public,
            nonce="fresh",
            registry_id="r",
            epoch=1,
            operation="read_current",
            request_id="request",
        )


def test_profile_in_writable_project_tree_is_never_trusted(tmp_path):
    from magicite.core.trust_custodian_transport import protected_path

    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    with pytest.raises(CustodianError):
        protected_path(profile, os.getuid() + 10000)


def test_receipt_wrong_key_and_boolean_epoch_are_rejected():
    key = Ed25519PrivateKey.generate()
    payload = {
        "nonce": "fresh",
        "registry_id": "r",
        "epoch": True,
        "operation": "read_current",
        "request_id": "request",
        "result": {"head_sequence": 1},
    }
    receipt = sign_receipt(key, payload)
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    with pytest.raises(CustodianError):
        verify_receipt(
            receipt,
            public,
            nonce="fresh",
            registry_id="r",
            epoch=1,
            operation="read_current",
            request_id="request",
        )
    payload["epoch"] = 1
    receipt = sign_receipt(key, payload)
    other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    with pytest.raises(CustodianError):
        verify_receipt(
            receipt,
            other,
            nonce="fresh",
            registry_id="r",
            epoch=1,
            operation="read_current",
            request_id="request",
        )


def test_production_service_refuses_running_as_client_identity(tmp_path):
    from magicite.core.trust import default_policy
    from magicite.core.trust_custodian import CustodianStore
    from magicite.core.trust_custodian_transport import CustodianService

    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)
    public = store.signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    profile = CustodyProfile(
        registry_id="r",
        epoch=1,
        socket_path=tmp_path / "socket",
        custodian_uid=os.getuid() + 10000,
        client_uid=os.getuid(),
        public_key=public,
    )
    try:
        with pytest.raises(CustodianError):
            CustodianService(store, profile).serve()
        assert not profile.socket_path.exists()
    finally:
        store.close()


def test_missing_operation_is_redacted_protocol_error_not_uncaught_keyerror(tmp_path, monkeypatch):
    from magicite.core import trust_custodian_transport as transport
    from magicite.core.trust_custodian import CustodianStore

    store = CustodianStore.create(tmp_path / "custody")
    public = store.signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    profile = CustodyProfile(
        registry_id="r",
        epoch=1,
        socket_path=tmp_path / "socket",
        custodian_uid=os.getuid(),
        client_uid=os.getuid() + 10000,
        public_key=public,
    )
    monkeypatch.setattr(transport, "check_peer", lambda *args: None)  # explicit credential double only
    a, b = socket.socketpair()
    try:
        transport.send_message(
            b,
            {
                "version": "trust-custodian/1",
                "nonce": "a" * 64,
                "request_id": "b" * 32,
                "registry_id": "r",
                "epoch": 1,
                "arguments": {},
            },
        )
        with pytest.raises(CustodianError):
            transport.CustodianService(store, profile).handle(a)
    finally:
        a.close()
        b.close()
        store.close()


def test_total_frame_deadline_rejects_slow_trickle(monkeypatch):
    import threading
    import time

    from magicite.core import trust_custodian_transport as transport

    monkeypatch.setattr(transport, "TIMEOUT", 0.08)
    a, b = socket.socketpair()

    def trickle():
        try:
            b.sendall((12).to_bytes(4, "big"))
            for byte in b'{"value": 1}':
                b.sendall(bytes([byte]))
                time.sleep(0.03)
        except OSError:
            pass

    worker = threading.Thread(target=trickle)
    worker.start()
    try:
        with pytest.raises(CustodianError):
            transport.receive_frame(a)
    finally:
        a.close()
        b.close()
        worker.join()


def test_acl_inspection_rejects_extended_grant(tmp_path):
    import pwd
    import subprocess
    import sys

    from magicite.core.trust_custodian_transport import _reject_acl

    path = tmp_path / "profile"
    path.write_text("{}")
    _reject_acl(path)
    if sys.platform == "darwin":
        subprocess.run(
            ["chmod", "+a", f"user:{pwd.getpwuid(os.getuid()).pw_name} allow write", str(path)], check=True
        )
        with pytest.raises(CustodianError):
            _reject_acl(path)
    elif sys.platform.startswith("linux"):
        # The ordinary no-ACL path above is real; ACL deployment qualification
        # requires its separate provisioned Linux job, not a fabricated grant.
        assert "system.posix_acl_access" not in os.listxattr(path)


def test_parser_recursion_is_normalized_to_redacted_protocol_error(monkeypatch):
    from magicite.core import trust_custodian_transport as transport

    a, b = socket.socketpair()

    def exhausted_parser(*args, **kwargs):
        raise RecursionError("nested adversarial JSON")

    b.sendall((2).to_bytes(4, "big") + b"{}")
    monkeypatch.setattr(transport.json, "loads", exhausted_parser)
    try:
        with pytest.raises(CustodianError):
            transport.receive_frame(a)
    finally:
        a.close()
        b.close()


def test_descriptor_binds_mutable_profile_identity_and_pending_closes(tmp_path, monkeypatch):
    import json

    from magicite.core import trust_custodian_transport as transport

    # Explicit filesystem-protection double tests descriptor semantics only;
    # real ownership/ACL checks have separate negative boundary tests.
    monkeypatch.setattr(transport, "protected_path", lambda *args, **kwargs: None)
    owner, client = os.getuid() + 10000, os.getuid()
    key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    profile_path = tmp_path / "profile.json"
    profile = {
        "state": "ACTIVE",
        "registry_id": "r",
        "epoch": 1,
        "minimum_epoch": 1,
        "socket_path": str(tmp_path / "socket"),
        "custodian_uid": owner,
        "client_uid": client,
        "public_key": key,
    }
    descriptor = {
        "schema": "CustodyEnrollment/1",
        "project_root": str(tmp_path.resolve()),
        "registry_id": "r",
        "custodian_uid": owner,
        "client_uid": client,
        "profile_path": str(profile_path),
    }
    descriptor_path = tmp_path / "enrollment.json"
    descriptor_path.write_text(json.dumps(descriptor))
    profile_path.write_text(json.dumps(profile))
    loaded = CustodyProfile.from_enrollment(descriptor_path, project_root=tmp_path)
    assert loaded.registry_id == "r"
    profile["registry_id"] = "substituted"
    profile_path.write_text(json.dumps(profile))
    with pytest.raises(CustodianError):
        CustodyProfile.from_enrollment(descriptor_path, project_root=tmp_path)
    profile["registry_id"] = "r"
    profile["state"] = "ROTATION_PENDING"
    profile_path.write_text(json.dumps(profile))
    with pytest.raises(CustodianError):
        CustodyProfile.from_enrollment(descriptor_path, project_root=tmp_path)
