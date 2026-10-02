"""AC-TH-06 witness: lease loss after external commit / before local replace and ACK.

VERIFY sub-clause: "existing lease assertions before and after external commit
and before local replace/ACK".
"""

from __future__ import annotations

import pytest

from magicite.core import trust_journal
from magicite.core.trust import TrustDecision, default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal
from magicite.errors import BusyError


class InjectedCustody:
    def __init__(self, store):
        self.store = store

    def call(self, operation, **arguments):
        return getattr(self.store, operation)("registry-one", **arguments)


@pytest.fixture
def env(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("registry-one", default_policy().to_dict(), actor="operator", reviewed=True)
    ledger = TrustJournal(tmp_path / "registry", "registry-one", InjectedCustody(store))
    ledger.initialize_reviewed_genesis()
    yield ledger, store
    store.close()


def _fence(store, attempt):
    return store.register_fence(
        "registry-one",
        predecessor=store.read_current("registry-one"),
        attempt_id=attempt,
        holder="lease",
        local_token=1,
    )


def _payload(identity):
    policy = default_policy()
    return TrustDecision(
        decision_id=identity,
        engram_id="subject",
        content_digest="a" * 64,
        decision="admit",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-01-01T00:00:00Z",
    ).to_dict()


class Lease:
    """assert_owned that raises the production lease-loss error once lost."""

    def __init__(self):
        self.lost = False
        self.checks = 0

    def __call__(self):
        self.checks += 1
        if self.lost:
            raise BusyError("writer lease lost")


def _append(ledger, store, lease, identity="admit"):
    return ledger.append(
        record_id=identity,
        kind="trust_decision",
        payload=_payload(identity),
        fence=_fence(store, identity),
        assert_owned=lease,
    )


def _reconcile(ledger, store):
    return ledger.reconcile(fence=_fence(store, "recovery"), assert_owned=lambda: None)


def test_positive_control_retained_ownership_acks(env):
    """AC-TH-06 control: same flow with the lease retained commits and ACKs."""
    ledger, store = env
    result = _append(ledger, store, Lease())
    assert result.latest_by_engram["subject"]["decision"] == "admit"
    assert (ledger.directory / "head.json").read_bytes()


def test_lease_lost_after_commit_blocks_local_head_replace_and_ack(env, monkeypatch):
    """AC-TH-06 VERIFY: existing lease assertions after external commit and before local replace/ACK."""
    ledger, store = env
    head_path = ledger.directory / "head.json"
    head_before = head_path.read_bytes()
    lease = Lease()
    real_call = ledger.client.call
    commits = []

    def lose_after_commit(operation, **arguments):
        result = real_call(operation, **arguments)
        if operation == "commit_record":
            commits.append(result)
            lease.lost = True  # exactly after custody returns
        return result

    def forbidden_write_head(*args, **kwargs):
        raise AssertionError("local head replace must not run after lease loss")

    monkeypatch.setattr(ledger.client, "call", lose_after_commit)
    monkeypatch.setattr(ledger, "_write_head", forbidden_write_head)
    with pytest.raises(BusyError):
        _append(ledger, store, lease)
    assert len(commits) == 1  # custody really committed
    assert store.read_current("registry-one")["head_sequence"] == commits[0]["head_sequence"]
    assert head_path.read_bytes() == head_before  # local head not replaced
    monkeypatch.undo()

    # Fail closed: custody is ahead of the local head until explicit reconcile.
    with pytest.raises(CustodianError):
        ledger.snapshot()
    recovered = _reconcile(ledger, store)
    assert recovered.latest_by_engram["subject"]["decision"] == "admit"
    assert head_path.read_bytes() != head_before
    assert ledger.snapshot().head == recovered.head


def test_lease_lost_after_local_append_before_commit_never_commits(env, monkeypatch):
    """AC-TH-06 VERIFY: existing lease assertions before external commit (after local journal append)."""
    ledger, store = env
    head_path = ledger.directory / "head.json"
    head_before = head_path.read_bytes()
    custody_head_before = store.read_current("registry-one")
    lease = Lease()
    real_call = ledger.client.call
    operations = []

    def spy(operation, **arguments):
        operations.append(operation)
        return real_call(operation, **arguments)

    def lose_after_journal(name):
        original = getattr(trust_journal, name)

        def wrapper(directory, file_name, *args, **kwargs):
            result = original(directory, file_name, *args, **kwargs)
            if file_name == "journal.jsonl":
                lease.lost = True
            return result

        monkeypatch.setattr(trust_journal, name, wrapper)

    lose_after_journal("_append_file")
    lose_after_journal("_replace_file")
    monkeypatch.setattr(ledger.client, "call", spy)
    with pytest.raises(BusyError):
        _append(ledger, store, lease)
    assert lease.lost
    assert "prepare_record" in operations
    assert "commit_record" not in operations
    assert store.read_current("registry-one")["head_sequence"] == custody_head_before["head_sequence"]
    assert head_path.read_bytes() == head_before
    monkeypatch.undo()

    with pytest.raises(CustodianError):  # local journal ahead of / pending custody
        ledger.snapshot()
    recovered = _reconcile(ledger, store)
    assert recovered.latest_by_engram["subject"]["decision"] == "admit"
