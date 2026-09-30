"""Rotation profile/service mechanics with explicit test-only peer/path injection."""

from __future__ import annotations

import json
import os

import pytest

from magicite.core import trust_custodian_transport as transport
from magicite.core.trust import default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore, _bytes


@pytest.fixture
def rotation_service(tmp_path, monkeypatch):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)
    path = tmp_path / "profile.json"
    path.write_bytes(
        _bytes(
            {
                "state": "ACTIVE",
                "registry_id": "r",
                "epoch": 1,
                "minimum_epoch": 1,
                "custodian_uid": os.getuid(),
                "client_uid": os.getuid() + 1,
                "public_key": store.signing_key.public_key().public_bytes_raw().hex(),
                "socket_path": str(tmp_path / "socket"),
            }
        )
    )
    monkeypatch.setattr(transport, "protected_path", lambda *args, **kwargs: None)
    profile = transport.CustodyProfile.load(path, expected_owner_uid=os.getuid())
    service = transport.CustodianService(store, profile)
    yield store, service, path
    store.close()


def test_rotation_service_profile_pending_blocks_normal_loader_and_finishes_exactly(rotation_service):
    store, service, path = rotation_service
    head = store.read_current("r")
    fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="h", local_token=1)
    head = store.read_current("r")
    prepared = service.rotation_operation(
        "rotate_prepare",
        {"fence": fence, "expected_head": head, "transition_id": "rotation", "actor": "operator"},
    )
    assert prepared["phase"] == "PREPARED"
    with pytest.raises(CustodianError):
        transport.CustodyProfile.load(path, expected_owner_uid=os.getuid())
    pending = transport.CustodyProfile.load(path, expected_owner_uid=os.getuid(), maintenance=True)
    assert pending.transition == prepared["transition"]
    committed = service.rotation_operation(
        "rotate_commit", {"fence": fence, "expected_head": head, "transition_id": "rotation"}
    )
    assert committed["phase"] == "COMMITTED"
    with pytest.raises(CustodianError):
        service.rotation_operation(
            "rotate_finish", {"transition_id": "wrong", "expected_head": committed["head"]}
        )
    assert json.loads(path.read_bytes())["state"] == "ROTATION_PENDING"
    result = service.rotation_operation(
        "rotate_finish", {"transition_id": "rotation", "expected_head": committed["head"]}
    )
    active = transport.CustodyProfile.load(path, expected_owner_uid=os.getuid())
    assert active.epoch == 2 and active.transition is None
    assert active.public_key == prepared["transition"]["body"]["new_public_key"]
    assert store.read_current("r") == result


def test_pending_profile_rejects_substituted_transition_pin(rotation_service):
    store, service, path = rotation_service
    head = store.read_current("r")
    fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="h", local_token=1)
    service.rotation_operation(
        "rotate_prepare",
        {
            "fence": fence,
            "expected_head": store.read_current("r"),
            "transition_id": "rotation",
            "actor": "operator",
        },
    )
    value = json.loads(path.read_bytes())
    value["public_key"] = "a" * 64
    path.write_bytes(_bytes(value))
    with pytest.raises(CustodianError):
        transport.CustodyProfile.load(path, expected_owner_uid=os.getuid(), maintenance=True)


def test_profile_write_failure_keeps_authority_closed_and_exact_preparation(rotation_service, monkeypatch):
    from magicite.core import trust_rotation_service

    store, service, path = rotation_service
    head = store.read_current("r")
    fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="h", local_token=1)
    original = trust_rotation_service._write_profile
    monkeypatch.setattr(
        trust_rotation_service, "_write_profile", lambda *a, **k: (_ for _ in ()).throw(OSError("stop"))
    )
    with pytest.raises(OSError):
        service.rotation_operation(
            "rotate_prepare",
            {
                "fence": fence,
                "expected_head": store.read_current("r"),
                "transition_id": "rotation",
                "actor": "operator",
            },
        )
    prepared = store.rotation_status("r")
    assert json.loads(path.read_bytes())["state"] == "ACTIVE"
    with pytest.raises(CustodianError):
        store.read_current("r")
    monkeypatch.setattr(trust_rotation_service, "_write_profile", original)
    assert service.rotation_operation("rotation_status", {}) == prepared
    assert json.loads(path.read_bytes())["state"] == "ROTATION_PENDING"


def test_socket_rotation_receipts_change_signer_only_after_commit(rotation_service, monkeypatch):
    import socket
    import threading

    store, service, _ = rotation_service
    monkeypatch.setattr(transport, "check_peer", lambda *a: None)  # explicit mechanism-only adapter
    old_pin = service.profile.public_key

    def call(operation, arguments, pin):
        request = {
            "version": "trust-custodian/1",
            "nonce": "a" * 64,
            "request_id": "b" * 32,
            "registry_id": "r",
            "epoch": service.profile.epoch,
            "operation": operation,
            "arguments": arguments,
        }
        left, right = socket.socketpair()
        results = []

        def client():
            with right:
                transport.send_message(right, request)
                results.append(transport.receive_message(right))

        thread = threading.Thread(target=client)
        thread.start()
        with left:
            service.handle(left)
        thread.join(timeout=2)
        assert not thread.is_alive()
        return transport.verify_receipt(
            results[0],
            pin,
            nonce=request["nonce"],
            request_id=request["request_id"],
            registry_id="r",
            epoch=request["epoch"],
            operation=operation,
        )

    head = store.read_current("r")
    fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="h", local_token=1)
    head = store.read_current("r")
    status = call(
        "rotate_prepare",
        {"fence": fence, "expected_head": head, "transition_id": "rotation", "actor": "operator"},
        old_pin,
    )
    new_pin = status["transition"]["body"]["new_public_key"]
    with pytest.raises(CustodianError):
        call("read_current", {}, old_pin)
    committed = call(
        "rotate_commit", {"fence": fence, "expected_head": head, "transition_id": "rotation"}, new_pin
    )
    assert committed["head"]["epoch"] == 2
    finished = call(
        "rotate_finish", {"transition_id": "rotation", "expected_head": committed["head"]}, new_pin
    )
    assert call("read_current", {}, new_pin) == finished


def test_crash_after_profile_advance_before_finish_remains_closed_and_resumes(rotation_service, monkeypatch):
    store, service, path = rotation_service
    head = store.read_current("r")
    fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="h", local_token=1)
    head = store.read_current("r")
    service.rotation_operation(
        "rotate_prepare",
        {"fence": fence, "expected_head": head, "transition_id": "rotation", "actor": "operator"},
    )
    committed = service.rotation_operation(
        "rotate_commit", {"fence": fence, "expected_head": head, "transition_id": "rotation"}
    )
    original = store.rotate_finish
    monkeypatch.setattr(store, "rotate_finish", lambda *a, **k: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        service.rotation_operation(
            "rotate_finish", {"transition_id": "rotation", "expected_head": committed["head"]}
        )
    assert json.loads(path.read_bytes())["state"] == "ACTIVE"
    assert json.loads(path.read_bytes())["epoch"] == 2
    with pytest.raises(CustodianError):
        store.read_current("r")
    monkeypatch.setattr(store, "rotate_finish", original)
    service.rotation_operation(
        "rotate_finish", {"transition_id": "rotation", "expected_head": committed["head"]}
    )
    assert store.read_current("r")["epoch"] == 2
