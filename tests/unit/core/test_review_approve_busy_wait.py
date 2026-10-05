"""review_approve replay waits a bounded wall-clock budget for the lease holder."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from magicite.core import registry as registry_mod
from magicite.errors import BusyError

_WINNER = SimpleNamespace(
    event_id="evt-1", decision="admit", decision_id="d-1", engram_id="e", content_digest="x", actor="a"
)


class _FakeClock:
    """Virtual monotonic clock: sleep advances time, nothing really sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _BusyLease:
    def __init__(self, busy_for_s: float, clock: _FakeClock) -> None:
        self._until = clock.now + busy_for_s
        self._clock = clock
        self.attempts = 0

    def acquire(self) -> Any:
        self.attempts += 1
        if self._clock.now < self._until:
            raise BusyError("writer lease flock is held by another process")
        return contextlib.nullcontext()

    def assert_owned(self) -> None:
        return None


def _setup(monkeypatch: pytest.MonkeyPatch, busy_for_s: float) -> _BusyLease:
    clock = _FakeClock()
    lease = _BusyLease(busy_for_s, clock)
    monkeypatch.setattr(registry_mod, "time", clock)
    monkeypatch.setattr(registry_mod, "_cross_process_lease", lambda *a, **k: lease)
    monkeypatch.setattr(registry_mod.lease_mod, "writer_lease", contextlib.nullcontext)
    monkeypatch.setattr(registry_mod.trust_mod, "list_decisions", lambda cfg: [_WINNER])
    monkeypatch.setattr(registry_mod.trust_mod, "live_content_digest", lambda *a, **k: "x")
    monkeypatch.setattr(registry_mod.trust_mod, "live_resource_digest", lambda *a, **k: None)
    monkeypatch.setattr(registry_mod.trust_mod, "admission_still_valid", lambda *a, **k: True)
    monkeypatch.setattr(registry_mod.lifecycle_mod, "apply_local_admission", lambda *a, **k: "verified")
    monkeypatch.setattr(registry_mod.approvals_mod, "propose", lambda *a, **k: None)
    return lease


def _call(event_id: str | None) -> Any:
    return registry_mod.review_approve(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        engram_id="e",
        expected_digest="x",
        actor="a",
        event_id=event_id,
    )


def test_replay_outlasts_old_32x10ms_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # Busy for 5s of virtual time: far beyond the old ~0.32s / 32-attempt budget.
    lease = _setup(monkeypatch, busy_for_s=5.0)
    assert _call("evt-1") is _WINNER
    assert lease.attempts > 32


def test_replay_busy_beyond_deadline_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, busy_for_s=registry_mod._REPLAY_BUSY_WAIT_S * 10)
    with pytest.raises(BusyError):
        _call("evt-1")


def test_no_event_id_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = _setup(monkeypatch, busy_for_s=5.0)
    with pytest.raises(BusyError):
        _call(None)
    assert lease.attempts == 1


@pytest.fixture
def approval_subject(cfg, db_conn, embedder):
    from tests.conftest import TOY_ENGRAMS_DIR

    incoming = cfg.project_root / "incoming"
    incoming.mkdir()
    source = TOY_ENGRAMS_DIR / "steam-runtime-repair.egr.md"
    (incoming / source.name).write_text(
        source.read_text().replace("provenance: authored", "provenance: imported")
    )
    outcome = registry_mod.register(cfg, db_conn, embedder, path="incoming", fmt="egr")
    assert outcome.ingested == 1
    subject = outcome.registered[0].id
    row = db_conn.execute(
        "SELECT content_sha256,verification_status FROM engram WHERE id=?", (subject,)
    ).fetchone()
    assert row["verification_status"] == "pending"
    return dict(
        engram_id=subject, expected_digest=row["content_sha256"], actor="reviewer", event_id="partial"
    )


