"""Round-5: authenticated purge marker, casefold deny, self-audit coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fk
from magicite.errors import InvalidInputError


@pytest.fixture(autouse=True)
def _reset():
    evidence_mod.set_checkpoint_fault_hook(None)
    evidence_mod.clear_receipt_buffer()
    yield
    evidence_mod.set_checkpoint_fault_hook(None)
    evidence_mod.clear_receipt_buffer()


def _event(**overrides):
    base = dict(
        event_id="ev_r5_001",
        decision_id="dec_r5_001",
        event_type="decision",
        recorded_at="2026-09-29T12:00:00+00:00",
        candidate_ids=("skill_a",),
        chosen_action="skill_a",
        behavior_policy_id="dense-v1",
        behavior_policy_digest="digest_a",
        propensity=1.0,
        query_fingerprint="a" * 64,
        fingerprint_scheme=fk.FINGERPRINT_SCHEME,
        source_tier=0,
        retention_class="operational",
    )
    base.update(overrides)
    return evidence_mod.EvidenceEvent(**base)


# ── Finding 1: purge_complete.marker must be authenticated ─────────────────


def test_forged_purge_marker_does_not_suppress_repurge(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    sentinel = "R5_FORGED_MARKER_SENTINEL"
    event = _event(
        event_id="ev_r5_forged_marker",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)

    def fault(label: str) -> None:
        if label == "after_tombstone":
            raise RuntimeError("injected after_tombstone")

    evidence_mod.set_checkpoint_fault_hook(fault)
    with pytest.raises(RuntimeError, match="after_tombstone"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r5_forged")
    evidence_mod.set_checkpoint_fault_hook(None)

    root = evidence_mod.evidence_dir(cfg)
    # Forge an unauthenticated marker claiming purge is complete for current generation.
    gen = evidence_mod._tombstone_generation(root)  # noqa: SLF001
    (root / "purge_complete.marker").write_text(
        json.dumps(
            {
                "purge_complete": True,
                "generation": gen,
                "updated_at": "2026-09-29T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    # Next mutation must ignore forged marker and finish physical purge.
    evidence_mod.checkpoint(
        cfg,
        db_conn,
        _event(
            event_id="ev_r5_trigger",
            decision_id="dec_r5_trigger",
            query_fingerprint="b" * 64,
        ),
    )
    for path in root.rglob("*.events.jsonl"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes(), f"sentinel still in {path}"


def test_valid_marker_invalidated_by_new_tombstone(cfg, db_conn, monkeypatch) -> None:
    cfg.ensure_dirs()
    first = _event(event_id="ev_r5_mark_a", decision_id="dec_r5_mark_a")
    evidence_mod.checkpoint(cfg, db_conn, first)
    evidence_mod.delete_event(cfg, db_conn, first.event_id, reason="r5_mark_a")

    purge_calls = {"n": 0}
    real_purge = evidence_mod._physically_purge_event_from_segments

    def counting_purge(root: Path, event_id: str):
        purge_calls["n"] += 1
        return real_purge(root, event_id)

    monkeypatch.setattr(evidence_mod, "_physically_purge_event_from_segments", counting_purge)

    # With a valid marker and nothing pending, physical purge is not re-run.
    evidence_mod.checkpoint(
        cfg,
        db_conn,
        _event(
            event_id="ev_r5_mark_b",
            decision_id="dec_r5_mark_b",
            query_fingerprint="c" * 64,
        ),
    )
    assert purge_calls["n"] == 0

    evidence_mod.delete_event(cfg, db_conn, "ev_r5_mark_b", reason="r5_mark_b")
    root = evidence_mod.evidence_dir(cfg)
    # Stale/mismatched marker must not suppress pending computation → purge path.
    (root / "purge_complete.marker").write_text(
        json.dumps(
            {
                "purge_complete": True,
                "pending_count": 0,
                "generation": "stale:0:0",
                "tombstone_digest": "0" * 64,
                "manifest_digest": "0" * 64,
                "mac": "0" * 64,
                "mac_scheme": "hmac-sha256/evidence-purge-marker-v1",
            }
        ),
        encoding="utf-8",
    )
    pending_calls = {"n": 0}
    real_pending = evidence_mod._pending_tombstone_payload_ids

    def counting_pending(root: Path):
        pending_calls["n"] += 1
        return real_pending(root)

    monkeypatch.setattr(evidence_mod, "_pending_tombstone_payload_ids", counting_pending)
    evidence_mod.checkpoint(
        cfg,
        db_conn,
        _event(
            event_id="ev_r5_mark_c",
            decision_id="dec_r5_mark_c",
            query_fingerprint="d" * 64,
        ),
    )
    assert pending_calls["n"] >= 1, "stale/mismatched marker must still compute pending ids"


# ── Finding 2: case-insensitive path alias denial ──────────────────────────


def test_hard_deny_casefold_path_logic() -> None:
    """Pure-logic: casefold-normalized comparison treats alias as under ancestor."""
    assert evidence_mod._path_equals_or_under_casefold(  # noqa: SLF001
        Path("/tmp/Evidence/SEGMENTS/a.events.jsonl"),
        Path("/tmp/evidence"),
    )
    assert not evidence_mod._path_equals_or_under_casefold(  # noqa: SLF001
        Path("/tmp/other/segments/a.events.jsonl"),
        Path("/tmp/evidence"),
    )


def test_hard_deny_case_insensitive_fs_alias(cfg, db_conn, tmp_path) -> None:
    cfg.ensure_dirs()
    root = evidence_mod._ensure_ledger_dirs(cfg)  # noqa: SLF001
    segments = root / "segments"
    probe = segments / "CASEFOLD_PROBE"
    probe.write_text("x", encoding="utf-8")
    alias = root.parent / root.name.upper() / "SEGMENTS" / "CASEFOLD_PROBE"
    # If the filesystem is case-sensitive, alias won't resolve to the same file.
    if not alias.exists():
        pytest.skip("filesystem is case-sensitive")
    try:
        if not alias.samefile(probe):
            pytest.skip("filesystem is case-sensitive")
    except OSError:
        pytest.skip("filesystem is case-sensitive")

    assert evidence_mod._is_hard_denied_purge_path(cfg, root, alias)  # noqa: SLF001


def test_truncated_tombstones_fail_closed_on_load(cfg, db_conn) -> None:
    """Self-audit: forged/truncated tombstone journal must not expose deleted data."""
    cfg.ensure_dirs()
    event = _event(event_id="ev_r5_tomb_trunc")
    evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r5_trunc")

    root = evidence_mod.evidence_dir(cfg)
    # Truncate journal without updating MAC.
    (root / "tombstones.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(InvalidInputError, match="tombstones\\.mac|HMAC|tombstone"):
        evidence_mod.load_event(cfg, event.event_id)


def test_forged_index_cannot_resurrect_deleted(cfg, db_conn) -> None:
    """Index is derived: forge must not resurrect tombstoned payloads on export."""
    cfg.ensure_dirs()
    sentinel = "R5_INDEX_FORGE_SENTINEL"
    event = _event(
        event_id="ev_r5_idx",
        behavior_policy_digest=sentinel,
        extra={"payload_sentinel": sentinel},
    )
    evidence_mod.checkpoint(cfg, db_conn, event)
    evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="r5_idx")

    root = evidence_mod.evidence_dir(cfg)
    (root / "event_index.json").write_text(
        json.dumps(
            {
                "events": {
                    event.event_id: {
                        "sequence": 1,
                        "segment_id": "00000001",
                        "payload_digest": "deadbeef",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    out = evidence_mod.export_evidence(cfg, db_conn, event_ids=[event.event_id])
    exported = (out / "events.jsonl").read_text(encoding="utf-8")
    assert sentinel not in exported
    for path in (root / "segments").glob("*.events.jsonl"):
        assert sentinel.encode() not in path.read_bytes()
