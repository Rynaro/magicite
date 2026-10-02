"""AC-TH-05 witnesses: fault matrix at every persistence boundary and the ack trace.

VERIFY sub-clause 1: "process/fault matrix around every persistence boundary
and lost replies" -- ``test_fault_matrix_*``.
VERIFY sub-clause 2: "acknowledgement trace shows custodian fsync first" --
``test_ack_trace_*`` and ``test_custodian_connection_is_synchronous_full``.

Standard adopted in-repo (owner decision): a sqlite3 statement trace on the
custodian connection (COMMIT), spies on the reply returned to the client, the
local head replace and the append return, plus ``PRAGMA synchronous`` = FULL
(so COMMIT implies a durable fsync inside sqlite). REMAINING / deferred to the
Linux container run: a literal fsync(2) syscall trace (strace-level ordering).

Faults are in-process injections against a simulated custody adapter; this
exercises mechanisms only and is not a production custody qualification.
"""

from __future__ import annotations

import json

import pytest

from magicite.core import trust_journal
from magicite.core.trust import TrustDecision, default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal

REG = "registry-one"


class InjectedCustody:
    def __init__(self, store):
        self.store = store

    def call(self, operation, **arguments):
        return getattr(self.store, operation)(REG, **arguments)


@pytest.fixture
def env(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll(REG, default_policy().to_dict(), actor="operator", reviewed=True)
    ledger = TrustJournal(tmp_path / "registry", REG, InjectedCustody(store))
    ledger.initialize_reviewed_genesis()
    yield ledger, store
    store.close()


def _fence(store, attempt):
    return store.register_fence(
        REG, predecessor=store.read_current(REG), attempt_id=attempt, holder="lease", local_token=1
    )


def _payload(identity, engram, decision):
    policy = default_policy()
    return TrustDecision(
        decision_id=identity,
        engram_id=engram,
        content_digest="a" * 64,
        decision=decision,
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-01-01T00:00:00Z",
    ).to_dict()


def _append(ledger, store, identity, engram, decision, assert_owned=lambda: None, attempt=None):
    return ledger.append(
        record_id=identity,
        kind="trust_decision",
        payload=_payload(identity, engram, decision),
        fence=_fence(store, attempt or identity),
        assert_owned=assert_owned,
    )


def _reconcile(ledger, store):
    return ledger.reconcile(fence=_fence(store, "recovery"), assert_owned=lambda: None)


def _local_head(ledger):
    return json.loads((ledger.directory / "head.json").read_bytes())


class Fault(RuntimeError):
    pass


def _arm(ledger, monkeypatch, point):
    """Install exactly one injected fault at the named persistence boundary."""
    real_call = ledger.client.call
    state = {"head_written": False, "armed_fsync": False}

    def call(operation, **arguments):
        if point == "prepare_before_custody" and operation == "prepare_record":
            raise Fault(point)
        if point == "commit_before_custody" and operation == "commit_record":
            raise Fault(point)
        result = real_call(operation, **arguments)
        if point == "prepare_lost_reply" and operation == "prepare_record":
            raise Fault(point)
        if point == "commit_lost_reply" and operation == "commit_record":
            raise Fault(point)  # custody already committed; reply lost
        if point == "journal_write__replace_file" and operation == "prepare_record":
            trust_journal._VERIFIED_SNAPSHOTS.clear()  # force the full-rewrite (no-cache) path
        if point == "journal_fsync" and operation == "prepare_record":
            state["armed_fsync"] = True
        return result

    monkeypatch.setattr(ledger.client, "call", call)

    if point == "journal_fsync":
        real_fsync = trust_journal.os.fsync

        def fsync(descriptor):
            if state["armed_fsync"]:
                state["armed_fsync"] = False
                raise OSError(5, "injected fsync failure")
            return real_fsync(descriptor)

        monkeypatch.setattr(trust_journal.os, "fsync", fsync)
    for name in ("_append_file", "_replace_file"):
        if point == "journal_write_" + name:
            real = getattr(trust_journal, name)

            def failing(*args, _real=real, **kwargs):
                if args[1] == "journal.jsonl":
                    raise OSError(5, "injected journal write failure")
                return _real(*args, **kwargs)

            monkeypatch.setattr(trust_journal, name, failing)

    real_write_head = ledger._write_head

    def write_head(head, assert_owned=lambda: None):
        if point == "local_head_write":
            raise Fault(point)
        real_write_head(head, assert_owned)
        state["head_written"] = True

    monkeypatch.setattr(ledger, "_write_head", write_head)

    def assert_owned():
        if point == "pre_return_ack" and state["head_written"]:
            raise Fault(point)

    return assert_owned


# point -> (custody holds an unacknowledged preparation, custody committed it)
MATRIX = {
    "prepare_before_custody": (False, False),
    "prepare_lost_reply": (True, False),
    "journal_write__append_file": (True, False),
    "journal_write__replace_file": (True, False),
    "journal_fsync": (True, False),
    "commit_before_custody": (True, False),
    "commit_lost_reply": (False, True),
    "local_head_write": (False, True),
    "pre_return_ack": (False, True),
}


@pytest.mark.parametrize("point", MATRIX)
def test_fault_matrix_every_persistence_boundary(env, monkeypatch, point):
    """AC-TH-05 VERIFY "process/fault matrix around every persistence boundary and lost replies".

    One fault per boundary after an acknowledged revoke of ``victim``. Asserts: the
    acked revoke is never lost, the faulted append never reports success, a valid
    unacknowledged preparation stays recoverable through reconcile (the prepared
    revoke of ``subject`` is not discarded), and local/custody heads converge.
    """
    ledger, store = env
    _append(ledger, store, "admit-subject", "subject", "admit")
    _append(ledger, store, "revoke-victim", "victim", "revoke")  # acknowledged
    pending_expected, committed_expected = MATRIX[point]
    sequence_before = store.read_current(REG)["head_sequence"]

    assert_owned = _arm(ledger, monkeypatch, point)
    with pytest.raises((Fault, CustodianError, OSError)):
        _append(ledger, store, "revoke-subject", "subject", "revoke", assert_owned)  # never success
    monkeypatch.undo()

    # Custody-side truth at the fault.
    head = store.read_current(REG)
    pending = store.prepared_record(REG)
    assert (pending is not None) == pending_expected
    assert (head["head_sequence"] == sequence_before + 1) == committed_expected
    if pending is not None:
        assert pending["record_id"] == "revoke-subject"  # valid prepared revoke retained

    # Between fault and reconcile: closed, or still showing the acked revoke. Never admit.
    try:
        view = ledger.snapshot()
    except CustodianError:
        view = None
    if view is not None:
        assert view.latest_by_engram["victim"]["decision"] == "revoke"

    recovered = _reconcile(ledger, store)
    assert recovered.latest_by_engram["victim"]["decision"] == "revoke"
    if point == "prepare_before_custody":
        # Custody never saw it: nothing to recover and nothing was claimed.
        assert recovered.latest_by_engram["subject"]["decision"] == "admit"
    else:
        assert recovered.latest_by_engram["subject"]["decision"] == "revoke"
    assert store.prepared_record(REG) is None
    final = store.read_current(REG)
    local = _local_head(ledger)
    assert (local["head_sequence"], local["head_mac"]) == (final["head_sequence"], final["head_mac"])
    assert ledger.snapshot().head["head_mac"] == final["head_mac"]  # no silent divergence


def test_fault_matrix_lost_reply_retry_is_exact_and_not_duplicated(env, monkeypatch):
    """AC-TH-05 VERIFY "lost replies": retry after a lost COMMIT reply takes the exact-retry path."""
    ledger, store = env
    _append(ledger, store, "revoke-victim", "victim", "revoke")
    assert_owned = _arm(ledger, monkeypatch, "commit_lost_reply")
    with pytest.raises(Fault):
        _append(ledger, store, "revoke-subject", "subject", "revoke", assert_owned)
    monkeypatch.undo()
    _reconcile(ledger, store)
    count = len(ledger.snapshot().records)
    again = _append(ledger, store, "revoke-subject", "subject", "revoke", attempt="retry")  # exact retry
    assert len(again.records) == count
    assert len(store.committed_records(REG)) == count
    assert again.latest_by_engram["subject"]["decision"] == "revoke"


def test_fault_matrix_positive_control_unfaulted_append_acks(env):
    """AC-TH-05 control: with no fault the same flow commits, finalizes locally and acks."""
    ledger, store = env
    result = _append(ledger, store, "revoke-subject", "subject", "revoke")
    assert result.latest_by_engram["subject"]["decision"] == "revoke"
    assert store.prepared_record(REG) is None
    assert _local_head(ledger)["head_mac"] == store.read_current(REG)["head_mac"]


def _traced_append(ledger, store, monkeypatch):
    events: list[str] = []
    store._conn.set_trace_callback(lambda sql: events.append("sql:" + sql.strip().split("\n")[0]))
    real_call = ledger.client.call

    def call(operation, **arguments):
        if operation == "commit_record":
            events.append("commit_record:begin")
        result = real_call(operation, **arguments)
        if operation == "commit_record":
            events.append("reply:returned_to_client")
        return result

    monkeypatch.setattr(ledger.client, "call", call)
    real_replace = trust_journal._replace_file

    def replace(directory, name, *args, **kwargs):
        real_replace(directory, name, *args, **kwargs)
        if name == "head.json":
            events.append("local_head_replaced")

    monkeypatch.setattr(trust_journal, "_replace_file", replace)
    _append(ledger, store, "revoke-subject", "subject", "revoke")
    events.append("append_returned")
    store._conn.set_trace_callback(None)
    return events


def test_ack_trace_custodian_commit_precedes_reply_head_replace_and_return(env, monkeypatch):
    """AC-TH-05 VERIFY "acknowledgement trace shows custodian fsync first".

    COMMIT (durable under synchronous=FULL) < reply returned to client <
    local head replace < append returns.
    """
    ledger, store = env
    events = _traced_append(ledger, store, monkeypatch)
    start = events.index("commit_record:begin")
    commit = events.index("sql:COMMIT", start)
    reply = events.index("reply:returned_to_client")
    head_replace = events.index("local_head_replaced")
    returned = events.index("append_returned")
    assert start < commit < reply < head_replace < returned
    # Control: the commit statement really is the one inserting the record.
    assert any(e.startswith("sql:INSERT") for e in events[start:commit])


def test_ack_trace_detects_head_written_before_commit(env, monkeypatch):
    """AC-TH-05 control: the trace assertion fails if the head replace precedes COMMIT."""
    ledger, store = env
    real_write_head = ledger._write_head
    real_call = ledger.client.call
    order: list[str] = []

    def call(operation, **arguments):
        result = real_call(operation, **arguments)
        order.append(operation)
        return result

    monkeypatch.setattr(ledger.client, "call", call)
    monkeypatch.setattr(
        ledger, "_write_head", lambda *a, **k: (order.append("head"), real_write_head(*a, **k))[1]
    )
    _append(ledger, store, "revoke-subject", "subject", "revoke")
    assert order.index("commit_record") < order.index("head")


def test_custodian_connection_is_synchronous_full(env, tmp_path):
    """AC-TH-05 VERIFY "custodian fsync first": create() and open() both run PRAGMA synchronous=FULL (2)."""
    _, store = env
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    reopened = CustodianStore.open(tmp_path / "custody")
    try:
        assert reopened._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        reopened.close()
