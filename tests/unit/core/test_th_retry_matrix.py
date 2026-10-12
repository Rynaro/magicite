"""AC-TH-02 retry matrix: record-identity kind reuse and genesis retries.

Direct store / injected custody are explicit test adapters (mechanism only).
"""

from __future__ import annotations

import pytest

from magicite.core.trust import TrustDecision, default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal

R = "r"


class Injected:
    def __init__(self, store, registry=R):
        self.store, self.registry = store, registry

    def call(self, operation, **arguments):
        return getattr(self.store, operation)(self.registry, **arguments)


@pytest.fixture
def store(tmp_path):
    authority = CustodianStore.create(tmp_path / "custody")
    authority.enroll(R, default_policy().to_dict(), actor="operator", reviewed=True)
    yield authority
    authority.close()


_ATTEMPTS = iter(range(10**6))


def fence(store):
    return store.register_fence(
        R,
        predecessor=store.read_current(R),
        attempt_id=f"attempt-{next(_ATTEMPTS)}",
        holder="lease",
        local_token=1,
    )


def policy(revision=1):
    value = default_policy().to_dict()
    value["revision"] = revision
    return value


def decision(identity, action="revoke"):
    base = default_policy()
    return TrustDecision(
        decision_id=identity,
        engram_id="subject",
        content_digest="a" * 64,
        decision=action,
        source_channel="local_authored",
        policy_id=base.policy_id,
        policy_revision=base.revision,
        policy_digest=base.digest(),
        actor="operator",
        timestamp="2026-01-01T00:00:00Z",
    ).to_dict()


def payload_for(kind, record_id):
    return decision(record_id) if kind == "trust_decision" else policy(2)


def prepare(store, active, record_id, kind, payload):
    return store.prepare_record(
        R, fence=active, expected_head=store.read_current(R), record_id=record_id, kind=kind, payload=payload
    )


def commit(store, active, record_id, kind, payload):
    record = prepare(store, active, record_id, kind, payload)
    return store.commit_record(R, fence=active, expected_head=store.read_current(R), record=record)


def rotate(store, transition_id="rot"):
    active = fence(store)
    head = store.read_current(R)
    store.rotate_prepare(R, fence=active, expected_head=head, transition_id=transition_id, actor="operator")
    committed = store.rotate_commit(R, fence=active, expected_head=head, transition_id=transition_id)
    store.rotate_finish(R, transition_id=transition_id, expected_head=committed)


# --- (1) same record ID reused with a changed kind ---------------------------


def _seed(store, case):
    """Commit (or prepare) the original record; return (record_id, new_kind)."""
    if case == "genesis->policy_snapshot":
        return "genesis", "policy_snapshot"
    if case == "policy_snapshot->trust_decision":
        commit(store, fence(store), "shared", "policy_snapshot", policy(2))
        return "shared", "trust_decision"
    if case == "trust_decision->policy_snapshot":
        commit(store, fence(store), "shared", "trust_decision", decision("shared"))
        return "shared", "policy_snapshot"
    if case == "pending policy_snapshot->trust_decision":
        prepare(store, fence(store), "shared", "policy_snapshot", policy(2))
        return "shared", "trust_decision"
    if case == "epoch_transition->policy_snapshot":
        rotate(store, "rot")
        return "epoch-rot", "policy_snapshot"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "genesis->policy_snapshot",
        "policy_snapshot->trust_decision",
        "trust_decision->policy_snapshot",
        "pending policy_snapshot->trust_decision",
        "epoch_transition->policy_snapshot",
    ],
)
def test_same_record_id_with_changed_kind_is_rejected_without_advancing(store, case):
    """AC-TH-02 VERIFY: retry matrix across decision, policy, genesis and epoch records --
    THEN clause "same ID is reused with changed ... kind ... SHALL be rejected without
    advancing authority" (committed genesis/policy/decision/epoch IDs and a pending ID)."""
    record_id, new_kind = _seed(store, case)
    active = fence(store)
    before = store.read_current(R)
    history = store.committed_records(R)
    pending = store.prepared_record(R)
    with pytest.raises(CustodianError, match="conflicting immutable record identity"):
        prepare(store, active, record_id, new_kind, payload_for(new_kind, record_id))
    assert store.read_current(R) == before
    assert store.committed_records(R) == history
    assert store.prepared_record(R) == pending
    # Positive control: the same kind/payload shape under a fresh identity is
    # preparable, so the rejection above is the identity guard, not the payload.
    if pending is None:
        fresh = prepare(store, active, "fresh-id", new_kind, payload_for(new_kind, "fresh-id"))
        assert fresh["record_id"] == "fresh-id" and fresh["kind"] == new_kind


