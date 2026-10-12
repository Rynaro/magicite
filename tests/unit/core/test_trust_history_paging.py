"""Byte-bound history mechanisms, not production E6 benchmark evidence."""

from __future__ import annotations

import json
import socket
import threading

import pytest

from magicite.core import trust_custodian_transport as wire
from magicite.core.trust import default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal


def test_near_limit_signed_record_message_crosses_bounded_frames():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    payload = {
        "result": "x" * (4 * 1024 * 1024 - 64),
        "nonce": "n" * 64,
        "registry_id": "r",
        "epoch": 1,
        "request_id": "q" * 32,
        "operation": "prepare_record",
    }
    receipt = wire.sign_receipt(key, payload)
    a, b = socket.socketpair()
    errors = []

    def sender():
        try:
            wire.send_message(b, receipt)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=sender)
    worker.start()
    try:
        assert wire.receive_message(a) == receipt
    finally:
        a.close()
        b.close()
        worker.join()
    assert errors == []


def test_history_larger_than_one_frame_roundtrips_with_pinned_pages(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        calls = 0

        def call(self, operation, **arguments):
            result = getattr(store, operation)("r", **arguments)
            # Exercise the actual signed receipt encoding budget for every page.
            wire.sign_receipt(
                store.signing_key,
                {
                    "nonce": "n" * 64,
                    "registry_id": "r",
                    "epoch": 1,
                    "operation": operation,
                    "request_id": "q" * 32,
                    "result": result,
                },
            )
            if operation == "history_page":
                self.calls += 1
            return result

    client = Adapter()
    try:
        fence = store.register_fence(
            "r", predecessor=store.read_current("r"), attempt_id="a", holder="lease", local_token=1
        )
        for revision in range(2, 27):
            payload = default_policy().to_dict()
            payload["revision"] = revision
            payload["scanner_revision"] = "x" * 200_000
            head = store.read_current("r")
            record = store.prepare_record(
                "r",
                fence=fence,
                expected_head=head,
                record_id=str(revision),
                kind="policy_snapshot",
                payload=payload,
            )
            store.commit_record("r", fence=fence, expected_head=head, record=record)
        assert len(json.dumps(store.committed_records("r"))) > 4 * 1024 * 1024
        journal = TrustJournal(tmp_path / "journal", "r", client)
        snapshot = journal.reconcile(fence=fence, assert_owned=lambda: None)
        assert snapshot.policy["revision"] == 26
        assert client.calls > 1
        assert journal.snapshot().head["head_sequence"] == 26
    finally:
        store.close()


@pytest.mark.parametrize("fault", ["offset", "head", "digest"])
def test_changed_history_page_binding_closes(tmp_path, fault):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            result = getattr(store, operation)("r", **arguments)
            if operation == "history_page":
                if fault == "offset":
                    result["offset"] += 1
                if fault == "head":
                    result["head_mac"] = "0" * 64
                if fault == "digest":
                    result["stream_digest"] = "0" * 64
            return result

    try:
        with pytest.raises(CustodianError):
            TrustJournal(tmp_path / "journal", "r", Adapter()).initialize_reviewed_genesis()
    finally:
        store.close()


def test_near_limit_preparation_survives_lost_reply(tmp_path):
    from magicite.core.trust_custodian import MAX_PAYLOAD, _bytes

    store = CustodianStore.create(tmp_path / "custody")
    try:
        store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)
        head = store.read_current("r")
        fence = store.register_fence("r", predecessor=head, attempt_id="a", holder="l", local_token=1)
        payload = default_policy().to_dict()
        payload["revision"] = 2
        payload["scanner_revision"] = ""
        sample = store._record("r", 1, 2, head["head_mac"], "large", "policy_snapshot", payload)
        payload["scanner_revision"] = "x" * (MAX_PAYLOAD - len(_bytes(sample)))
        record = store.prepare_record(
            "r", fence=fence, expected_head=head, record_id="large", kind="policy_snapshot", payload=payload
        )
        assert len(_bytes(record)) == MAX_PAYLOAD
        # Lost response leaves identical durable preparation discoverable by a fresh read.
        assert store.read_current("r")["pending_record_id"] == "large"
        recovered = store.prepared_record("r")
        assert recovered == record
        for operation in ("prepare_record", "prepared_record"):
            receipt = wire.sign_receipt(
                store.signing_key,
                {
                    "result": recovered,
                    "nonce": "a" * 64,
                    "registry_id": "r",
                    "epoch": 1,
                    "operation": operation,
                    "request_id": "b" * 32,
                },
            )
            assert len(wire._message_bytes(receipt)) > wire.MAX_FRAME
            a, b = socket.socketpair()
            worker = threading.Thread(target=wire.send_message, args=(b, receipt))
            worker.start()
            try:
                actual = wire.receive_message(a)
                from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

                public = store.signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
                assert (
                    wire.verify_receipt(
                        actual,
                        public,
                        nonce="a" * 64,
                        registry_id="r",
                        epoch=1,
                        operation=operation,
                        request_id="b" * 32,
                    )
                    == record
                )
            finally:
                a.close()
                b.close()
                worker.join()
        store.commit_record("r", fence=fence, expected_head=head, record=record)
        head = store.read_current("r")
        payload["scanner_revision"] += "x"
        with pytest.raises(CustodianError):
            store.prepare_record(
                "r",
                fence=fence,
                expected_head=head,
                record_id="large2",
                kind="policy_snapshot",
                payload=payload,
            )
        assert store.read_current("r")["pending_record_id"] is None
    finally:
        store.close()


@pytest.mark.parametrize("fault", ["index", "transfer", "size", "digest", "extra", "truncated", "oversize"])
def test_malformed_fragment_rejected_before_dispatch(fault):
    import hashlib
    import struct

    raw = b'{"value":1}'
    transfer = b"x" * 16
    total = len(raw) if fault != "oversize" else wire.MAX_LOGICAL + 1
    header = b"MTC2" + struct.pack("!II", total, 1) + transfer + hashlib.sha256(raw).digest()
    part = (b"y" * 16 if fault == "transfer" else transfer) + struct.pack(
        "!II", 1 if fault == "index" else 0, len(raw) + (1 if fault == "size" else 0)
    )
    data = header + part + (b'{"value":2}' if fault == "digest" else raw)
    if fault == "extra":
        data += b"extra"
    if fault == "truncated":
        data = data[:-1]
    a, b = socket.socketpair()
    try:
        b.sendall(data)
        b.shutdown(socket.SHUT_WR)
        with pytest.raises(CustodianError):
            wire.receive_message(a)
    finally:
        a.close()
        b.close()


def test_logical_message_limit_and_total_deadline(monkeypatch):
    import time

    assert len(wire._message_bytes({"x": "x" * (wire.MAX_LOGICAL - 8)})) == wire.MAX_LOGICAL
    with pytest.raises(CustodianError):
        wire._message_bytes({"x": "x" * (wire.MAX_LOGICAL - 7)})
    monkeypatch.setattr(wire, "TIMEOUT", 0.06)
    a, b = socket.socketpair()

    def slow():
        try:
            for byte in b"MTC2" + b"\0" * 60:
                b.sendall(bytes([byte]))
                time.sleep(0.02)
        except OSError:
            pass

    worker = threading.Thread(target=slow)
    worker.start()
    try:
        with pytest.raises(CustodianError, match="deadline"):
            wire.receive_message(a)
    finally:
        a.close()
        b.close()
        worker.join()
