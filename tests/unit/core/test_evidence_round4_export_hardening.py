"""Round-4: protected export roots, domain-separated MAC, pathsep, purge skip."""

from __future__ import annotations

import hashlib
import hmac
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
        event_id="ev_r4_001",
        decision_id="dec_r4_001",
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


def _force_seal_open(cfg) -> Path:
    root = evidence_mod.evidence_dir(cfg)
    segments = root / "segments"
    open_path = segments / "open.events.jsonl"
    data = open_path.read_bytes()
    sealed = segments / "00000001.events.jsonl"
    sealed.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (segments / "00000001.manifest.json").write_text(
        json.dumps(
            {
                "segment_id": "00000001",
                "kind": "EvidenceLedger/1",
                "sha256": digest,
                "sealed": True,
                "record_count": sum(1 for ln in data.splitlines() if ln.strip()),
            }
        ),
        encoding="utf-8",
    )
    open_path.write_bytes(b"")
    (segments / "open.manifest.json").write_text(
        json.dumps(
            {
                "segment_id": "00000002",
                "kind": "EvidenceLedger/1",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "sealed": False,
                "record_count": 0,
            }
        ),
        encoding="utf-8",
    )
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["open_segment_id"] = "00000002"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return sealed


# ── Finding 1: export roots must not cover ledger authority ────────────────


def test_data_dir_as_export_root_is_refused(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    object.__setattr__(cfg, "evidence_export_roots", (str(cfg.data_dir.resolve()),))
    event = _event(event_id="ev_r4_root_refuse")
    evidence_mod.checkpoint(cfg, db_conn, event)
    with pytest.raises(InvalidInputError, match="export root|protected|data.?dir|refus"):
        evidence_mod.export_evidence(
            cfg,
            db_conn,
            event_ids=[event.event_id],
            export_dir=cfg.data_dir / "sneaky_export",
        )


def test_poisoned_registry_targeting_sealed_segment_is_denied(
    cfg, db_conn, monkeypatch
) -> None:
    cfg.ensure_dirs()
    victim_event = _event(event_id="ev_r4_victim_seg", decision_id="dec_r4_victim")
    evidence_mod.checkpoint(cfg, db_conn, victim_event)
    sealed = _force_seal_open(cfg)
    assert sealed.is_file()
    sealed_bytes = sealed.read_bytes()
    st = sealed.stat()

    # Bypass config-time root denial so we exercise purge-time hard-deny.
    monkeypatch.setattr(
        evidence_mod,
        "_allowed_export_roots",
        lambda c, root: [cfg.data_dir.resolve(), (root / "exports").resolve()],
    )
    monkeypatch.setattr(
        evidence_mod,
        "_validate_configured_export_roots",
        lambda *a, **k: None,
    )

    root = evidence_mod.evidence_dir(cfg)
    body = {
        "kind": "EvidenceExportRegistry/1",
        "mac_scheme": "hmac-sha256/export-registry-v1",
        "exports": [
            {
                "path": str(sealed.resolve()),
                "sha256": hashlib.sha256(sealed_bytes).hexdigest(),
                "size": st.st_size,
                "st_dev": st.st_dev,
                "st_ino": st.st_ino,
                "exported_at": "2026-09-29T12:00:00+00:00",
            }
        ],
        "dirs": [],
        "updated_at": "2026-09-29T12:00:00+00:00",
    }
    body["mac"] = evidence_mod._registry_mac(cfg, body)  # noqa: SLF001
    (root / "registered_exports.json").write_text(json.dumps(body), encoding="utf-8")

    other = _event(
        event_id="ev_r4_other",
        decision_id="dec_r4_other",
        query_fingerprint="b" * 64,
    )
    evidence_mod.checkpoint(cfg, db_conn, other)
    result = evidence_mod.delete_event(cfg, db_conn, other.event_id, reason="r4_poison")

    assert sealed.is_file()
    assert sealed.read_bytes() == sealed_bytes
    skipped = result.get("export_purge_skipped") or []
    assert any(
        "protected" in str(s).lower() or "deny" in str(s).lower() or "segment" in str(s).lower()
        for s in skipped
    ), skipped


# ── Finding 2: domain-separated registry MAC ───────────────────────────────


def test_legacy_registry_mac_scheme_fails_closed_with_reregister_hint(cfg, db_conn) -> None:
    cfg.ensure_dirs()
    event = _event(event_id="ev_r4_legacy_mac")
    evidence_mod.checkpoint(cfg, db_conn, event)
    root = evidence_mod.evidence_dir(cfg)

    # Old Round-3 scheme: raw fingerprint key, no domain separation / version prefix.
    payload = {
        "kind": "EvidenceExportRegistry/1",
        "exports": [],
        "dirs": [],
        "updated_at": "2026-09-29T12:00:00+00:00",
    }
    key = fk.load_or_create_fingerprint_key(cfg)
    legacy_mac = hmac.new(
        key,
        evidence_mod._canonical_json(payload).encode("utf-8"),  # noqa: SLF001
        hashlib.sha256,
    ).hexdigest()
    payload["mac"] = legacy_mac
    (root / "registered_exports.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidInputError, match="re-?regist"):
        evidence_mod.delete_event(cfg, db_conn, event.event_id, reason="legacy_mac")


# ── Finding 3: pathsep-aware env split ─────────────────────────────────────


def test_evidence_export_roots_env_uses_pathsep_not_colon_or_comma(monkeypatch) -> None:
    from magicite import config as config_mod

    monkeypatch.setattr(config_mod.os, "pathsep", ";")
    roots = config_mod._coerce(  # noqa: SLF001
        "evidence_export_roots",
        r"C:\exports;D:\x",
    )
    assert roots == (r"C:\exports", r"D:\x")

    # Commas must not split when pathsep is ';'.
    roots_comma = config_mod._coerce(  # noqa: SLF001
        "evidence_export_roots",
        r"C:\exports,keepme",
    )
    assert roots_comma == (r"C:\exports,keepme",)

    # TOML list still accepted.
    roots_list = config_mod._coerce(  # noqa: SLF001
        "evidence_export_roots",
        [r"C:\exports", r"D:\x"],
    )
    assert roots_list == (r"C:\exports", r"D:\x")


# ── Finding 4: one-pass repurge + skip when complete ───────────────────────


def test_checkpoint_skips_physical_purge_when_marker_complete(cfg, db_conn, monkeypatch) -> None:
    cfg.ensure_dirs()
    for i in range(3):
        ev = _event(
            event_id=f"ev_r4_tomb_{i}",
            decision_id=f"dec_r4_tomb_{i}",
            query_fingerprint=f"{i:064d}",
        )
        evidence_mod.checkpoint(cfg, db_conn, ev)
        evidence_mod.delete_event(cfg, db_conn, ev.event_id, reason="r4_tomb")

    # Marker complete: pending scan may still run, but physical purge must not.
    calls = {"n": 0}
    real = evidence_mod._physically_purge_event_from_segments

    def counting(root: Path, event_id: str):
        calls["n"] += 1
        return real(root, event_id)

    monkeypatch.setattr(evidence_mod, "_physically_purge_event_from_segments", counting)

    evidence_mod.checkpoint(
        cfg,
        db_conn,
        _event(
            event_id="ev_r4_after_complete",
            decision_id="dec_r4_after",
            query_fingerprint="c" * 64,
        ),
    )
    assert calls["n"] == 0, "complete marker must not re-run physical purge when pending is empty"
