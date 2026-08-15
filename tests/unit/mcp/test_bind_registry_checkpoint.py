"""Direct (non-stdio) unit coverage for ``mcp/bind_registry.py::checkpoint()``
(M4: Dream's phase 7 in isolation, spec §3.3 tool 10)."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
from magicite.mcp import bind_registry
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import CheckpointInput

PROTON = "proton-ge-proton-downgrade"


def test_checkpoint_tool_reports_write_ratio(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    signals_mod.signal_use(cfg, db_conn, skill_ids=[proton_id], session_id="s1")

    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    out = bind_registry.checkpoint(ctx, CheckpointInput())

    assert 0.0 <= out.write_ratio <= 1.0
    assert isinstance(out.modified_engrams, list)
    assert out.checkpointed == len(out.modified_engrams)


def test_checkpoint_tool_is_idempotent_over_unchanged_state(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)

    first = bind_registry.checkpoint(ctx, CheckpointInput())
    second = bind_registry.checkpoint(ctx, CheckpointInput())
    assert second.checkpointed == 0
    assert second.modified_engrams == []
    assert first  # keep first referenced; both are legitimate assertions
