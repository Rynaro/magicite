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


def test_rotation_client_keeps_revoke_and_reconciles_epoch_under_existing_lease(
    rotation_service, tmp_path, monkeypatch
):
    from magicite.config import Config
    from magicite.core.trust_journal import TrustJournal
    from magicite.core.trust_rotation_client import rotate_registry
    from magicite.core.trust_rotation_service import ROTATION_OPERATIONS
    from magicite.storage import db

    store, service, _ = rotation_service
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)

    class Adapter:
        def call(self, operation, **arguments):
            if operation in ROTATION_OPERATIONS:
                return service.rotation_operation(operation, arguments)
            return getattr(store, operation)("r", **arguments)

    client = Adapter()
    journal = TrustJournal(cfg.data_dir / "trust/authority", "r", client)
    journal.initialize_reviewed_genesis()
    from magicite.core import trust, writer_guard

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", client))
    policy = default_policy()
    trust.persist_decision(
        cfg,
        conn,
        trust.TrustDecision(
            decision_id="revoke",
            engram_id="subject",
            content_digest="a" * 64,
            decision="revoke",
            source_channel="local_authored",
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_digest=policy.digest(),
            actor="operator",
            timestamp="2026-09-30T00:00:00Z",
        ),
    )
    try:
        result = rotate_registry(
            cfg, conn, client, registry_id="r", transition_id="operator-rotation", actor="operator"
        )
        assert result["epoch"] == 2
        snapshot = journal.snapshot()
        assert snapshot.head == result
        assert snapshot.records[-1]["kind"] == "epoch_transition"
        assert snapshot.latest_by_engram["subject"]["decision"] == "revoke"
        # Lost completion reply resumes the SAME transition, never creates epoch3.
        repeated = rotate_registry(
            cfg, conn, client, registry_id="r", transition_id="operator-rotation", actor="operator"
        )
        assert repeated["epoch"] == 2
        assert len(journal.snapshot().records) == 3
    finally:
        conn.close()


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit", "before_finish", "after_finish"])
def test_client_resumes_fsynced_preparation_after_commit_interruption(
    rotation_service, tmp_path, monkeypatch, boundary
):
    from magicite.config import Config
    from magicite.core.trust_journal import TrustJournal
    from magicite.core.trust_rotation_client import rotate_registry
    from magicite.core.trust_rotation_service import ROTATION_OPERATIONS
    from magicite.storage import db

    store, service, _ = rotation_service
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)

    class Adapter:
        def call(self, operation, **arguments):
            if operation in ROTATION_OPERATIONS:
                return service.rotation_operation(operation, arguments)
            return getattr(store, operation)("r", **arguments)

    client = Adapter()
    journal = TrustJournal(cfg.data_dir / "trust/authority", "r", client)
    journal.initialize_reviewed_genesis()
    method = "rotate_commit" if boundary.endswith("commit") else "rotate_finish"
    original = getattr(store, method)

    def interrupt(*args, **kwargs):
        if boundary.startswith("after"):
            original(*args, **kwargs)
        raise OSError("interrupt")

    monkeypatch.setattr(store, method, interrupt)
    try:
        with pytest.raises(OSError):
            rotate_registry(cfg, conn, client, registry_id="r", transition_id="resume", actor="operator")
        status = store.rotation_status("r", transition_id="resume")
        expected_phase = (
            "PREPARED"
            if boundary == "before_commit"
            else ("FINISHED" if boundary == "after_finish" else "COMMITTED")
        )
        assert status["phase"] == expected_phase
        assert json.loads(journal.journal_path.read_bytes().splitlines()[-1]) == status["record"]
        monkeypatch.setattr(store, method, original)
        result = rotate_registry(cfg, conn, client, registry_id="r", transition_id="resume", actor="operator")
        assert result["epoch"] == 2
        assert journal.snapshot().records[-1] == status["record"]
    finally:
        conn.close()


def test_real_maintenance_client_verifies_old_then_new_epoch_receipts(rotation_service, monkeypatch):
    import socket
    import tempfile
    import threading
    from pathlib import Path

    store, service, path = rotation_service
    actual_uid = os.getuid()
    main_thread = threading.current_thread()
    monkeypatch.setattr(
        transport.os,
        "getuid",
        lambda: actual_uid if threading.current_thread() is main_thread else actual_uid + 1,
    )
    monkeypatch.setattr(transport, "check_peer", lambda *a: None)  # peer isolation is not qualified here
    temporary = tempfile.TemporaryDirectory(prefix="rotation-wire-", dir="/private/tmp")
    value = json.loads(path.read_bytes())
    value["socket_path"] = str(Path(temporary.name) / "socket")
    path.write_bytes(_bytes(value))
    service.profile = transport.CustodyProfile.load(path, expected_owner_uid=actual_uid)
    client = transport.CustodianClient(service.profile, maintenance=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(service.profile.socket_path))
    listener.listen(1)

    def call(operation, **arguments):
        results, errors = [], []

        def invoke():
            try:
                results.append(client.call(operation, **arguments))
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=invoke)
        thread.start()
        connection, _ = listener.accept()
        with connection:
            service.handle(connection)
        thread.join(timeout=2)
        assert not thread.is_alive()
        if errors:
            raise errors[0]
        return results[0]

    try:
        head = store.read_current("r")
        fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="h", local_token=1)
        head = store.read_current("r")
        prepared = call(
            "rotate_prepare", fence=fence, expected_head=head, transition_id="wire", actor="operator"
        )
        assert prepared["phase"] == "PREPARED"
        committed = call("rotate_commit", fence=fence, expected_head=head, transition_id="wire")
        assert committed["phase"] == "COMMITTED"
        proof = call("rotation_status", transition_id="wire")
        result = call("rotate_finish", transition_id="wire", expected_head=proof["head"])
        assert result["epoch"] == 2
        assert call("read_current")["epoch"] == 2
        assert json.loads(path.read_bytes())["state"] == "ACTIVE"
    finally:
        listener.close()
        temporary.cleanup()


def test_operator_rotate_command_uses_existing_profile_and_registry(rotation_service, tmp_path, monkeypatch):
    from click.testing import CliRunner

    from magicite.config import Config
    from magicite.core.custody_admin import custody_cli
    from magicite.core.trust_journal import TrustJournal
    from magicite.core.trust_rotation_service import ROTATION_OPERATIONS
    from magicite.storage import db

    store, service, _ = rotation_service
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    conn.close()

    class Adapter:
        def call(self, operation, **arguments):
            if operation in ROTATION_OPERATIONS:
                return service.rotation_operation(operation, arguments)
            return getattr(store, operation)("r", **arguments)

    client = Adapter()
    TrustJournal(cfg.data_dir / "trust/authority", "r", client).initialize_reviewed_genesis()
    monkeypatch.setattr(transport.CustodyProfile, "from_enrollment", lambda *a, **k: service.profile)
    monkeypatch.setattr(transport, "CustodianClient", lambda *a, **k: client)
    result = CliRunner().invoke(
        custody_cli,
        ["rotate", "--project-root", str(tmp_path), "--transition-id", "cli-rotation", "--actor", "operator"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["epoch"] == 2
    assert store.read_current("r")["epoch"] == 2
