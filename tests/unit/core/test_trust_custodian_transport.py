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
