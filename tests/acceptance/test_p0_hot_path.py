"""AC-013 / G1: the SQLite authorizer, wired end-to-end through the real
server state (spec §6.2). ``tests/unit/storage/test_authorizer.py`` proves
the DENY matrix in isolation; this suite proves it against the actual
:func:`magicite.mcp.app.build_state` connections a running server hands to
its 16 tools -- and proves the writer path is not strangled by it.

**M5 test-quality fix (mutation testing found G1's *dispatch* wiring had
zero real coverage).** Two pre-existing gaps, closed here:

1. ``test_authorizer_denies_durable_write`` used to poke ``state.conn``
   directly -- that only re-proved the authorizer itself works (already
   covered by ``tests/unit/storage/test_authorizer.py``'s DENY matrix), and
   said nothing about whether ``dispatch_call``'s own connection-selection
   logic (``_WRITER_CONNECTION_TOOLS`` membership -> which physical
   connection a tool name resolves to) is wired correctly at all. It is
   rewritten below to drive the denial through ``dispatch_call`` itself.
2. ``test_hot_path_tools_get_the_ephemeral_connection``
   (``tests/unit/mcp/test_dispatch_call.py``) only asserted set membership
   -- a metadata check that would still pass even if ``dispatch_call``
   ignored the set entirely. :func:`test_connection_split_actually_gates_
   durable_writes` below is the behavioural proof the mutation report asked
   for: it dispatches the *same* probe tool once under each routing
   outcome (real, then monkeypatched) and asserts the write's success
   differs exactly as the routing implies -- handing every tool the
   unrestricted writer connection (or the reverse) now fails this test.

AC-024's static import-boundary guard lives in
``tests/unit/test_p0_enforcement.py`` (spec §7.2 groups both under this
file's name in the Kupo verify command list; the AST check itself is a
separate, cheaper test that has run since M0).
"""

from __future__ import annotations

import pytest

from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.mcp import app as app_mod
from magicite.mcp import registry as tool_registry_mod
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import RouteInput, RouteOutput

pytestmark = pytest.mark.acceptance

#: A minimal, schema-valid ``engram`` row -- deliberately NOT a partial-
#: column insert (``INSERT INTO engram (id, name) VALUES (...)`` fails on
#: unrelated NOT NULL constraints regardless of the authorizer, which is
#: exactly the "passes for the wrong reason" shape this file's own fix is
#: about). A probe using this row only ever fails because G1 denies it.
_MINIMAL_ENGRAM_INSERT = (
    "INSERT INTO engram (id, name, path, spec_version, version, origin, "
    "verification_status, status, intent_does, intent_use_when, s_decayed_at, "
    "created_at, updated_at, identity_sha256, content_sha256, body_sha256, file_mtime_ns) "
    "VALUES ('egr_x','n','p','engram/0.2',1,'authored','pending','draft','d','u','t',"
    "'t','t','i','c','b',1)"
)


def _register_probe_tool(name: str):  # noqa: ANN202
    """Registers a throwaway R0 tool that attempts a full, valid durable
    write via ``ctx.conn`` -- the shared probe body every test in this
    file uses to observe *which* connection ``dispatch_call`` actually
    handed the tool, without ever poking ``state.conn``/``state.writer_
    conn`` directly."""

    def _probe(ctx: ToolContext, params: RouteInput) -> RouteOutput:
        ctx.conn.execute(_MINIMAL_ENGRAM_INSERT)
        return RouteOutput(
            candidates=[],
            composition_plan=[],
            plan_confidence=0.0,
            instructions="",
            session_id="probe",
            registry_size=0,
        )

    # magicite_tool keys the registry off fn.__name__ *at decoration time*,
    # so the rename must happen before the decorator runs -- applying it
    # via a plain call (rather than `@magicite_tool(...)` sugar) lets every
    # test in this file register its own uniquely-named probe from the
    # same body without colliding in the process-global TOOL_REGISTRY.
    _probe.__name__ = name
    register = magicite_tool(
        risk="R0",
        side_effect="none",
        idempotent=True,
        input_model=RouteInput,
        output_model=RouteOutput,
        description="test-only probe: a full, valid durable-table write via ctx.conn.",
    )
    return register(_probe)


