"""Direct (non-stdio) unit coverage for ``mcp/bind_dream.py``: ``consolidate()``
really enqueues (M4, ``core.dream.enqueue``); ``nucleate()`` really mines
frequent, uncovered paths and proposes them (M6, ``core/distill.py``)."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
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


def test_nucleate_proposes_a_frequent_uncovered_path(cfg, db_conn, embedder) -> None:
    """CR-3: nucleate() creates a `proposed` op='nucleate' approval per
    candidate and never writes an engram -- the host agent drafts the
    real .egr.md and calls register() (spec §3.3 tool 13's own note)."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    # Two skills with no existing composes/depends_on edge between them in
    # the toy registry -- a genuine coverage gap.
    names = ["nvidia-prime-render-offload", "lutris-wine-prefix-setup"]
    for i in range(5):
        sid = f"nuc-{i}"
        signals_mod.signal_use(cfg, db_conn, skill_ids=names, session_id=sid)
        signals_mod.signal_outcome(
            cfg, db_conn, valence=0.9, salience=0.9, skill_ids=names, session_id=sid
        )
    before_count = db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    out = bind_dream.nucleate(ctx, NucleateInput(min_support=5))

    assert len(out.candidates) == 1
    cand = out.candidates[0]
    assert cand.path_names == sorted(names)
    assert cand.support == 5
    assert cand.mean_valence > 0
    assert cand.draft_skeleton  # a non-empty scaffold string
    assert len(out.approval_ids) == 1
    row = db_conn.execute(
        "SELECT state, op, target_name FROM approval WHERE id = ?", (out.approval_ids[0],)
    ).fetchone()
    assert row["state"] == "proposed"
    assert row["op"] == "nucleate"
    # never writes an engram (CR-3: proposal only).
    after_count = db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]
    assert after_count == before_count


def test_nucleate_below_min_support_proposes_nothing(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    names = ["nvidia-prime-render-offload", "lutris-wine-prefix-setup"]
    signals_mod.signal_use(cfg, db_conn, skill_ids=names, session_id="one-off")
    signals_mod.signal_outcome(
        cfg, db_conn, valence=0.9, salience=0.9, skill_ids=names, session_id="one-off"
    )

    out = bind_dream.nucleate(ctx, NucleateInput(min_support=5))

    assert out.candidates == []
    assert out.approval_ids == []
