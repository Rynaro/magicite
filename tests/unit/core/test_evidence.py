"""S09 evidence ledger: idempotence, provenance/support (AC-S09-01, AC-S09-04)."""

from __future__ import annotations

import pytest

from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.errors import IdempotencyKeyConflictError, InvalidInputError


@pytest.fixture(autouse=True)
def _clear_receipts():
    evidence_mod.clear_receipt_buffer()
    yield
    evidence_mod.clear_receipt_buffer()


def _decision_event(**overrides):
    base = dict(
        event_id="ev_idem_001",
        decision_id="dec_001",
        event_type="decision",
        recorded_at="2026-09-29T12:00:00+00:00",
        candidate_ids=("skill_a", "skill_b"),
        chosen_action="skill_a",
        behavior_policy_id="dense-v1",
        behavior_policy_digest="digest_a",
        propensity=1.0,
        query_fingerprint="a" * 64,
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        source_tier=0,
        retention_class="operational",
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


def test_event_id_idempotence(cfg, db_conn) -> None:
    """AC-S09-01: one immutable payload per event_id across checkpoint retries."""
    cfg.ensure_dirs()
    event = _decision_event()
    ack1 = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack1.durable is True
    assert ack1.replayed is False
    assert ack1.event_id == event.event_id

    # Identical payload retry → same ack, no duplicate sequence.
    ack2 = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack2.replayed is True
    assert ack2.sequence == ack1.sequence
    assert ack2.payload_digest == ack1.payload_digest

    # Conflicting payload for same event_id → conflict; original preserved.
    conflict = _decision_event(chosen_action="skill_b", propensity=0.0)
    with pytest.raises(IdempotencyKeyConflictError):
        evidence_mod.checkpoint(cfg, db_conn, conflict)

    loaded = evidence_mod.load_event(cfg, event.event_id)
    assert loaded is not None
    assert loaded.chosen_action == "skill_a"

    row = db_conn.execute(
        "SELECT COUNT(*) AS n FROM evidence_event_projection WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert int(row["n"]) == 1


def test_provenance_and_support() -> None:
    """AC-S09-04: unsupported counterfactual / delayed self-report stay unknown."""
    verdict = evidence_mod.evaluate_support(
        behavior_policy_id="dense-v1",
        propensity=1.0,
        chosen_action="skill_a",
        candidate_action="skill_b",
        verifier=evidence_mod.VerifierRef(
            type="deterministic_test",
            id="suite",
            version="1",
        ),
        outcome="success",
        source_tier=2,
    )
    assert verdict.status == "insufficient_support"
    assert verdict.counterfactual_efficacy == "unknown"

    delayed = evidence_mod.evaluate_support(
        behavior_policy_id="dense-v1",
        propensity=1.0,
        chosen_action="skill_a",
        candidate_action="skill_a",
        verifier=evidence_mod.VerifierRef(
            type="self_reported",
            id="user",
            version="1",
        ),
        outcome="success",
        delayed_feedback=True,
        source_tier=1,
    )
    assert delayed.status == "unknown"
    assert delayed.counterfactual_efficacy == "unknown"
    assert delayed.verifier_type == "self_reported"
    assert delayed.source_tier == 1

    inferred = evidence_mod.evaluate_support(
        behavior_policy_id="dense-v1",
        propensity=0.4,
        chosen_action="skill_a",
        candidate_action="skill_a",
        verifier=evidence_mod.VerifierRef(
            type="inferred",
            id="passive",
            version="1",
        ),
        outcome="success",
        source_tier=0,
    )
    assert inferred.status == "unknown"


def test_enqueue_receipt_never_persists_raw_query(cfg, db_conn) -> None:
    receipt = evidence_mod.enqueue_decision_receipt(
        cfg,
        query="SECRET_SENTINEL_raw_prompt_xyz",
        candidate_ids=["skill_a"],
        policy_id="dense-v1",
        policy_digest="d1",
        source_tier=0,
    )
    assert "SECRET" not in receipt.query_fingerprint
    assert receipt.query_fingerprint
    assert (
        db_conn.execute("SELECT COUNT(*) AS n FROM evidence_event_projection").fetchone()["n"] == 0
    )


def test_refuse_raw_query_in_durable_event(cfg, db_conn) -> None:
    event = _decision_event(extra={"query": "SECRET_SENTINEL"})
    with pytest.raises(InvalidInputError):
        evidence_mod.checkpoint(cfg, db_conn, event)
