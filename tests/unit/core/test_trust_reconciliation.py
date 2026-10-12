"""Frozen TH08 partial-migration closure through authenticated BEGIN/COMPLETE."""

from __future__ import annotations

import hashlib
import json

import pytest

from magicite.core.trust import TrustDecision, default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore, _bytes
from magicite.core.trust_journal import TrustJournal


@pytest.fixture
def planned(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    policy = default_policy()
    store.enroll("r", policy.to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    client = Adapter()
    directory = tmp_path / "local"
    ordinary = TrustJournal(directory, "r", client)
    ordinary.initialize_reviewed_genesis()
    internal = TrustJournal(directory, "r", client, reconciliation_id="migration")
    fence = store.register_fence(
        "r", predecessor=store.read_current("r"), attempt_id="attempt", holder="h", local_token=1
    )
    payloads = []
    for identity, action in (("pending", "pending"), ("revoke", "revoke")):
        payloads.append(
            TrustDecision(
                decision_id=identity,
                engram_id="subject",
                content_digest="a" * 64,
                decision=action,
                source_channel="local_authored",
                policy_id=policy.policy_id,
                policy_revision=policy.revision,
                policy_digest=policy.digest(),
                actor="operator",
                timestamp="2026-09-30T00:00:00Z",
            ).to_dict()
        )
    begin = {
        "schema": "LegacyReconciliation/1",
        "phase": "BEGIN",
        "migration_id": "migration",
        "registry_id": "r",
        "epoch": 1,
        "manifest_digest": "a" * 64,
        "backup_digest": "b" * 64,
        "expected_records": [
            {
                "record_id": p["decision_id"],
                "kind": "trust_decision",
                "payload_digest": hashlib.sha256(_bytes(p)).hexdigest(),
            }
            for p in payloads
        ],
        "targets": [],
    }
    internal.append(
        record_id="legacy-migration-begin",
        kind="legacy_reconciliation",
        payload=begin,
        fence=fence,
        assert_owned=lambda: None,
    )
    yield store, ordinary, internal, fence, begin, payloads
    store.close()


def complete(begin):
    return {
        **{k: v for k, v in begin.items() if k not in {"expected_records", "targets"}},
        "phase": "COMPLETE",
    }


def test_active_gate_closes_normal_snapshot_despite_fake_local_completion(planned):
    store, ordinary, _, _, _, _ = planned
    (ordinary.directory / "complete.json").write_text(json.dumps({"complete": True}))
    with pytest.raises(CustodianError):
        ordinary.snapshot()
    reopened = CustodianStore.open(store.directory)
    try:
        assert reopened.read_current("r")["legacy_reconciliation"]["migration_id"] == "migration"
    finally:
        reopened.close()


def test_plan_rejects_out_of_order_and_unlisted_admission_and_rotation(planned):
    store, _, internal, fence, begin, payloads = planned
    with pytest.raises(CustodianError):
        internal.append(
            record_id="revoke",
            kind="trust_decision",
            payload=payloads[1],
            fence=fence,
            assert_owned=lambda: None,
        )
    with pytest.raises(CustodianError):
        internal.append(
            record_id="admit",
            kind="trust_decision",
            payload={**payloads[0], "decision_id": "admit", "decision": "admit"},
            fence=fence,
            assert_owned=lambda: None,
        )
    with pytest.raises(CustodianError):
        internal.append(
            record_id="legacy-migration-complete",
            kind="legacy_reconciliation",
            payload=complete(begin),
            fence=fence,
            assert_owned=lambda: None,
        )
    with pytest.raises(CustodianError):
        store.rotate_prepare(
            "r",
            fence=fence,
            expected_head=store.read_current("r"),
            transition_id="forbidden",
            actor="operator",
        )
    assert store.read_current("r")["head_sequence"] == 2


def test_exact_resume_and_completion_preserve_restriction_without_admission(planned):
    store, ordinary, internal, fence, begin, payloads = planned
    internal.append(
        record_id="legacy-migration-begin",
        kind="legacy_reconciliation",
        payload=begin,
        fence=fence,
        assert_owned=lambda: None,
    )
    for payload in payloads:
        internal.append(
            record_id=payload["decision_id"],
            kind="trust_decision",
            payload=payload,
            fence=fence,
            assert_owned=lambda: None,
        )
        with pytest.raises(CustodianError):
            ordinary.snapshot()
    internal.append(
        record_id="legacy-migration-complete",
        kind="legacy_reconciliation",
        payload=complete(begin),
        fence=fence,
        assert_owned=lambda: None,
    )
    assert ordinary.snapshot().latest_by_engram["subject"]["decision"] == "revoke"
    assert store.read_current("r")["legacy_reconciliation"] is None
    before = store.read_current("r")["head_sequence"]
    internal.append(
        record_id="pending",
        kind="trust_decision",
        payload=payloads[0],
        fence=fence,
        assert_owned=lambda: None,
    )
    assert ordinary.snapshot().latest_by_engram["subject"]["decision"] == "revoke"
    assert store.read_current("r")["head_sequence"] == before