@pytest.mark.parametrize("boundary", ["admission", "audit", "audit-db"])
def test_postcommit_busy_is_failure_then_replay_completes_once(
    boundary, approval_subject, cfg, db_conn, monkeypatch
):
    """AC-R3-01/02/03: interruption must be visible; replay repairs exact effects."""
    import json

    module, name = {
        "admission": (registry_mod.lifecycle_mod, "apply_local_admission"),
        "audit": (registry_mod.approvals_mod, "propose"),
        "audit-db": (registry_mod.approvals_mod, "_upsert_row"),
    }[boundary]
    original = getattr(module, name)
    armed = True

    def stop_once(*args, **kwargs):
        nonlocal armed
        if armed:
            armed = False
            raise BusyError("postcommit boundary lost ownership")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, stop_once)
    with pytest.raises(BusyError):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    decisions = [d for d in registry_mod.trust_list(cfg) if d.event_id == "partial"]
    assert len(decisions) == 1
    if boundary == "admission":
        row = db_conn.execute(
            "SELECT verification_status FROM engram WHERE id=?", (approval_subject["engram_id"],)
        ).fetchone()
        assert row["verification_status"] == "pending"
    decision = registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert decision.decision_id == decisions[0].decision_id
    row = db_conn.execute(
        "SELECT verification_status FROM engram WHERE id=?", (decision.engram_id,)
    ).fetchone()
    assert row["verification_status"] == "verified"
    rows = db_conn.execute("SELECT * FROM approval WHERE op='trust_approve'").fetchall()
    assert len(rows) == 1
    mirrors = sorted(cfg.approvals_dir.glob("*.json"))
    assert len(mirrors) == 1
    mirror = mirrors[0].read_bytes()
    record = json.loads(mirror)
    assert record["payload"]["decision_id"] == decision.decision_id
    assert len(record["audit_log"]) == 1
    for _ in range(3):
        assert (
            registry_mod.review_approve(cfg, db_conn, **approval_subject).decision_id == decision.decision_id
        )
    assert mirrors[0].read_bytes() == mirror
    assert len(db_conn.execute("SELECT id FROM approval WHERE op='trust_approve'").fetchall()) == 1
    assert len([d for d in registry_mod.trust_list(cfg) if d.event_id == "partial"]) == 1


def test_lost_ownership_does_not_retry_or_write_local_effects(approval_subject, cfg, db_conn, monkeypatch):
    """AC-R3-04: fail at ownership boundary, without converting it to acquisition retry."""
    original_approve = registry_mod.trust_mod.approve
    original_lease = registry_mod._cross_process_lease
    lost = False
    acquisitions = []
    effects = []

    def approve(*args, **kwargs):
        nonlocal lost
        decision = original_approve(*args, **kwargs)
        lost = True
        return decision

    def lease(*args, **kwargs):
        held = original_lease(*args, **kwargs)
        acquisitions.append(held)
        original_assert = held.assert_owned

        def assert_owned():
            if lost:
                raise BusyError("writer ownership was lost")
            original_assert()

        monkeypatch.setattr(held, "assert_owned", assert_owned)
        return held

    monkeypatch.setattr(registry_mod, "_cross_process_lease", lease)
    monkeypatch.setattr(registry_mod.trust_mod, "approve", approve)
    monkeypatch.setattr(
        registry_mod.lifecycle_mod, "apply_local_admission", lambda *a, **k: effects.append("local")
    )
    monkeypatch.setattr(registry_mod.approvals_mod, "propose", lambda *a, **k: effects.append("audit"))
    clock = _FakeClock()
    monkeypatch.setattr(registry_mod, "time", clock)
    with pytest.raises(BusyError):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert len(acquisitions) == 1
    assert effects == []


def test_replay_cannot_apply_a_different_subject_or_digest(approval_subject, cfg, db_conn):
    from magicite.errors import InvalidInputError

    registry_mod.review_approve(cfg, db_conn, **approval_subject)
    for changed in ({"engram_id": "other"}, {"expected_digest": "0" * 64}):
        with pytest.raises(InvalidInputError):
            registry_mod.review_approve(cfg, db_conn, **(approval_subject | changed))


def test_ownership_loss_after_admission_stops_before_audit(approval_subject, cfg, db_conn, monkeypatch):
    """AC-R3-04: the second local boundary is also fenced."""
    original_lease = registry_mod._cross_process_lease
    original_admit = registry_mod.lifecycle_mod.apply_local_admission
    lost = False
    audits = []

    def lease(*args, **kwargs):
        held = original_lease(*args, **kwargs)
        original_assert = held.assert_owned

        def assert_owned():
            if lost:
                raise BusyError("ownership lost after admission")
            original_assert()

        monkeypatch.setattr(held, "assert_owned", assert_owned)
        return held

    def admit(*args, **kwargs):
        nonlocal lost
        result = original_admit(*args, **kwargs)
        lost = True
        return result

    monkeypatch.setattr(registry_mod, "_cross_process_lease", lease)
    monkeypatch.setattr(registry_mod.lifecycle_mod, "apply_local_admission", admit)
    monkeypatch.setattr(registry_mod.approvals_mod, "propose", lambda *a, **k: audits.append("audit"))
    with pytest.raises(BusyError):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert audits == []
    assert not list(cfg.approvals_dir.glob("*.json"))
    row = db_conn.execute(
        "SELECT verification_status FROM engram WHERE id=?", (approval_subject["engram_id"],)
    ).fetchone()
    assert row["verification_status"] == "verified"


