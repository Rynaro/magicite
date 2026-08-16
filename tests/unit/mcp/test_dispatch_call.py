"""``mcp/app.py::dispatch_call`` cross-cutting behavior beyond idempotency
(``tests/unit/mcp/test_idempotency.py``): the Tier-0 passive-inference
ledger (spec §3.3, ``obs/events.py``) and the G1 connection routing
(``tests/acceptance/test_p0_hot_path.py`` proves the guard itself; this
proves *every* tool -- not just the ones under direct AC-013 test -- gets
the connection its risk class implies)."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.mcp import app as app_mod


def _events(conn, tool: str):
    return conn.execute("SELECT * FROM eph_event WHERE tool = ?", (tool,)).fetchall()


def test_every_successful_call_appends_a_tier0_ledger_row(cfg) -> None:
    state = app_mod.build_state(cfg)
    try:
        result = app_mod.dispatch_call(state, "introspect", {})
        assert result.is_error is False
        rows = _events(state.conn, "introspect")
        assert len(rows) == 1
        assert rows[0]["signal_tier"] == 0
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_route_gets_both_the_generic_ledger_row_and_its_own_specific_one(cfg, embedder) -> None:
    """spec §3.3: "every tool invocation appends an eph_event row... route
    additionally increments eph_bookkeeping and records the returned
    candidate set" -- two rows for one route() call is correct, not a
    double-count bug."""
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        result = app_mod.dispatch_call(state, "route", {"query": "steam wont open", "k": 3})
        assert result.is_error is False
        rows = _events(state.conn, "route")
        assert len(rows) == 2
        assert {r["signal_tier"] for r in rows} == {0}
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_idempotency_replay_does_not_append_a_second_ledger_row(cfg, embedder) -> None:
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        args = {"skill_ids": ["proton-ge-proton-downgrade"], "session_id": "s1", "request_id": "r1"}
        app_mod.dispatch_call(state, "signal_use", args)
        # signal_use writes its own additional event (core/signals.py, same
        # pattern as route()'s specific bookkeeping) alongside dispatch_call's
        # generic one -- 2 rows for one genuine call, same as the route()
        # case above; the replay below must add zero more.
        after_first = len(_events(state.conn, "signal_use"))
        assert after_first == 2

        app_mod.dispatch_call(state, "signal_use", args)
        assert len(_events(state.conn, "signal_use")) == after_first
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_route_ledger_row_uses_the_resolved_not_raw_session_id(cfg, embedder) -> None:
    """M4 fix: a caller that omits session_id gets one server-minted
    (spec §3.3's session resolution rule) -- the generic Tier-0 ledger row
    dispatch_call writes for the call must be stamped with THAT resolved
    id, not the raw (None) input, or the row is silently orphaned from the
    session that actually produced it."""
    state = app_mod.build_state(cfg)
    try:
        registry_mod.register(cfg, state.writer_conn, embedder, path=".magicite/engrams")
        result = app_mod.dispatch_call(state, "route", {"query": "steam wont open", "k": 3})
        assert result.is_error is False
        minted_session_id = result.structured_content["session_id"]
        assert minted_session_id  # a real uuid4, not empty/None

        rows = _events(state.conn, "route")
        assert len(rows) == 2
        assert all(r["session_id"] == minted_session_id for r in rows)
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_a_failed_call_does_not_append_a_ledger_row(cfg) -> None:
    state = app_mod.build_state(cfg)
    try:
        result = app_mod.dispatch_call(state, "introspect", {"skill_id": "does-not-exist"})
        assert result.is_error is True
        assert _events(state.conn, "introspect") == []
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_hot_path_tools_get_the_ephemeral_connection(cfg) -> None:
    """A static metadata check only -- it would still pass even if
    ``dispatch_call`` ignored ``_WRITER_CONNECTION_TOOLS`` entirely (a
    mutation-testing finding, M5). The behavioural proof that
    ``dispatch_call`` actually *honours* this set -- dispatching a real
    write through it and observing the write's outcome differ by routing
    -- is ``tests/acceptance/test_p0_hot_path.py::
    test_connection_split_actually_gates_durable_writes``."""
    state = app_mod.build_state(cfg)
    try:
        assert "introspect" not in app_mod._WRITER_CONNECTION_TOOLS
        assert "signal_use" not in app_mod._WRITER_CONNECTION_TOOLS
        assert "session_end" not in app_mod._WRITER_CONNECTION_TOOLS
        assert "route" not in app_mod._WRITER_CONNECTION_TOOLS
        assert "load_skill_body" not in app_mod._WRITER_CONNECTION_TOOLS
        assert "signal_outcome" not in app_mod._WRITER_CONNECTION_TOOLS
        assert "flag_dead" not in app_mod._WRITER_CONNECTION_TOOLS
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_writer_class_tools_are_exactly_the_nine_r2_r3_mutators() -> None:
    assert app_mod._WRITER_CONNECTION_TOOLS == {
        "register",
        "sync",
        "checkpoint",
        "export",
        "consolidate",
        "nucleate",
        "sharpen",
        "promote",
        "archive",
    }