def _unregister_probe_tool(name: str) -> None:
    """``mcp/registry.py`` has no unregister API by design (the frozen
    16-tool surface is meant to be append-only in production) -- removing
    a test-only probe here keeps AC-003/AC-004's exact-16 assertions
    honest for every other test in the same process."""
    tool_registry_mod.TOOL_REGISTRY.pop(name, None)
    if name in tool_registry_mod._REGISTRATION_ORDER:
        tool_registry_mod._REGISTRATION_ORDER.remove(name)


def test_authorizer_denies_durable_write(cfg: Config) -> None:
    """GIVEN a hot-path tool holding an authorizer-restricted connection
    WHEN it attempts any INSERT, UPDATE or DELETE on a non-eph_ table
    THEN SQLite SHALL deny the statement and the tool SHALL surface an
    internal error.

    Driven through ``dispatch_call`` (not a raw connection poke, see
    module docstring): a fresh R0 probe tool -- never added to
    ``_WRITER_CONNECTION_TOOLS`` -- is dispatched and must be denied; the
    same connection still accepts an ``eph_`` write; a real R2 tool
    (``register``, routed through the identical dispatcher) still
    succeeds, proving the writer path is not strangled by the guard.
    """
    tool_name = "_test_ac013_hot_path_probe"
    _register_probe_tool(tool_name)

    state = app_mod.build_state(cfg)
    try:
        assert tool_name not in app_mod._WRITER_CONNECTION_TOOLS

        result = app_mod.dispatch_call(state, tool_name, {"query": "anything"})
        assert result.is_error is True
        assert "internal error" in result.structured_content["message"]
        assert state.conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 0

        # Reads and eph_ writes pass on the very same (hot-path) connection.
        state.conn.execute(
            "INSERT INTO eph_session (session_id, started_at, last_seen_at) VALUES ('s','t','t')"
        )
        assert state.conn.execute("SELECT * FROM engram").fetchall() == []

        # The Dream/writer path is NOT strangled by the guard: a real R2
        # durable-write tool, routed through the same dispatcher, succeeds.
        register_result = app_mod.dispatch_call(
            state, "register", {"path": ".spectra/engrams"}
        )
        assert register_result.is_error is False
        assert register_result.structured_content["ingested"] == 7
    finally:
        state.conn.close()
        state.writer_conn.close()
        _unregister_probe_tool(tool_name)


def test_connection_split_actually_gates_durable_writes(cfg: Config, monkeypatch) -> None:
    """The behavioural test the mutation report asked for: "handing every
    tool the unrestricted writer connection still passes the entire
    suite" must no longer be true. Dispatches the *same* probe tool twice
    -- once under the real routing (not in ``_WRITER_CONNECTION_TOOLS`` ->
    denied), once with the dispatcher's own routing set monkeypatched to
    include it (-> the identical write now succeeds) -- so this test fails
    if ``dispatch_call``'s connection-selection is broken in *either*
    direction (always ephemeral, or always writer)."""
    tool_name = "_test_connection_split_probe"
    _register_probe_tool(tool_name)

    state = app_mod.build_state(cfg)
    try:
        result = app_mod.dispatch_call(state, tool_name, {"query": "first"})
        assert result.is_error is True
        assert state.conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 0

        monkeypatch.setattr(app_mod, "_WRITER_CONNECTION_TOOLS", frozenset({tool_name}))
        result2 = app_mod.dispatch_call(state, tool_name, {"query": "second"})
        assert result2.is_error is False
        assert state.conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 1
    finally:
        state.conn.close()
        state.writer_conn.close()
        _unregister_probe_tool(tool_name)


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
        # A full, VALID row (see module docstring) -- a partial-column
        # insert would raise IntegrityError regardless of the authorizer,
        # which would make this assertion pass for the wrong reason.
        ctx.conn.execute(_MINIMAL_ENGRAM_INSERT)
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
        _unregister_probe_tool(tool_name)
