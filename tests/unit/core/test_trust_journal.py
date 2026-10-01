"""Snapshot authority anchors from AC-TH-01/03/05/07."""

from __future__ import annotations

import json

import pytest

from magicite.core import trust_journal
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


def test_predicted_temporary_symlink_never_overwrites_outside_file(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("registry-one", default_policy().to_dict(), actor="operator", reviewed=True)
    directory = tmp_path / "registry"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (directory / "head.pending").symlink_to(outside)
    ledger = TrustJournal(directory, "registry-one", InjectedCustody(store))
    try:
        ledger.initialize_reviewed_genesis()
        assert outside.read_text() == "untouched"
        assert not ledger.head_path.is_symlink()
    finally:
        store.close()


def test_journal_symlink_cannot_be_appended_through(journal, tmp_path):
    ledger, store = journal
    outside = tmp_path / "outside"
    outside.write_bytes(ledger.journal_path.read_bytes())
    ledger.journal_path.unlink()
    ledger.journal_path.symlink_to(outside)
    before = outside.read_bytes()
    with pytest.raises(CustodianError):
        commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    assert outside.read_bytes() == before


def test_exact_retry_checks_lease_after_remote_preparation(journal, monkeypatch):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    payload = ledger.snapshot().decisions[-1]
    fence = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id="retry",
        holder="lease",
        local_token=1,
    )
    lost = False
    real_call = ledger.client.call

    def lose_after_prepare(operation, **arguments):
        nonlocal lost
        result = real_call(operation, **arguments)
        if operation == "prepare_record":
            lost = True
        return result

    def assert_owned():
        if lost:
            raise CustodianError("lease lost")

    monkeypatch.setattr(ledger.client, "call", lose_after_prepare)
    with pytest.raises(CustodianError):
        ledger.append(
            record_id="admit", kind="trust_decision", payload=payload, fence=fence, assert_owned=assert_owned
        )


def test_hard_linked_journal_is_not_a_mutation_target(journal, tmp_path):
    import os

    ledger, store = journal
    outside = tmp_path / "outside"
    os.link(ledger.journal_path, outside)
    before = outside.read_bytes()
    with pytest.raises(CustodianError):
        commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    assert outside.read_bytes() == before