def test_completed_event_replay_cannot_resurrect_revoked_admission(approval_subject, cfg, db_conn):
    from magicite.errors import InvalidInputError

    decision = registry_mod.review_approve(cfg, db_conn, **approval_subject)
    registry_mod.review_revoke(cfg, db_conn, engram_id=decision.engram_id, actor="revoker")
    before = {p.name: p.read_bytes() for p in cfg.approvals_dir.glob("*.json")}
    with pytest.raises(InvalidInputError):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    row = db_conn.execute(
        "SELECT verification_status FROM engram WHERE id=?", (decision.engram_id,)
    ).fetchone()
    assert row["verification_status"] == "pending"
    assert {p.name: p.read_bytes() for p in cfg.approvals_dir.glob("*.json")} == before


def test_completed_event_replay_revalidates_live_resources(approval_subject, cfg, db_conn, monkeypatch):
    from magicite.errors import InvalidInputError

    registry_mod.review_approve(cfg, db_conn, **approval_subject)
    before = {p.name: p.read_bytes() for p in cfg.approvals_dir.glob("*.json")}
    monkeypatch.setattr(registry_mod.trust_mod, "live_resource_digest", lambda *a, **k: "0" * 64)
    with pytest.raises(InvalidInputError):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert {p.name: p.read_bytes() for p in cfg.approvals_dir.glob("*.json")} == before


def test_replay_preserves_pre_idempotency_audit_identity(approval_subject, cfg, db_conn):
    import json

    registry_mod.review_approve(cfg, db_conn, **approval_subject)
    old = next(cfg.approvals_dir.glob("*.json"))
    data = json.loads(old.read_bytes())
    original_id = data["id"]
    data["id"] = "appr_legacy"
    legacy = cfg.approvals_dir / "appr_legacy.json"
    legacy.write_text(json.dumps(data))
    old.unlink()
    db_conn.execute("DELETE FROM approval WHERE id=?", (original_id,))
    before = legacy.read_bytes()
    registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert legacy.read_bytes() == before
    assert list(cfg.approvals_dir.glob("*.json")) == [legacy]
    assert [r["id"] for r in db_conn.execute("SELECT id FROM approval WHERE op='trust_approve'")] == [
        "appr_legacy"
    ]


@pytest.mark.parametrize("damage", ["missing", "empty", "wrong-operation", "wrong-actor", "wrong-state"])
def test_replay_refuses_incomplete_or_invalid_audit(approval_subject, cfg, db_conn, damage):
    """Independent VIGIL reproduction: mirror must retain the proposal audit."""
    import json

    from magicite.errors import InvalidInputError

    registry_mod.review_approve(cfg, db_conn, **approval_subject)
    path = next(cfg.approvals_dir.glob("*.json"))
    data = json.loads(path.read_bytes())
    if damage == "missing":
        data.pop("audit_log")
    elif damage == "empty":
        data["audit_log"] = []
    elif damage == "wrong-operation":
        data["audit_log"][0]["operation"] = "not-a-proposal"
    elif damage == "wrong-actor":
        data["audit_log"][0]["actor"] = "different-actor"
    else:
        data["state"] = "not-a-state"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    row_before = dict(db_conn.execute("SELECT * FROM approval WHERE id=?", (data["id"],)).fetchone())
    with pytest.raises(InvalidInputError, match="audit.*reconciliation"):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert path.read_bytes() == before
    assert dict(db_conn.execute("SELECT * FROM approval WHERE id=?", (data["id"],)).fetchone()) == row_before


def test_replay_refuses_empty_db_audit_when_mirror_missing(approval_subject, cfg, db_conn):
    import json

    from magicite.errors import InvalidInputError

    registry_mod.review_approve(cfg, db_conn, **approval_subject)
    path = next(cfg.approvals_dir.glob("*.json"))
    data = json.loads(path.read_bytes())
    path.unlink()
    row = db_conn.execute("SELECT payload_json FROM approval WHERE id=?", (data["id"],)).fetchone()
    payload = json.loads(row["payload_json"])
    payload["audit_log"] = []
    db_conn.execute("UPDATE approval SET payload_json=? WHERE id=?", (json.dumps(payload), data["id"]))
    with pytest.raises(InvalidInputError, match="audit.*reconciliation"):
        registry_mod.review_approve(cfg, db_conn, **approval_subject)
    assert not path.exists()
