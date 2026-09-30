"""Snapshot authority anchors from AC-TH-01/03/05/07."""

from __future__ import annotations

import json

import pytest

from magicite.core.trust import TrustDecision, default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal


class InjectedCustody:
    """Explicit unit adapter, never selected by runtime configuration."""

    def __init__(self, store):
        self.store = store

    def call(self, operation, **arguments):
        return getattr(self.store, operation)("registry-one", **arguments)


@pytest.fixture
def journal(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("registry-one", default_policy().to_dict(), actor="operator", reviewed=True)
    journal = TrustJournal(tmp_path / "registry", "registry-one", InjectedCustody(store))
    journal.initialize_reviewed_genesis()
    yield journal, store
    store.close()


def commit(journal, store, identity, action, timestamp):
    policy = default_policy()
    value = TrustDecision(
        decision_id=identity,
        engram_id="subject",
        content_digest="a" * 64,
        decision=action,
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp=timestamp,
    ).to_dict()
    fence = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id=identity,
        holder="lease",
        local_token=1,
    )
    journal.append(
        record_id=identity, kind="trust_decision", payload=value, fence=fence, assert_owned=lambda: None
    )


def test_sequence_wins_over_future_admit_timestamp(journal):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2099-01-01T00:00:00Z")
    commit(ledger, store, "revoke", "revoke", "2020-01-01T00:00:00Z")
    assert ledger.snapshot().latest_by_engram["subject"]["decision"] == "revoke"


def test_whole_local_rollback_against_current_custody_closes(journal):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    old_journal, old_head = ledger.journal_path.read_bytes(), ledger.head_path.read_bytes()
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    ledger.journal_path.write_bytes(old_journal)
    ledger.head_path.write_bytes(old_head)
    with pytest.raises(CustodianError):
        ledger.snapshot()


@pytest.mark.parametrize("mutation", ["delete", "edit", "duplicate", "reorder", "truncate"])
def test_local_journal_mutations_never_read_as_current(journal, mutation):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    rows = ledger.journal_path.read_text().splitlines()
    if mutation == "delete":
        ledger.journal_path.unlink()
    else:
        if mutation == "edit":
            row = json.loads(rows[-1])
            row["payload"]["decision"] = "admit"
            rows[-1] = json.dumps(row)
        elif mutation == "duplicate":
            rows.append(rows[-1])
        elif mutation == "reorder":
            rows.reverse()
        elif mutation == "truncate":
            rows.pop()
        ledger.journal_path.write_text("\n".join(rows) + "\n")
    with pytest.raises(CustodianError):
        ledger.snapshot()


def test_missing_local_tree_cannot_be_implicitly_reenrolled(journal):
    ledger, store = journal
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    ledger.journal_path.unlink()
    ledger.head_path.unlink()
    with pytest.raises(CustodianError):
        ledger.initialize_reviewed_genesis()


def test_pending_preparation_closes_read_until_exact_reconciliation(journal):
    ledger, store = journal
    fence = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id="a",
        holder="lease",
        local_token=1,
    )
    store.prepare_record(
        "registry-one",
        fence=fence,
        expected_head=store.read_current("registry-one"),
        record_id="policy",
        kind="policy_snapshot",
        payload=default_policy().to_dict(),
    )
    with pytest.raises(CustodianError):
        ledger.snapshot()


def test_lost_commit_reply_closes_then_recovers_without_losing_revoke(journal, monkeypatch):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    real_call = ledger.client.call

    def lost_reply(operation, **arguments):
        result = real_call(operation, **arguments)
        if operation == "commit_record":
            raise CustodianError("lost reply")
        return result

    monkeypatch.setattr(ledger.client, "call", lost_reply)
    with pytest.raises(CustodianError):
        commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    monkeypatch.setattr(ledger.client, "call", real_call)
    with pytest.raises(CustodianError):
        ledger.snapshot()
    fence = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id="recovery",
        holder="lease",
        local_token=1,
    )
    recovered = ledger.reconcile(fence=fence, assert_owned=lambda: None)
    assert recovered.latest_by_engram["subject"]["decision"] == "revoke"


def test_pending_exact_record_is_finished_by_new_fence(journal):
    ledger, store = journal
    fence = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id="old",
        holder="lease",
        local_token=1,
    )
    changed = default_policy().to_dict()
    changed["revision"] = 2
    store.prepare_record(
        "registry-one",
        fence=fence,
        expected_head=store.read_current("registry-one"),
        record_id="policy",
        kind="policy_snapshot",
        payload=changed,
    )
    current = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id="new",
        holder="lease",
        local_token=1,
    )
    recovered = ledger.reconcile(fence=current, assert_owned=lambda: None)
    assert recovered.policy["revision"] == 2
    assert recovered.head["pending_record_id"] is None