def test_epoch_identity_reused_by_ordinary_record_blocks_rotation(store):
    """AC-TH-02 VERIFY: retry matrix ... epoch records -- THEN "same ID is reused with
    changed ... kind ... SHALL be rejected without advancing authority" (an ordinary
    record already holding ``epoch-<id>`` cannot be re-minted as epoch_transition)."""
    commit(store, fence(store), "epoch-rot", "policy_snapshot", policy(2))
    active = fence(store)
    head = store.read_current(R)
    history = store.committed_records(R)
    with pytest.raises(CustodianError, match="conflicting immutable record identity"):
        store.rotate_prepare(R, fence=active, expected_head=head, transition_id="rot", actor="operator")
    assert store.read_current(R) == head
    assert store.committed_records(R) == history
    with pytest.raises(CustodianError, match="no matching custody rotation"):
        store.rotation_status(R)  # nothing was durably prepared
    # Positive control: a distinct transition identity rotates normally.
    rotate(store, "rot-two")
    assert store.read_current(R)["epoch"] == 2


# --- (3) decision / policy prepare, commit and lost-reply cells -------------


@pytest.mark.parametrize("kind", ["trust_decision", "policy_snapshot"])
def test_prepare_commit_and_lost_reply_retries_have_one_effect(store, kind):
    """AC-TH-02 VERIFY: prepare/commit/lost-reply retry matrix across decision, policy ...
    records -- THEN "exact committed retries SHALL have one effect; conflicting payloads ...
    SHALL be rejected without advancing authority"."""
    payload = payload_for(kind, "item")
    active = fence(store)
    head = store.read_current(R)
    record = prepare(store, active, "item", kind, payload)
    # Lost prepare reply: the exact retry returns the same durable preparation.
    assert prepare(store, active, "item", kind, payload) == record
    assert store.read_current(R)["head_sequence"] == head["head_sequence"]
    committed = store.commit_record(R, fence=active, expected_head=head, record=record)
    # Lost commit reply: exact commit retry and exact prepare+commit retry are no-ops.
    assert store.commit_record(R, fence=active, expected_head=head, record=record) == committed
    assert prepare(store, active, "item", kind, payload) == record
    assert store.commit_record(R, fence=active, expected_head=head, record=record) == committed
    assert committed["head_sequence"] == head["head_sequence"] + 1
    assert [r["record_id"] for r in store.committed_records(R)].count("item") == 1
    changed = decision("item", "admit") if kind == "trust_decision" else policy(3)
    with pytest.raises(CustodianError, match="conflicting immutable record identity"):
        prepare(store, active, "item", kind, changed)
    assert store.read_current(R) == committed
    assert store.prepared_record(R) is None


# --- (2) genesis prepare / commit / lost reply ------------------------------


def test_genesis_lost_enroll_reply_exact_retry_has_one_effect(tmp_path):
    """AC-TH-02 VERIFY: ... lost-reply retry matrix across ... genesis records -- THEN
    "exact committed retries SHALL have one effect" (custodian enroll retry after a lost
    reply neither resets nor duplicates; the committed genesis is exactly the request)."""
    store = CustodianStore.create(tmp_path / "custody")
    try:
        store.enroll(R, policy(), actor="operator", reviewed=True)  # reply "lost"
        before, history = store.read_current(R), store.committed_records(R)
        with pytest.raises(CustodianError, match="already enrolled"):
            store.enroll(R, policy(), actor="operator", reviewed=True)
        assert store.read_current(R) == before
        assert store.committed_records(R) == history and len(history) == 1
        assert history[0]["kind"] == "genesis" and history[0]["record_id"] == "genesis"
        assert history[0]["payload"] == {
            "policy": policy(),
            "actor": "operator",
            "historical_provenance": "operator-reviewed-current-state",
        }
        # Positive control: enrollment itself works for a not-yet-enrolled registry.
        store.enroll("other", policy(), actor="operator", reviewed=True)
        assert store.read_current("other")["head_sequence"] == 1
    finally:
        store.close()


