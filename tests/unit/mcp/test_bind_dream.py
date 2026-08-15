"""Direct (non-stdio) unit coverage for ``mcp/bind_dream.py``: ``consolidate()``
now really enqueues (M4, ``core.dream.enqueue``); ``nucleate()`` still
raises ``not_implemented`` (M6, distillation)."""

from __future__ import annotations

import pytest

from magicite.errors import NotImplementedToolError
from magicite.mcp import bind_dream
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import ConsolidateInput, NucleateInput


def test_consolidate_enqueues_a_run(cfg, db_conn) -> None:
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=None)
    out = bind_dream.consolidate(ctx, ConsolidateInput())
    assert out.enqueued is True
    assert out.status == "queued"
    assert out.consolidation_id


def test_consolidate_is_idempotent_while_queued(cfg, db_conn) -> None:
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=None)
    first = bind_dream.consolidate(ctx, ConsolidateInput())
    second = bind_dream.consolidate(ctx, ConsolidateInput(manual_trigger=True))
    assert second.consolidation_id == first.consolidation_id
    assert second.enqueued is False


def test_nucleate_still_not_implemented(cfg, db_conn) -> None:
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=None)
    with pytest.raises(NotImplementedToolError):
        bind_dream.nucleate(ctx, NucleateInput())
