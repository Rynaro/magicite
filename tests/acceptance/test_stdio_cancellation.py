"""AC-S11-03: cancellation around durable commit + retry → at most one effect."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.mcp import app as app_mod

pytestmark = pytest.mark.acceptance

PROTON = "proton-ge-proton-downgrade"


def _die_after_response_staged(project_root: str, arguments: dict[str, object]) -> None:
    cfg = Config.load(Path(project_root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    state = app_mod.build_state(cfg)

    def stop(label: str) -> None:
        if label == "response_staged":
            os._exit(77)

    app_mod.dispatch_call(
        state,
        "signal_use",
        arguments,
        idempotency_fault_hook=stop,
    )


def test_commit_boundary(cfg, embedder) -> None:
    """GIVEN cancellation around durable commit followed by retry
    WHEN the client reconnects
    THEN the durable operation SHALL have at most one effect.
    """
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
    finally:
        state.conn.close()
        state.writer_conn.close()

    arguments: dict[str, object] = {
        "skill_ids": [PROTON],
        "session_id": "cancel-boundary",
        "request_id": "commit-boundary-1",
    }
    process = multiprocessing.get_context("spawn").Process(
        target=_die_after_response_staged,
        args=(str(cfg.project_root), arguments),
    )
    process.start()
    process.join(timeout=20)
    assert process.exitcode == 77

    recovered = app_mod.build_state(cfg)
    try:
        # First reconnect may see pending+staged and complete without re-executing.
        first = app_mod.dispatch_call(recovered, "signal_use", arguments)
        assert first.is_error is False
        engram_id = recovered.conn.execute(
            "SELECT id FROM engram WHERE name = ?", (PROTON,)
        ).fetchone()["id"]
        count_after_first = recovered.conn.execute(
            "SELECT COUNT(*) AS n FROM eph_tag WHERE session_id = ? AND engram_id = ?",
            ("cancel-boundary", engram_id),
        ).fetchone()["n"]

        second = app_mod.dispatch_call(recovered, "signal_use", arguments)
        assert second.is_error is False
        assert second.structured_content == first.structured_content
        count_after_second = recovered.conn.execute(
            "SELECT COUNT(*) AS n FROM eph_tag WHERE session_id = ? AND engram_id = ?",
            ("cancel-boundary", engram_id),
        ).fetchone()["n"]
        assert count_after_first == 1
        assert count_after_second == 1
    finally:
        recovered.conn.close()
        recovered.writer_conn.close()
