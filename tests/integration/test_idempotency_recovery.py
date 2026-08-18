"""Frozen AC-043: process death never repeats a handler effect."""

from __future__ import annotations

import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.mcp import app as app_mod

PROTON = "proton-ge-proton-downgrade"


def _die_after_event(project_root: str, arguments: dict[str, object]) -> None:
    cfg = Config.load(Path(project_root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    state = app_mod.build_state(cfg)

    def stop(label: str) -> None:
        if label == "event_committed":
            os._exit(91)

    app_mod.dispatch_call(
        state,
        "signal_use",
        arguments,
        idempotency_fault_hook=stop,
    )


def test_process_death_after_event_replays_staged_response(cfg, embedder) -> None:
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
    finally:
        state.conn.close()
        state.writer_conn.close()

    arguments: dict[str, object] = {
        "skill_ids": [PROTON],
        "session_id": "process-death",
        "request_id": "recover-after-event",
    }
    process = multiprocessing.get_context("spawn").Process(
        target=_die_after_event,
        args=(str(cfg.project_root), arguments),
    )
    process.start()
    process.join(timeout=20)
    assert process.exitcode == 91

    recovered_state = app_mod.build_state(cfg)
    try:
        replay = app_mod.dispatch_call(recovered_state, "signal_use", arguments)
        assert replay.is_error is False
        engram_id = recovered_state.conn.execute(
            "SELECT id FROM engram WHERE name = ?", (PROTON,)
        ).fetchone()["id"]
        count = recovered_state.conn.execute(
            "SELECT COUNT(*) AS n FROM eph_tag WHERE session_id = ? AND engram_id = ?",
            ("process-death", engram_id),
        ).fetchone()["n"]
        assert count == 1
        reservation = app_mod.inspect_idempotency_reservation(
            recovered_state.conn,
            tool="signal_use",
            request_id="recover-after-event",
        )
        assert reservation is not None
        assert reservation.state == "completed"
        assert reservation.response_staged is True
    finally:
        recovered_state.conn.close()
        recovered_state.writer_conn.close()


def test_operator_can_inspect_and_complete_an_unstaged_reservation(cfg) -> None:
    state = app_mod.build_state(cfg)
    try:
        arguments = {
            "skill_ids": [PROTON],
            "session_id": "operator-recovery",
            "request_id": "operator-pending",
        }
        digest = app_mod._args_sha256(arguments)
        now = datetime.now(UTC)
        state.conn.execute(
            "INSERT INTO eph_idempotency "
            "(tool, request_id, args_sha256, response_json, created_at, expires_at, state) "
            "VALUES (?, ?, ?, '', ?, ?, 'pending')",
            (
                "signal_use",
                "operator-pending",
                digest,
                now.isoformat(),
                (now + timedelta(days=1)).isoformat(),
            ),
        )
        inspected = app_mod.inspect_idempotency_reservation(
            state.conn, tool="signal_use", request_id="operator-pending"
        )
        assert inspected is not None
        assert inspected.state == "pending"
        assert inspected.response_staged is False

        recovered = app_mod.recover_idempotency_reservation(
            state.conn,
            tool="signal_use",
            request_id="operator-pending",
            expected_args_sha256=digest,
            actor="release-operator",
            reason="verified the external effect committed before worker termination",
            response={
                "tagged": [PROTON],
                "co_activation_candidates": [],
                "expires_at": now.isoformat(),
                "signal_tier": 1,
                "capped": [],
                "note": "operator-recovered response",
            },
        )
        assert recovered.state == "completed"
        replay = app_mod.dispatch_call(state, "signal_use", arguments)
        assert replay.is_error is False
        assert replay.structured_content["note"] == "operator-recovered response"
        assert state.conn.execute("SELECT COUNT(*) AS n FROM eph_tag").fetchone()["n"] == 0
        assert (
            state.conn.execute(
                "SELECT COUNT(*) AS n FROM eph_event WHERE tool = 'idempotency_recovery'"
            ).fetchone()["n"]
            == 1
        )
    finally:
        state.conn.close()
        state.writer_conn.close()
