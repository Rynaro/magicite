"""``core/audit.py``: Dream Phase 6 (spec §4.3 phase table row 6, "reports
only"). Writes ``runtime/audit-<run>.json``; never mutates ``engram``/
``edge`` rows."""

from __future__ import annotations

import json

from magicite.core import audit as audit_mod
from magicite.core import registry as registry_mod
from magicite.storage import durable as durable_mod

PROTON = "proton-ge-proton-downgrade"


def test_audit_report_is_reports_only(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    before = {
        dict(r)["id"]: dict(r)
        for r in db_conn.execute("SELECT * FROM engram").fetchall()
    }

    report = audit_mod.run_audit(cfg, db_conn, run_id="dream_test1")

    after = {
        dict(r)["id"]: dict(r)
        for r in db_conn.execute("SELECT * FROM engram").fetchall()
    }
    assert before == after, "Phase 6 must never mutate engram rows (spec: reports only)"
    assert report.registry_size == 7


def test_audit_flags_silent_engrams(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    db_conn.execute(
        "INSERT INTO eph_bookkeeping (engram_id, exposure_delta, route_returns) VALUES (?, 0, 5)",
        (proton_id,),
    )
    report = audit_mod.run_audit(cfg, db_conn, run_id="dream_test2")
    assert PROTON not in report.silent_engrams
    # the other 6 engrams never got a route -- they are silent.
    assert report.silent_count == 6


def test_audit_writes_a_json_report_file(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    audit_mod.run_audit(cfg, db_conn, run_id="dream_test3")
    path = cfg.runtime_dir / "audit-dream_test3.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "dream_test3"
    assert payload["registry_size"] == 7


def test_hub_detection(cfg, db_conn, embedder) -> None:
    """AC-030: GIVEN a registry where one engram absorbs more than 50% of
    routing traffic WHEN the audit phase runs THEN the audit report SHALL
    flag that engram as a black-hole hub."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    other_ids = [
        str(r["id"])
        for r in db_conn.execute("SELECT id FROM engram WHERE name != ?", (PROTON,)).fetchall()
    ]
    # PROTON absorbs 60 of 100 total route returns (60% > the AC-030
    # 50% bar, and above the docs/07 <30% traffic-share KPI target too).
    db_conn.execute(
        "INSERT INTO eph_bookkeeping (engram_id, exposure_delta, route_returns) VALUES (?, 0, 60)",
        (proton_id,),
    )
    per_other = 40 // len(other_ids)
    for oid in other_ids:
        db_conn.execute(
            "INSERT INTO eph_bookkeeping (engram_id, exposure_delta, route_returns) VALUES (?, 0, ?)",
            (oid, per_other),
        )

    report = audit_mod.run_audit(cfg, db_conn, run_id="dream_hub_test")

    assert PROTON in report.hub_candidates
    assert report.hub_traffic_share >= 0.5


def test_audit_coverage_gaps_report_dangling_needs(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    # proton-ge-proton-downgrade declares needs: [steam-prefix-access] which
    # DOES resolve in the toy registry, so no dangling depends_on edges
    # exist yet; assert the field is present and empty rather than absent.
    report = audit_mod.run_audit(cfg, db_conn, run_id="dream_test4")
    assert report.coverage_gaps == []


def test_yields_reported_as_metadata_only() -> None:
    """AC-027: ``yields`` remains portable metadata, never a graph edge."""
    assert "yields" not in durable_mod.EDGE_TYPE_FOR_FIELD
    assert "not wired to a DB edge type" in audit_mod.AuditReport(
        run_id="contract",
        generated_at="2026-08-18T00:00:00+00:00",
        registry_size=0,
    ).orphans_note