@pytest.mark.parametrize("variant", ["policy", "actor", "unreviewed"])
def test_genesis_differing_retry_is_rejected_without_change(store, variant):
    """AC-TH-02 VERIFY: ... genesis records -- THEN "conflicting payloads ... SHALL be
    rejected without advancing authority" (a differing genesis never replaces enrollment)."""
    before, history = store.read_current(R), store.committed_records(R)
    arguments = {"policy": policy(), "actor": "operator", "reviewed": True}
    if variant == "policy":
        arguments["policy"] = policy(2)
    elif variant == "actor":
        arguments["actor"] = "someone-else"
    else:
        arguments["reviewed"] = False
    with pytest.raises(CustodianError):
        store.enroll(R, arguments.pop("policy"), **arguments)
    assert store.read_current(R) == before
    assert store.committed_records(R) == history


@pytest.fixture
def local(tmp_path, store):
    return TrustJournal(tmp_path.resolve() / "local", R, Injected(store))


def _crash_before_head(ledger, monkeypatch):
    def crash(*args, **kwargs):
        raise OSError("crash between journal and head")

    monkeypatch.setattr(ledger, "_write_head", crash)
    with pytest.raises(OSError):
        ledger.initialize_reviewed_genesis()
    monkeypatch.undo()
    assert ledger.journal_path.exists() and not ledger.head_path.exists()


def test_genesis_init_resumes_after_crash_between_journal_and_head(local, monkeypatch, store):
    """AC-TH-02 VERIFY: prepare/commit/lost-reply retry matrix across ... genesis records --
    THEN "exact committed retries SHALL have one effect" (client genesis init interrupted
    after the journal write resumes exactly; reads were closed until then)."""
    _crash_before_head(local, monkeypatch)
    journal = local.journal_path.read_bytes()
    with pytest.raises(CustodianError):
        local.snapshot()  # positive control: interrupted state is closed
    local.initialize_reviewed_genesis()
    assert local.journal_path.read_bytes() == journal
    snapshot = local.snapshot()
    assert snapshot.head["head_sequence"] == 1
    assert [r["record_id"] for r in snapshot.records] == ["genesis"]


def test_genesis_init_retry_after_completion_has_one_effect(local):
    """AC-TH-02 VERIFY: ... lost-reply retry matrix across ... genesis records -- THEN
    "exact committed retries SHALL have one effect" (a retry of a completed client init
    after a lost reply is refused, writes nothing, and the initialized state stays valid)."""
    local.initialize_reviewed_genesis()
    files = local.journal_path.read_bytes(), local.head_path.read_bytes()
    with pytest.raises(CustodianError, match="already initialized"):
        local.initialize_reviewed_genesis()
    assert (local.journal_path.read_bytes(), local.head_path.read_bytes()) == files
    assert local.snapshot().head["head_sequence"] == 1


@pytest.mark.parametrize(
    "tamper", ["truncated-journal", "edited-journal", "head-without-journal", "stale-head"]
)
def test_genesis_init_refuses_nonidentical_partial_state(local, monkeypatch, tamper):
    """AC-TH-02 VERIFY: ... genesis records -- THEN "conflicting payloads ... SHALL be
    rejected without advancing authority" (init resumes only over byte-identical files)."""
    _crash_before_head(local, monkeypatch)
    exact = local.journal_path.read_bytes()
    if tamper == "truncated-journal":
        local.journal_path.write_bytes(exact[:-10])
    elif tamper == "edited-journal":
        local.journal_path.write_bytes(exact.replace(b'"operator"', b'"attacker"'))
    else:
        local.initialize_reviewed_genesis()
        if tamper == "head-without-journal":
            local.journal_path.unlink()
        else:
            stale = local.head_path.read_bytes().replace(b'"head_sequence":1', b'"head_sequence":0')
            local.head_path.write_bytes(stale)
    state = {p: p.read_bytes() for p in (local.journal_path, local.head_path) if p.exists()}
    with pytest.raises(CustodianError, match="already initialized"):
        local.initialize_reviewed_genesis()
    assert {p: p.read_bytes() for p in (local.journal_path, local.head_path) if p.exists()} == state
    with pytest.raises(CustodianError):
        local.snapshot()


def test_genesis_resume_honours_lost_lease_and_advanced_custody(local, monkeypatch, store):
    """AC-TH-02 VERIFY: ... genesis records -- THEN resumed retries are "rejected without
    advancing authority" when the lease is lost or custody already moved past genesis."""
    _crash_before_head(local, monkeypatch)

    def lost():
        raise CustodianError("lost lease")

    with pytest.raises(CustodianError, match="lost lease"):
        local.initialize_reviewed_genesis(assert_owned=lost)
    assert not local.head_path.exists()
    commit(store, fence(store), "later", "policy_snapshot", policy(2))
    with pytest.raises(CustodianError, match="explicit recovery"):
        local.initialize_reviewed_genesis()
    assert not local.head_path.exists()
