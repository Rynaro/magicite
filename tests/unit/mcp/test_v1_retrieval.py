"""AC-S11-01: load_skill_body returns stale_decision without the body."""

from __future__ import annotations

import hashlib
from pathlib import Path

from magicite.core import registry as registry_mod
from magicite.mcp import bind_retrieval
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import LoadSkillBodyInput, RouteInput


def test_stale_body_denied(cfg, db_conn, embedder) -> None:
    """GIVEN a decision whose body changed
    WHEN load_skill_body runs with the prior content digest
    THEN the call SHALL return stale_decision without the body.
    """
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    ctx = ToolContext(cfg=cfg, conn=db_conn, embedder=embedder)
    routed = bind_retrieval.route(ctx, RouteInput(query="rollback proton for a steam game", k=3))
    assert routed.candidates
    name = routed.candidates[0].name
    engram_id = routed.candidates[0].id
    expected = routed.selected_content_digests.get(engram_id)
    if not expected:
        row0 = db_conn.execute(
            "SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)
        ).fetchone()
        expected = str(row0["content_sha256"])

    row = db_conn.execute("SELECT path FROM engram WHERE id = ?", (engram_id,)).fetchone()
    path = Path(cfg.project_root) / row["path"]
    path.write_text(path.read_text(encoding="utf-8") + "\n# stale-body-probe\n", encoding="utf-8")
    new_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    db_conn.execute("UPDATE engram SET content_sha256 = ? WHERE id = ?", (new_digest, engram_id))
    db_conn.commit()

    out = bind_retrieval.load_skill_body(
        ctx,
        LoadSkillBodyInput(
            name=name,
            level="L2",
            expected_content_digest=expected,
            expected_policy_digest=routed.policy_digest,
        ),
    )
    assert out.status == "stale_decision"
    assert out.procedure == ""
    assert out.pitfalls == ""
    assert out.examples is None
    assert "stale_decision" in out.reason_codes
