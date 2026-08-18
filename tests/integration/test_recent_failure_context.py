from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.core import router as router_mod


def test_recent_failure_context_is_populated_by_ingestion(cfg, db_conn, embedder) -> None:
    path = cfg.registry_dir / "proton-ge-proton-downgrade.egr.md"
    raw = path.read_text(encoding="utf-8")
    path.write_text(
        raw.replace(
            "1. Identify the game's Steam appid",
            "1. [fault: PROTON_REGRESSION] Identify the game's Steam appid",
            1,
        ),
        encoding="utf-8",
    )
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    row = db_conn.execute(
        "SELECT fault_class FROM engram_step WHERE engram_id = 'egr_b5320dfd' AND step_no = 1"
    ).fetchone()
    assert row["fault_class"] == "PROTON_REGRESSION"

    baseline = router_mod.route(
        cfg, db_conn, embedder, query="rollback ge-proton for steam", k=7
    )
    conditioned = router_mod.route(
        cfg,
        db_conn,
        embedder,
        query="rollback ge-proton for steam",
        context={"recent_failures": ["PROTON_REGRESSION"]},
        k=7,
    )
    baseline_score = next(c.score for c in baseline.candidates if c.id == "egr_b5320dfd")
    conditioned_score = next(c.score for c in conditioned.candidates if c.id == "egr_b5320dfd")
    assert conditioned_score > baseline_score
    assert conditioned.unresolved_context == []
