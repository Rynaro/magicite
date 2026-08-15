"""AC-013 / G1: the SQLite authorizer, wired end-to-end through the real
server state (spec §6.2). ``tests/unit/storage/test_authorizer.py`` proves
the DENY matrix in isolation; this suite proves it against the actual
:func:`magicite.mcp.app.build_state` connections a running server hands to
its 16 tools -- and proves the writer path is not strangled by it.

AC-024's static import-boundary guard lives in
``tests/unit/test_p0_enforcement.py`` (spec §7.2 groups both under this
file's name in the Kupo verify command list; the AST check itself is a
separate, cheaper test that has run since M0).
"""

from __future__ import annotations

import sqlite3

import pytest

from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.mcp import app as app_mod
from magicite.mcp import registry as tool_registry_mod
from magicite.mcp.registry import magicite_tool

pytestmark = pytest.mark.acceptance


def test_authorizer_denies_durable_write(cfg: Config) -> None:
    """GIVEN a hot-path tool holding an authorizer-restricted connection
    WHEN it attempts any INSERT, UPDATE or DELETE on a non-eph_ table
    THEN SQLite SHALL deny the statement and the tool SHALL surface an
    internal error."""
    state = app_mod.build_state(cfg)
    try:
        # The hot path's own connection (what route/signal_use/introspect/...
        # actually receive from dispatch_call) denies the durable write.
        with pytest.raises(sqlite3.DatabaseError):
            state.conn.execute(
                "INSERT INTO engram (id, name, path, spec_version, version, origin, "
                "verification_status, status, intent_does, intent_use_when, s_decayed_at, "
                "created_at, updated_at, identity_sha256, content_sha256, body_sha256, file_mtime_ns) "
                "VALUES ('egr_x','n','p','engram/0.2',1,'authored','pending','draft','d','u','t',"
                "'t','t','i','c','b',1)"
            )
        assert state.conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 0

        # Reads and eph_ writes pass on the very same connection.
        state.conn.execute(
            "INSERT INTO eph_session (session_id, started_at, last_seen_at) VALUES ('s','t','t')"
        )
        assert state.conn.execute("SELECT * FROM engram").fetchall() == []

        # The Dream/writer path is NOT strangled by the guard: the same
        # logical write succeeds on the writer connection.
        state.writer_conn.execute(
            "INSERT INTO engram (id, name, path, spec_version, version, origin, "
            "verification_status, status, intent_does, intent_use_when, s_decayed_at, "
            "created_at, updated_at, identity_sha256, content_sha256, body_sha256, file_mtime_ns) "
            "VALUES ('egr_x','n','p','engram/0.2',1,'authored','pending','draft','d','u','t',"
            "'t','t','i','c','b',1)"
        )
        assert state.writer_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 1
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_writer_tools_are_not_strangled_by_the_authorizer(cfg: Config, embedder) -> None:
    """End-to-end (not just the raw connection): register() -- an R2
    durable-write tool -- must still succeed when routed through the real
    dispatcher, which is exactly where AC-013's guard is wired in."""
    state = app_mod.build_state(cfg)
    try:
        outcome = registry_mod.register(cfg, state.writer_conn, embedder, path=".spectra/engrams")
        assert outcome.ingested == 7
        assert outcome.validation_errors == []
    finally:
        state.conn.close()
        state.writer_conn.close()


def test_dispatch_call_surfaces_a_denied_write_as_an_internal_error(cfg: Config) -> None:
    """THEN the tool SHALL surface an internal error: a hot-path tool body
    that (incorrectly) attempted a durable write must not crash the
    dispatcher or leak a raw sqlite3 traceback -- it comes back as a normal
    MagiciteError-shaped ``is_error=True`` result, the same as any other
    tool failure."""
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import RouteInput, RouteOutput

    tool_name = "_test_misbehaving_hot_path_tool"

    @magicite_tool(
        risk="R0",
        side_effect="none",
        idempotent=True,
        input_model=RouteInput,
        output_model=RouteOutput,
        description="test-only: a hot-path tool that misbehaves and tries a durable write",
    )
    def _test_misbehaving_hot_path_tool(ctx: ToolContext, params: RouteInput) -> RouteOutput:
        ctx.conn.execute("INSERT INTO engram (id, name) VALUES ('x', 'y')")
        raise AssertionError("unreachable: the authorizer must have denied the write above")

    state = app_mod.build_state(cfg)
    try:
        result = app_mod.dispatch_call(state, tool_name, {"query": "anything"})
        assert result.is_error is True
        assert "internal error" in result.structured_content["message"]
        assert state.conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 0
    finally:
        state.conn.close()
        state.writer_conn.close()
        # This test registers a throwaway tool onto the process-global
        # TOOL_REGISTRY (mcp/registry.py has no unregister API by design --
        # the frozen 16-tool surface is meant to be append-only in
        # production). Removing it here keeps AC-003/AC-004's exact-16
        # assertions honest for every other test in the same process.
        tool_registry_mod.TOOL_REGISTRY.pop(tool_name, None)
        tool_registry_mod._REGISTRATION_ORDER.remove(tool_name)