def test_directory_symlink_is_not_followed_for_genesis(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("registry-one", default_policy().to_dict(), actor="operator", reviewed=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "registry"
    alias.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(CustodianError):
            TrustJournal(alias, "registry-one", InjectedCustody(store)).initialize_reviewed_genesis()
        assert list(outside.iterdir()) == []
    finally:
        store.close()


def test_append_checks_lease_after_final_remote_snapshot(journal, monkeypatch):
    ledger, store = journal
    changed = default_policy().to_dict()
    changed["revision"] = 2
    fence = store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id="policy",
        holder="lease",
        local_token=1,
    )
    calls = 0
    lost = False
    original = ledger.snapshot

    def snapshot_then_expire():
        nonlocal calls, lost
        result = original()
        calls += 1
        if calls == 2:
            lost = True
        return result

    def assert_owned():
        if lost:
            raise CustodianError("lease lost")

    monkeypatch.setattr(ledger, "snapshot", snapshot_then_expire)
    with pytest.raises(CustodianError):
        ledger.append(
            record_id="policy",
            kind="policy_snapshot",
            payload=changed,
            fence=fence,
            assert_owned=assert_owned,
        )


def test_verified_snapshot_reuse_never_hides_a_later_revoke(journal):
    from magicite.core import trust_journal

    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    assert ledger.snapshot().latest_by_engram["subject"]["decision"] == "admit"
    assert ledger.snapshot() is ledger.snapshot()
    key = (str(ledger.directory.absolute()), "registry-one", None)
    stale = trust_journal._VERIFIED_SNAPSHOTS[key]
    # Another process revokes; this process still holds the earlier verification.
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    trust_journal._VERIFIED_SNAPSHOTS[key] = stale
    assert ledger.snapshot().latest_by_engram["subject"]["decision"] == "revoke"


@pytest.mark.parametrize("mutation", ["edit_journal", "edit_head", "delete"])
def test_verified_snapshot_reuse_closes_on_local_change(journal, mutation):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    ledger.snapshot()
    if mutation == "edit_journal":
        rows = ledger.journal_path.read_text().splitlines()
        row = json.loads(rows[-1])
        row["payload"]["decision"] = "admit"
        rows[-1] = json.dumps(row)
        ledger.journal_path.write_text("\n".join(rows) + "\n")
    elif mutation == "edit_head":
        head = json.loads(ledger.head_path.read_bytes())
        head["head_sequence"] -= 1
        ledger.head_path.write_text(json.dumps(head))
    else:
        ledger.journal_path.unlink()
    with pytest.raises(CustodianError):
        ledger.snapshot()


def test_verified_snapshot_reuse_requires_reachable_custody(journal, monkeypatch):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    ledger.snapshot()

    def unavailable(operation, **arguments):
        raise CustodianError("custody unavailable")

    monkeypatch.setattr(ledger.client, "call", unavailable)
    with pytest.raises(CustodianError):
        ledger.snapshot()


def test_incremental_append_equals_full_reverification(journal, monkeypatch):
    from magicite.core import trust_journal

    ledger, store = journal
    ledger.snapshot()
    remote_calls = 0
    real_remote = ledger._remote

    def counting_remote(**arguments):
        nonlocal remote_calls
        remote_calls += 1
        return real_remote(**arguments)

    monkeypatch.setattr(ledger, "_remote", counting_remote)
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    incremental = ledger.snapshot()
    assert remote_calls == 0, "own appends extend the verified replay without re-reading history"
    trust_journal._VERIFIED_SNAPSHOTS.clear()
    cold = ledger.snapshot()
    assert remote_calls == 1
    assert cold.head == incremental.head
    assert cold.policy == incremental.policy
    assert cold.decisions == incremental.decisions
    assert cold.latest_by_engram == incremental.latest_by_engram
    assert cold.records == incremental.records
    assert cold.source_signers == incremental.source_signers
    assert ledger.journal_path.read_bytes().count(b"\n") == 3


def test_same_size_edit_with_restored_mtime_still_closes(journal):
    import os

    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    ledger.snapshot()
    before = os.stat(ledger.journal_path)
    content = ledger.journal_path.read_bytes()
    forged = content.replace(b'"decision":"revoke"', b'"decision":"reject"')
    assert len(forged) == len(content) and forged != content
    ledger.journal_path.write_bytes(forged)
    os.utime(ledger.journal_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(CustodianError):
        ledger.snapshot()


def test_concurrent_local_write_between_verification_and_append_closes(journal, monkeypatch):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    real_call = ledger.client.call

    def interleave(operation, **arguments):
        result = real_call(operation, **arguments)
        if operation == "prepare_record":
            with ledger.journal_path.open("ab") as stream:
                stream.write(b"{}\n")
        return result

    monkeypatch.setattr(ledger.client, "call", interleave)
    with pytest.raises(CustodianError):
        commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    monkeypatch.setattr(ledger.client, "call", real_call)
    with pytest.raises(CustodianError):
        ledger.snapshot()


def test_concurrent_local_write_during_append_window_closes_and_drops_cache(journal, monkeypatch):
    ledger, store = journal
    commit(ledger, store, "admit", "admit", "2026-01-01T00:00:00Z")
    real_append = trust_journal._append_file

    def raced_append(directory, name, content, expected, assert_owned):
        def owned_then_foreign_write():
            # Runs after the pre-write identity check, before our own write.
            with ledger.journal_path.open("ab") as stream:
                stream.write(b"{}\n")
            assert_owned()

        return real_append(directory, name, content, expected, owned_then_foreign_write)

    monkeypatch.setattr(trust_journal, "_append_file", raced_append)
    with pytest.raises(CustodianError):
        commit(ledger, store, "revoke", "revoke", "2026-01-02T00:00:00Z")
    monkeypatch.setattr(trust_journal, "_append_file", real_append)
    with pytest.raises(CustodianError):
        ledger.snapshot()
