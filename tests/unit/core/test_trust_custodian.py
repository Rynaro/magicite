"""Anchors for adopted AC-TH-01/02/03/05/06, derived before store implementation."""

from __future__ import annotations

import copy

import pytest

from magicite.core.trust import default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore


@pytest.fixture
def store(tmp_path):
    authority = CustodianStore.create(tmp_path / "custody")
    authority.enroll("registry-one", default_policy().to_dict(), actor="operator", reviewed=True)
    yield authority
    authority.close()


def register(store, attempt="attempt-one", predecessor=None):
    before = predecessor or store.read_current("registry-one")
    return store.register_fence(
        "registry-one", predecessor=before, attempt_id=attempt, holder="existing-lease-holder", local_token=1
    )


def prepare(store, fence, record_id="record-one", payload=None):
    policy = payload or default_policy().to_dict()
    return store.prepare_record(
        "registry-one",
        fence=fence,
        expected_head=store.read_current("registry-one"),
        record_id=record_id,
        kind="policy_snapshot",
        payload=policy,
    )


def test_missing_registry_never_auto_enrolls(tmp_path):
    authority = CustodianStore.create(tmp_path / "custody")
    with pytest.raises(CustodianError):
        authority.read_current("missing")
    authority.close()


def test_explicit_enrollment_is_durable_and_cannot_reset(store):
    before = store.read_current("registry-one")
    assert before["head_sequence"] == 1
    assert before["policy_digest"]
    with pytest.raises(CustodianError):
        store.enroll("registry-one", default_policy().to_dict(), actor="operator", reviewed=True)
    assert store.read_current("registry-one") == before


def test_preparation_does_not_advance_head_or_authorize_reads(store):
    fence = register(store)
    before = store.read_current("registry-one")
    record = prepare(store, fence)
    after = store.read_current("registry-one")
    assert after["head_sequence"] == before["head_sequence"]
    assert after["head_mac"] == before["head_mac"]
    assert after["pending_record_id"] == record["record_id"]


def test_exact_committed_retry_has_one_effect_and_conflict_rejected(store):
    fence = register(store)
    record = prepare(store, fence)
    before = store.read_current("registry-one")
    first = store.commit_record("registry-one", fence=fence, expected_head=before, record=record)
    again = store.commit_record("registry-one", fence=fence, expected_head=before, record=record)
    assert first == again
    assert first["head_sequence"] == 2
    changed = default_policy().to_dict()
    changed["revision"] = 2
    with pytest.raises(CustodianError):
        prepare(store, fence, payload=changed)
    assert len(store.committed_records("registry-one")) == 2


def test_stale_registration_rejected_even_when_head_unchanged(store):
    predecessor = store.read_current("registry-one")
    current = register(store, "newer", predecessor)
    with pytest.raises(CustodianError):
        register(store, "paused-old", predecessor)
    assert store.read_current("registry-one")["fence_generation"] == current["generation"]


def test_same_attempt_retry_cannot_reactivate_superseded_generation(store):
    predecessor = store.read_current("registry-one")
    first = register(store, "first", predecessor)
    assert register(store, "first", predecessor) == first
    register(store, "second")
    with pytest.raises(CustodianError):
        register(store, "first", predecessor)


def test_full_predecessor_compares_head_even_with_same_generation(store):
    fence = register(store)
    old = store.read_current("registry-one")
    record = prepare(store, fence)
    store.commit_record("registry-one", fence=fence, expected_head=old, record=record)
    with pytest.raises(CustodianError):
        register(store, "new-but-stale-head", old)


def test_new_fence_rejects_old_commit_and_can_recover_exact_preparation(store):
    old = register(store, "old")
    record = prepare(store, old)
    head = store.read_current("registry-one")
    current = register(store, "new")
    with pytest.raises(CustodianError):
        store.commit_record("registry-one", fence=old, expected_head=head, record=record)
    final = store.commit_record("registry-one", fence=current, expected_head=head, record=record)
    assert final["head_sequence"] == 2
    assert final["pending_record_id"] is None


@pytest.mark.parametrize(
    "field,value", [("sequence", 99), ("record_id", "other"), ("prev_mac", "0" * 64), ("mac", "0" * 64)]
)
def test_prepared_record_tamper_cannot_advance_custodian(store, field, value):
    fence = register(store)
    record = prepare(store, fence)
    changed = copy.deepcopy(record)
    changed[field] = value
    before = store.read_current("registry-one")
    with pytest.raises(CustodianError):
        store.commit_record("registry-one", fence=fence, expected_head=before, record=changed)
    assert store.read_current("registry-one") == before


def test_prepared_history_survives_store_restart(store):
    fence = register(store)
    record = prepare(store, fence)
    directory = store.directory
    store.close()
    reopened = CustodianStore.open(directory)
    try:
        assert reopened.read_current("registry-one")["pending_record_id"] == record["record_id"]
        assert reopened.prepared_record("registry-one") == record
    finally:
        reopened.close()


def test_old_admission_cannot_be_resequenced_under_new_wrapper_after_revoke(store):
    from magicite.core.trust import TrustDecision

    policy = default_policy()
    fence = register(store)

    def decision(identity, action):
        return TrustDecision(
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

    admit = decision("admit-one", "admit")
    for payload in (admit, decision("revoke-one", "revoke")):
        before = store.read_current("registry-one")
        record = store.prepare_record(
            "registry-one",
            fence=fence,
            expected_head=before,
            record_id=payload["decision_id"],
            kind="trust_decision",
            payload=payload,
        )
        store.commit_record("registry-one", fence=fence, expected_head=before, record=record)
    before = store.read_current("registry-one")
    with pytest.raises(CustodianError):
        store.prepare_record(
            "registry-one",
            fence=fence,
            expected_head=before,
            record_id="replayed-admission",
            kind="trust_decision",
            payload=admit,
        )
    assert store.read_current("registry-one") == before
    assert store.committed_records("registry-one")[-1]["payload"]["decision"] == "revoke"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "not-a-decision"),
        ("content_digest", "not-a-digest"),
        ("source_channel", "untrusted-guess"),
        ("policy_revision", True),
    ],
)
def test_invalid_domain_payload_cannot_be_prepared(store, field, value):
    from magicite.core.trust import TrustDecision

    policy = default_policy()
    payload = TrustDecision(
        decision_id="d",
        engram_id="subject",
        content_digest="a" * 64,
        decision="admit",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
    ).to_dict()
    payload[field] = value
    fence = register(store)
    with pytest.raises(CustodianError):
        store.prepare_record(
            "registry-one",
            fence=fence,
            expected_head=store.read_current("registry-one"),
            record_id="d",
            kind="trust_decision",
            payload=payload,
        )
