"""S09 evidence recovery + privacy (AC-S09-02, AC-S09-03)."""

from __future__ import annotations

import json
import os
import shutil

from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.storage import db as db_mod
from magicite.storage import ephemeral as ephemeral_mod

SECRET = "SECRET_SENTINEL_raw_query_value"


def _decision_event(**overrides):
    base = dict(
        event_id="ev_rec_001",
        decision_id="dec_rec_001",
        event_type="decision",
        recorded_at="2026-09-29T12:00:00+00:00",
        candidate_ids=("skill_a",),
        chosen_action="skill_a",
        behavior_policy_id="dense-v1",
        behavior_policy_digest="digest_a",
        propensity=1.0,
        query_fingerprint="b" * 64,
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        source_tier=0,
        retention_class="operational",
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


def test_ack_survives_rebuild(cfg, db_conn) -> None:
    """AC-S09-02: acknowledged checkpoint remains after process death + rebuild."""
    cfg.ensure_dirs()
    event = _decision_event()
    ack = evidence_mod.checkpoint(cfg, db_conn, event)
    assert ack.durable is True

    # Simulate torn trailing write on the open segment, then process restart.
    open_seg = evidence_mod.evidence_dir(cfg) / "segments" / "open.events.jsonl"
    with open_seg.open("ab") as fh:
        fh.write(b'{"sequence":999,"torn":')  # incomplete JSON line
        fh.flush()
        os.fsync(fh.fileno())

    # Drop rebuildable projections (DB wipe of evidence tables only).
    db_conn.execute("DELETE FROM evidence_event_projection")
    db_conn.execute("UPDATE evidence_meta SET last_sequence = 0 WHERE id = 1")
    assert (
        db_conn.execute(
            "SELECT COUNT(*) AS n FROM evidence_event_projection WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()["n"]
        == 0
    )

    # Recovery: reopen + rebuild from authoritative file ledger.
    recovered = evidence_mod.rebuild_projections(cfg, db_conn)
    assert recovered >= 1
    loaded = evidence_mod.load_event(cfg, event.event_id)
    assert loaded is not None
    assert loaded.decision_id == event.decision_id
    row = db_conn.execute(
        "SELECT event_id, sequence FROM evidence_event_projection WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    assert int(row["sequence"]) == ack.sequence


def test_privacy_history_and_export(cfg, db_conn) -> None:
    """AC-S09-03: upgrade sanitization + checkpoint/export omit raw sentinels."""
    cfg.ensure_dirs()

    # Historical dirty eph_event row (pre-S09 / pre-S00 fingerprinting).
    ephemeral_mod.append_event(
        db_conn,
        session_id="s1",
        tool="route",
        signal_tier=0,
        engram_id=None,
        payload={"query": SECRET, "k": 3, "candidate_ids": ["x"]},
    )
    leaks_before = evidence_mod.inventory_raw_query_leakage(db_conn)
    assert any(SECRET in json.dumps(f) or "query" in f.get("keys", []) for f in leaks_before)

    report = evidence_mod.sanitize_ephemeral_history(db_conn)
    assert report.redacted_rows >= 1
    assert "query" in report.keys_removed
    assert evidence_mod.inventory_raw_query_leakage(db_conn) == []

    # Durable checkpoint with fingerprint only — never the sentinel.
    key = fk.load_or_create_fingerprint_key(cfg)
    fp = fk.query_fingerprint(SECRET, key=key)
    event = _decision_event(event_id="ev_priv_001", query_fingerprint=fp)
    evidence_mod.checkpoint(cfg, db_conn, event)

    # Managed stores must not contain the raw sentinel.
    root = evidence_mod.evidence_dir(cfg)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".key"}:
            continue  # key file is opaque bytes; still must not equal utf-8 sentinel
        text = path.read_bytes()
        assert SECRET.encode() not in text, f"raw sentinel found in {path}"

    export_dir = evidence_mod.export_evidence(cfg, event_ids=["ev_priv_001"])
    export_bytes = b"".join(p.read_bytes() for p in export_dir.rglob("*") if p.is_file())
    assert SECRET.encode() not in export_bytes
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fingerprint_key_exported"] is False

    # Projection row also clean.
    payload_rows = db_conn.execute("SELECT * FROM evidence_event_projection").fetchall()
    blob = json.dumps([dict(r) for r in payload_rows])
    assert SECRET not in blob


def test_migration_slot_six_applied(cfg, db_conn) -> None:
    assert db_mod.schema_version(db_conn) >= 6
    tables = {
        r["name"]
        for r in db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'evidence_%'"
        ).fetchall()
    }
    assert "evidence_meta" in tables
    assert "evidence_event_projection" in tables
    assert "evidence_tombstone_projection" in tables


def test_deletion_covers_managed_exports_only(cfg, db_conn, tmp_path) -> None:
    """AC-S09-05: purge managed export dir + registered paths; unregistered copies stay."""
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_export_mgmt", decision_id="dec_export_mgmt")
    evidence_mod.checkpoint(cfg, db_conn, event)

    managed = evidence_mod.export_evidence(cfg, event_ids=[event.event_id])
    assert managed.is_dir()
    managed_marker = managed / "events.jsonl"
    assert managed_marker.is_file()

    registered_outside = tmp_path / "registered_export_out"
    registered_outside.mkdir()
    # Caller-chosen destinations require an explicit allowed root (Round-3).
    object.__setattr__(cfg, "evidence_export_roots", (str(registered_outside.resolve()),))
    evidence_mod.export_evidence(
        cfg, event_ids=[event.event_id], export_dir=registered_outside / "exp"
    )
    assert (registered_outside / "exp" / "events.jsonl").is_file()

    unregistered = tmp_path / "operator_copy"
    unregistered.mkdir()
    shutil.copy2(managed_marker, unregistered / "events.jsonl")
    assert (unregistered / "events.jsonl").is_file()

    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="ac_s09_05")

    assert not (managed / "events.jsonl").exists()
    assert not (registered_outside / "exp" / "events.jsonl").exists()
    assert (unregistered / "events.jsonl").is_file()


def test_export_carries_copy_deletion_notice(cfg, db_conn) -> None:
    """AC-S09-06: default export artifact notices that deletion cannot follow copies."""
    cfg.ensure_dirs()
    event = _decision_event(event_id="ev_export_notice", decision_id="dec_export_notice")
    evidence_mod.checkpoint(cfg, db_conn, event)
    out = evidence_mod.export_evidence(cfg, event_ids=[event.event_id])
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    notice = manifest.get("privacy_deletion_notice") or manifest.get("copy_deletion_notice")
    assert notice
    assert "operator" in notice.lower() or "cop" in notice.lower()
    assert "cannot" in notice.lower() or "does not" in notice.lower() or "won't" in notice.lower()
