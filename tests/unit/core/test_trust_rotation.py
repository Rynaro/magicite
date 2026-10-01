"""AC-TH-10 durable rotation anchors; direct store is an explicit test adapter."""

from __future__ import annotations

import copy

import pytest

from magicite.core.trust import default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore


@pytest.fixture
def authority(tmp_path):
    store = CustodianStore.create(tmp_path / "custody")
    for registry in ("r", "other"):
        store.enroll(registry, default_policy().to_dict(), actor="operator", reviewed=True)
    yield store
    store.close()


def start(store):
    before = store.read_current("r")
    fence = store.register_fence("r", predecessor=before, attempt_id="a", holder="h", local_token=1)
    head = store.read_current("r")
    transition = store.rotate_prepare(
        "r", fence=fence, expected_head=head, transition_id="rotate-one", actor="operator"
    )
    return fence, head, transition


def test_rotation_preparation_is_durable_nonadvancing_and_blocks_ordinary_operations(authority):
    fence, head, transition = start(authority)
    status = authority.rotation_status("r")
    assert status["phase"] == "PREPARED"
    assert status["head"] == head
    assert status["transition"] == transition
    assert (
        authority.rotate_prepare(
            "r", fence=fence, expected_head=head, transition_id="rotate-one", actor="operator"
        )
        == transition
    )
    with pytest.raises(CustodianError):
        authority.read_current("r")
    with pytest.raises(CustodianError):
        authority.prepare_record(
            "r",
            fence=fence,
            expected_head=head,
            record_id="ordinary",
            kind="policy_snapshot",
            payload=default_policy().to_dict(),
        )
    # The unrelated registry remains available under its original key.
    assert authority.read_current("other")["epoch"] == 1
    reopened = CustodianStore.open(authority.directory)
    try:
        assert reopened.rotation_status("r") == status
    finally:
        reopened.close()


def test_rotation_commit_preserves_history_policy_and_requires_exact_finish(authority):
    from magicite.core.trust_rotation import verify_transition

    fence, head, transition = start(authority)
    verify_transition(transition)
    before = authority.committed_records("r")
    committed = authority.rotate_commit("r", fence=fence, expected_head=head, transition_id="rotate-one")
    assert committed["epoch"] == 2
    assert committed["head_sequence"] == head["head_sequence"] + 1
    assert committed["policy_digest"] == head["policy_digest"]
    assert authority.committed_records("r")[:-1] == before
    assert (
        authority.rotate_commit("r", fence=fence, expected_head=head, transition_id="rotate-one") == committed
    )
    with pytest.raises(CustodianError):
        authority.rotate_finish("r", transition_id="other", expected_head=committed)
    with pytest.raises(CustodianError):
        authority.read_current("r")
    authority.rotate_finish("r", transition_id="rotate-one", expected_head=committed)
    assert authority.read_current("r") == committed
    assert (
        authority.signer_for("r").public_key().public_bytes_raw().hex()
        == transition["body"]["new_public_key"]
    )
    assert (
        authority.signer_for("other").public_key().public_bytes_raw()
        == authority.signing_key.public_key().public_bytes_raw()
    )


def test_rotation_resumed_fence_rejects_old_commit_even_before_head_change(authority):
    fence, head, _ = start(authority)
    predecessor = authority.rotation_status("r")["head"]
    newer = authority.rotate_register(
        "r",
        transition_id="rotate-one",
        predecessor=predecessor,
        attempt_id="new",
        holder="new-holder",
        local_token=2,
    )
    with pytest.raises(CustodianError):
        authority.rotate_commit("r", fence=fence, expected_head=head, transition_id="rotate-one")
    with pytest.raises(CustodianError):
        authority.rotate_register(
            "r",
            transition_id="rotate-one",
            predecessor=predecessor,
            attempt_id="stalled",
            holder="old-holder",
            local_token=3,
        )
    committed = authority.rotate_commit("r", fence=newer, expected_head=head, transition_id="rotate-one")
    assert committed["epoch"] == 2


@pytest.mark.parametrize(
    "field,value", [("new_epoch", 4), ("policy_digest", "0" * 64), ("transition_id", "different")]
)
def test_rotation_dual_signatures_bind_complete_tuple(authority, field, value):
    from magicite.core.trust_rotation import verify_transition

    _, _, transition = start(authority)
    changed = copy.deepcopy(transition)
    changed["body"][field] = value
    with pytest.raises(CustodianError):
        verify_transition(changed)


