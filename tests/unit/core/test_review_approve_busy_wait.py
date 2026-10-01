"""review_approve replay waits a bounded wall-clock budget for the lease holder."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from magicite.core import registry as registry_mod
from magicite.errors import BusyError

_WINNER = SimpleNamespace(event_id="evt-1", decision="admit", decision_id="d-1")


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
