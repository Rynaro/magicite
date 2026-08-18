"""AC-019: replaying a write tool with the same request_id and identical
arguments returns the cached response rather than repeating the side
effect. The mechanism itself (``eph_idempotency``) lives in
``mcp/app.py::dispatch_call`` and has applied to every write tool since M0;
this suite is the honest proving test the AC calls for, exercised against
``signal_use`` (an M3 tool whose side effect -- an inserted ``eph_tag`` row
-- is easy to observe directly)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from magicite.core import registry as registry_mod
from magicite.errors import ErrorCode
from magicite.mcp import app as app_mod

PROTON = "proton-ge-proton-downgrade"


def _tag_count(conn, session_id: str, engram_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag WHERE session_id = ? AND engram_id = ?",
        (session_id, engram_id),
    ).fetchone()["n"]


def test_replay_returns_cached_response(cfg, embedder) -> None:
    """GIVEN a write tool called twice with the same request_id and
    identical arguments
    WHEN the second call arrives
    THEN the server SHALL return the stored response without repeating the
    side effect."""
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        engram_id = state.conn.execute(
            "SELECT id FROM engram WHERE name = ?", (PROTON,)
        ).fetchone()["id"]

        args = {"skill_ids": [PROTON], "session_id": "s1", "request_id": "req-1"}

        first = app_mod.dispatch_call(state, "signal_use", args)
        assert first.is_error is False
        assert _tag_count(state.conn, "s1", engram_id) == 1

        second = app_mod.dispatch_call(state, "signal_use", args)
        assert second.is_error is False
        assert second.structured_content == first.structured_content
        # the side effect was NOT repeated: still exactly one tag row.
        assert _tag_count(state.conn, "s1", engram_id) == 1
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_replay_with_different_arguments_conflicts(cfg, embedder) -> None:
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")

        first = app_mod.dispatch_call(
            state, "signal_use", {"skill_ids": [PROTON], "session_id": "s1", "request_id": "req-1"}
        )
        assert first.is_error is False

        conflict = app_mod.dispatch_call(
            state,
            "signal_use",
            {"skill_ids": [PROTON], "session_id": "s2", "request_id": "req-1"},  # same id, different args
        )
        assert conflict.is_error is True
        assert conflict.structured_content["code"] == ErrorCode.IDEMPOTENCY_KEY_CONFLICT.value
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_idempotency_is_keyed_by_tool_not_just_request_id(cfg, embedder) -> None:
    """M5 security fix #2: before the fix, ``eph_idempotency`` was keyed
    on ``request_id`` alone. ``sync``/``checkpoint``/``consolidate`` are
    all near-argument-less beyond ``request_id``, so a caller reusing the
    same ``request_id`` across tools produced an identical ``args_sha256``
    -- the lookup, ignoring the tool name, then replayed whichever tool
    ran *first*'s cached response verbatim for every subsequent tool that
    reused the id: ``checkpoint()``/``consolidate()`` silently returned
    ``sync()``'s payload, no-op'ing their own real side effect while
    reporting success (breaking AC-019's "identical arguments" contract
    and letting a caller pre-burn a request_id to no-op a later,
    legitimate call to a different tool)."""
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")

        sync_result = app_mod.dispatch_call(state, "sync", {"request_id": "shared-id"})
        assert sync_result.is_error is False
        assert "synced" in sync_result.structured_content

        checkpoint_result = app_mod.dispatch_call(state, "checkpoint", {"request_id": "shared-id"})
        assert checkpoint_result.is_error is False
        assert "checkpointed" in checkpoint_result.structured_content
        assert "synced" not in checkpoint_result.structured_content, (
            "checkpoint() must not silently replay sync()'s cached response"
        )

        consolidate_result = app_mod.dispatch_call(state, "consolidate", {"request_id": "shared-id"})
        assert consolidate_result.is_error is False
        assert "consolidation_id" in consolidate_result.structured_content
        assert "checkpointed" not in consolidate_result.structured_content
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_idempotency_replay_still_works_within_the_same_tool(cfg, embedder) -> None:
    """The other half of the fix: replay is still correct WITHIN one tool
    reusing a request_id (AC-019 is unaffected by the tool-keying)."""
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        first = app_mod.dispatch_call(state, "sync", {"request_id": "req-x"})
        second = app_mod.dispatch_call(state, "sync", {"request_id": "req-x"})
        assert first.is_error is False
        assert second.structured_content == first.structured_content
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_replay_without_request_id_repeats_the_side_effect(cfg, embedder) -> None:
    """Negative control: omitting request_id is NOT idempotent -- two calls
    with identical arguments but no request_id both execute (this is what
    makes the per-session cap test in test_signals.py meaningful at all)."""
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        engram_id = state.conn.execute(
            "SELECT id FROM engram WHERE name = ?", (PROTON,)
        ).fetchone()["id"]

        args = {"skill_ids": [PROTON], "session_id": "s1"}
        app_mod.dispatch_call(state, "signal_use", args)
        app_mod.dispatch_call(state, "signal_use", args)

        assert _tag_count(state.conn, "s1", engram_id) == 2
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_expired_idempotency_key_executes_again(cfg, embedder) -> None:
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        engram_id = state.conn.execute(
            "SELECT id FROM engram WHERE name = ?", (PROTON,)
        ).fetchone()["id"]
        args = {"skill_ids": [PROTON], "session_id": "s1", "request_id": "expires"}
        first = app_mod.dispatch_call(state, "signal_use", args)
        assert first.is_error is False
        state.conn.execute(
            "UPDATE eph_idempotency SET expires_at = ? WHERE tool = ? AND request_id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "signal_use", "expires"),
        )

        second = app_mod.dispatch_call(state, "signal_use", args)

        assert second.is_error is False
        assert _tag_count(state.conn, "s1", engram_id) == 2
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_pending_idempotency_reservation_does_not_repeat_side_effect(cfg, embedder) -> None:
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        args = {"skill_ids": [PROTON], "session_id": "s1", "request_id": "pending"}
        args_hash = app_mod._args_sha256(args)
        now = datetime.now(UTC)
        state.conn.execute(
            "INSERT INTO eph_idempotency "
            "(tool, request_id, args_sha256, response_json, created_at, expires_at, state) "
            "VALUES (?, ?, ?, '', ?, ?, 'pending')",
            (
                "signal_use",
                "pending",
                args_hash,
                now.isoformat(),
                (now + timedelta(days=1)).isoformat(),
            ),
        )

        result = app_mod.dispatch_call(state, "signal_use", args)

        assert result.is_error is True
        assert result.structured_content["code"] == ErrorCode.BUSY.value
        assert state.conn.execute("SELECT COUNT(*) AS n FROM eph_tag").fetchone()["n"] == 0
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_idempotency_hash_does_not_persist_adapter_secret_verifier() -> None:
    common = {"skill_ids": [PROTON], "session_id": "s1", "request_id": "redacted"}
    first = app_mod._args_sha256({**common, "adapter_token": "weak-one"})
    second = app_mod._args_sha256({**common, "adapter_token": "weak-two"})

    assert first == second