def test_rotation_keeps_acknowledged_revoke_and_supports_next_epoch(authority):
    from magicite.core.trust import TrustDecision

    policy = default_policy()
    fence = authority.register_fence(
        "r", predecessor=authority.read_current("r"), attempt_id="initial", holder="h", local_token=1
    )
    revoke = TrustDecision(
        decision_id="revoked",
        engram_id="subject",
        content_digest="a" * 64,
        decision="revoke",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
    ).to_dict()
    head = authority.read_current("r")
    record = authority.prepare_record(
        "r", fence=fence, expected_head=head, record_id="revoked", kind="trust_decision", payload=revoke
    )
    authority.commit_record("r", fence=fence, expected_head=head, record=record)
    for epoch in (2, 3):
        head = authority.read_current("r")
        fence = authority.register_fence(
            "r", predecessor=head, attempt_id=f"epoch-{epoch}", holder="h", local_token=epoch
        )
        head = authority.read_current("r")
        identity = f"rotation-{epoch}"
        authority.rotate_prepare(
            "r", fence=fence, expected_head=head, transition_id=identity, actor="operator"
        )
        committed = authority.rotate_commit("r", fence=fence, expected_head=head, transition_id=identity)
        authority.rotate_finish("r", transition_id=identity, expected_head=committed)
        assert authority.read_current("r")["epoch"] == epoch
        assert authority.committed_records("r")[1] == record
        # Old decision identity remains immutable and cannot be resequenced.
        assert (
            authority.prepare_record(
                "r",
                fence=fence,
                expected_head=committed,
                record_id="revoked",
                kind="trust_decision",
                payload=revoke,
            )
            == record
        )


def test_pending_journal_record_blocks_rotation_without_new_keys(authority):
    fence = authority.register_fence(
        "r", predecessor=authority.read_current("r"), attempt_id="a", holder="h", local_token=1
    )
    head = authority.read_current("r")
    authority.prepare_record(
        "r",
        fence=fence,
        expected_head=head,
        record_id="pending",
        kind="policy_snapshot",
        payload=default_policy().to_dict(),
    )
    before = authority._load("r")
    with pytest.raises(CustodianError):
        authority.rotate_prepare(
            "r", fence=fence, expected_head=head, transition_id="rotation", actor="operator"
        )
    assert authority._load("r") == before


def test_rotation_preparation_survives_real_child_exit(tmp_path):
    import subprocess
    import sys

    directory = tmp_path / "custody"
    store = CustodianStore.create(directory)
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)
    store.close()
    program = """
import os, sys
from pathlib import Path
from magicite.core.trust_custodian import CustodianStore
store = CustodianStore.open(Path(sys.argv[1]))
fence = store.register_fence(
    "r", predecessor=store.read_current("r"), attempt_id="child", holder="h", local_token=1
)
store.rotate_prepare(
    "r", fence=fence, expected_head=store.read_current("r"),
    transition_id="child-rotation", actor="operator"
)
os._exit(23)
"""
    completed = subprocess.run([sys.executable, "-c", program, str(directory)], capture_output=True)
    assert completed.returncode == 23, completed.stderr.decode()
    reopened = CustodianStore.open(directory)
    try:
        status = reopened.rotation_status("r")
        assert status["phase"] == "PREPARED" and status["head"]["epoch"] == 1
        fence = reopened.rotate_register(
            "r",
            transition_id="child-rotation",
            predecessor=status["head"],
            attempt_id="parent",
            holder="parent",
            local_token=2,
        )
        head = reopened.rotate_commit(
            "r", transition_id="child-rotation", fence=fence, expected_head=status["head"]
        )
        reopened.rotate_finish("r", transition_id="child-rotation", expected_head=head)
        assert reopened.read_current("r")["epoch"] == 2
    finally:
        reopened.close()


def test_transition_certificate_binds_exact_prepared_record(authority):
    from magicite.core.trust_rotation import verify_transition_record

    _, _, transition = start(authority)
    record = authority.rotation_status("r")["record"]
    verify_transition_record(transition, record)
    for field in ("mac", "prev_mac", "payload_digest"):
        changed = copy.deepcopy(record)
        changed[field] = "f" * 64
        with pytest.raises(CustodianError):
            verify_transition_record(transition, changed)


def test_general_preparation_cannot_forge_rotation_or_genesis(authority):
    head = authority.read_current("r")
    fence = authority.register_fence("r", predecessor=head, attempt_id="ordinary", holder="h", local_token=1)
    before = authority.read_current("r")
    for kind in ("epoch_transition", "genesis"):
        with pytest.raises(CustodianError):
            authority.prepare_record(
                "r", fence=fence, expected_head=before, record_id="forged", kind=kind, payload={}
            )
    assert authority.read_current("r") == before
