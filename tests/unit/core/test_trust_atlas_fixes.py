"""ATLAS REQUEST-CHANGES regressions for S04 trust ledger (majors + minors)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles as bundles_mod
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.engram.digests import asset_bytes_digest, assets_manifest_digest
from magicite.errors import InvalidInputError
from magicite.storage import lease as lease_mod

pytestmark = pytest.mark.acceptance


def _external_subject(cfg, name: str = "atlas-subject", eid: str = "egr_a7a7a7a7") -> Path:
    incoming = cfg.project_root / "external-intake"
    incoming.mkdir(exist_ok=True)
    path = incoming / f"{name}.egr.md"
    path.write_text(
        "---\n"
        "spec: engram/0.2\n"
        f"name: {name}\n"
        f"id: {eid}\n"
        "version: 1\n"
        "provenance: imported\n"
        "intent:\n"
        "  does: Provide an ATLAS review subject\n"
        "  use_when: probing digest-bound admission\n"
        "  not_when: using forged digests\n"
        "triggers:\n"
        "  positive: [atlas review]\n"
        "  negative: [forged digest]\n"
        "---\n"
        "## Procedure\n"
        "1. Bind live digests.\n"
        "## Pitfalls\n"
        "- Approving a caller-supplied digest without a live row check\n"
        "## Examples\n"
        "+ live digest\n"
        "- forged digest\n",
        encoding="utf-8",
    )
    return path


# ── MAJOR 1: live digest binding ──────────────────────────────────────────


def test_approve_requires_live_digest_match(cfg, db_conn, embedder) -> None:
    """Cleared mirrors + forged expected_digest must NOT flip verification_status."""
    _external_subject(cfg)
    outcome = registry_mod.register(cfg, db_conn, embedder, path="external-intake", fmt="egr")
    assert outcome.ingested == 1
    entry = outcome.registered[0]
    live = db_conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)).fetchone()[
        "content_sha256"
    ]

    # Clear durable mirrors so approve has no prior staged digest to compare.
    for path in trust_mod.trust_decisions_dir(cfg).glob("*.json"):
        path.unlink()

    forged = "0" * 64
    assert forged != live

    with pytest.raises(InvalidInputError):
        registry_mod.review_approve(
            cfg,
            db_conn,
            engram_id=entry.id,
            expected_digest=forged,
            actor="attacker",
            event_id="evt-forged-digest",
        )

    row = db_conn.execute("SELECT verification_status FROM engram WHERE id = ?", (entry.id,)).fetchone()
    assert row["verification_status"] != "verified"
    assert not trust_mod.admission_still_valid(cfg, engram_id=entry.id, content_digest=live)


# ── MAJOR 2: corrupt mirror fails closed ──────────────────────────────────


def test_corrupt_authority_fails_closed_while_mirror_is_only_projection(cfg, db_conn, embedder) -> None:
    _external_subject(cfg, name="corrupt-subj", eid="egr_c0c0c0c0")
    outcome = registry_mod.register(cfg, db_conn, embedder, path="external-intake", fmt="egr")
    entry = outcome.registered[0]
    live = db_conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)).fetchone()[
        "content_sha256"
    ]

    registry_mod.review_approve(
        cfg, db_conn, engram_id=entry.id, expected_digest=live, actor="reviewer", event_id="evt-ok"
    )

    corrupt = trust_mod.trust_decisions_dir(cfg) / "td_corrupt.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    # The unauthenticated projection cannot override a valid custody history.
    assert trust_mod.admission_still_valid(cfg, engram_id=entry.id, content_digest=live)
    authority = cfg.data_dir / "trust/authority/journal.jsonl"
    authority.write_bytes(authority.read_bytes() + b"{not-json\n")

    with pytest.raises((InvalidInputError, trust_mod.TrustLedgerCorruptError)):
        trust_mod.list_decisions(cfg)

    with pytest.raises((InvalidInputError, trust_mod.TrustLedgerCorruptError)):
        trust_mod.reload_from_mirror(cfg, db_conn)

    with pytest.raises((InvalidInputError, trust_mod.TrustLedgerCorruptError)):
        registry_mod.review_approve(
            cfg,
            db_conn,
            engram_id=entry.id,
            expected_digest=live,
            actor="reviewer",
            event_id="evt-after-corrupt",
        )

    view = registry_mod.trust_view_for(cfg, db_conn, engram_id=entry.id)
    assert view.admitted is False


# ── MINOR 5: no second lock; mutators lease-safe ──────────────────────────


def test_approve_under_and_without_outer_lease(cfg, db_conn, embedder) -> None:
    """Direct approve/reject acquire cross-process + writer lease; nested must not deadlock."""
    assert getattr(trust_mod, "_ledger_lock", None) is None

    _external_subject(cfg, name="lease-subj", eid="egr_1ea5e001")
    outcome = registry_mod.register(cfg, db_conn, embedder, path="external-intake", fmt="egr")
    entry = outcome.registered[0]
    live = db_conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)).fetchone()[
        "content_sha256"
    ]

    held: list[bool] = []
    _orig_live = trust_mod.live_content_digest

    def _probe_during_mutation(*args, **kwargs):
        held.append(lease_mod.cross_process_lease_held())
        return _orig_live(*args, **kwargs)

    trust_mod.live_content_digest = _probe_during_mutation  # type: ignore[method-assign]
    try:
        # Direct mutator must acquire the same cross-process lease as review_*.
        decision = trust_mod.approve(
            cfg,
            db_conn,
            engram_id=entry.id,
            expected_digest=live,
            actor="direct",
            event_id="evt-direct",
        )
        held_during_approve = list(held)
        held.clear()
        rejected = trust_mod.reject(
            cfg,
            db_conn,
            engram_id=entry.id,
            expected_digest=live,
            actor="direct-reject",
            event_id="evt-reject",
        )
        held_during_reject = list(held)
    finally:
        trust_mod.live_content_digest = _orig_live  # type: ignore[method-assign]

    assert decision.decision == "admit"
    assert held_during_approve and all(held_during_approve), (
        "cross-process lease must be held during approve mutation"
    )
    assert rejected.decision == "reject"
    assert held_during_reject and all(held_during_reject), (
        "cross-process lease must be held during reject mutation"
    )

    # A later rejection invalidates the original event: its replay must not
    # silently acknowledge local admission. Explicit re-review uses a new event.
    with pytest.raises(InvalidInputError, match="stale_decision"):
        registry_mod.review_approve(
            cfg, db_conn, engram_id=entry.id, expected_digest=live,
            actor="nested", event_id="evt-direct",
        )
    with registry_mod._cross_process_lease(cfg, db_conn, "outer-review").acquire(), lease_mod.writer_lease():
        again = registry_mod.review_approve(
            cfg, db_conn, engram_id=entry.id, expected_digest=live,
            actor="nested", event_id="evt-nested",
        )
        assert again.decision == "admit"
        assert again.decision_id != decision.decision_id
        assert lease_mod.cross_process_lease_held()


# ── MINOR 6: drive-letter / UNC archive paths ─────────────────────────────


def test_reject_drive_letter_and_unc_members(tmp_path: Path) -> None:
    for bad in ("C:/escape.egr.md", "//server/share/x.egr.md", "\\\\server\\share\\x.egr.md"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(bad, b"payload")
        with pytest.raises(InvalidInputError, match="drive|UNC|absolute|escape|unsafe|separator"):
            bundles_mod.extract_bundle_archive(buf.getvalue(), dest=tmp_path / "stage")


# ── MINOR 7: resource digest binding ──────────────────────────────────────


def test_asset_change_invalidates_admission(cfg, db_conn, embedder) -> None:
    """C10: content/resource/policy — asset byte change expires admission."""
    reg = cfg.registry_dir
    reg.mkdir(parents=True, exist_ok=True)
    asset_rel = "assets/tool.txt"
    asset_path = reg / asset_rel
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"asset-v1")
    sha = asset_bytes_digest(b"asset-v1")
    size = len(b"asset-v1")

    body = (
        "## Procedure\n"
        "1. Use the bundled asset.\n"
        "## Pitfalls\n"
        "- Ignoring asset digest binding\n"
        "## Examples\n"
        "+ stable assets\n"
        "- mutated assets\n"
    )
    from magicite.engram.digests import routing_body_digest

    body_digest = routing_body_digest(body)
    egr = reg / "asset-bound.egr.md"
    egr.write_text(
        "---\n"
        "spec: engram/1.0\n"
        "name: asset-bound\n"
        "id: egr_a55e7001\n"
        "version: 1\n"
        "intent:\n"
        "  does: Bind resource digests into local admission\n"
        "  use_when: assets accompany an imported skill\n"
        "  not_when: assets can mutate after approval\n"
        "routing:\n"
        "  positive: [asset bound]\n"
        "  negative: [mutable asset]\n"
        f'  body_digest: "{body_digest}"\n'
        "origin:\n"
        "  channel: imported\n"
        "  verification_status: pending\n"
        "assets:\n"
        f"  {asset_rel}:\n"
        f'    sha256: "{sha}"\n'
        f"    size: {size}\n"
        "    media_type: text/plain\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )

    resource = assets_manifest_digest({asset_rel: {"sha256": sha, "size": size, "media_type": "text/plain"}})
    outcome = registry_mod.register(cfg, db_conn, embedder, path=str(egr))
    assert outcome.ingested == 1
    engram_id = outcome.registered[0].id
    content_digest = db_conn.execute(
        "SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["content_sha256"]

    decision = registry_mod.review_approve(
        cfg,
        db_conn,
        engram_id=engram_id,
        expected_digest=content_digest,
        actor="reviewer",
        event_id="evt-asset-bind",
    )
    assert decision.resource_digest == resource
    assert trust_mod.admission_still_valid(
        cfg, engram_id=engram_id, content_digest=content_digest, resource_digest=resource
    )

    asset_path.write_bytes(b"asset-v2-MUTATED")
    live_resource = assets_manifest_digest(
        {
            asset_rel: {
                "sha256": asset_bytes_digest(b"asset-v2-MUTATED"),
                "size": len(b"asset-v2-MUTATED"),
                "media_type": "text/plain",
            }
        }
    )
    assert live_resource != resource
    assert not trust_mod.admission_still_valid(
        cfg,
        engram_id=engram_id,
        content_digest=content_digest,
        resource_digest=live_resource,
    )


# ── MINOR 9: bundle hierarchy / assets preserved ──────────────────────────


def test_import_bundle_preserves_asset_hierarchy(cfg, db_conn, embedder, tmp_path: Path) -> None:
    src = tmp_path / "bundle-src"
    (src / "skills").mkdir(parents=True)
    (src / "skills" / "assets").mkdir(parents=True)
    asset_bytes = b"helper-bytes"
    (src / "skills" / "assets" / "helper.bin").write_bytes(asset_bytes)

    body = (
        "## Procedure\n"
        "1. Read helper.bin.\n"
        "## Pitfalls\n"
        "- Flattening drops assets\n"
        "## Examples\n"
        "+ nested assets\n"
        "- basename-only staging\n"
    )
    from magicite.engram.digests import routing_body_digest

    body_digest = routing_body_digest(body)
    sha = asset_bytes_digest(asset_bytes)
    (src / "skills" / "nested.egr.md").write_text(
        "---\n"
        "spec: engram/1.0\n"
        "name: nested-bundle\n"
        "id: egr_b011d1e5\n"
        "version: 1\n"
        "intent:\n"
        "  does: Carry nested assets in a signed bundle\n"
        "  use_when: importing a multi-file skill package\n"
        "  not_when: assets are silently dropped\n"
        "routing:\n"
        "  positive: [nested bundle]\n"
        "  negative: [flattened import]\n"
        f'  body_digest: "{body_digest}"\n'
        "origin:\n"
        "  channel: imported\n"
        "  verification_status: pending\n"
        "assets:\n"
        "  skills/assets/helper.bin:\n"
        f'    sha256: "{sha}"\n'
        f"    size: {len(asset_bytes)}\n"
        "    media_type: application/octet-stream\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )

    key = Ed25519PrivateKey.generate()
    trust_mod.pin_trust_root(cfg, public_key_bytes=key.public_key().public_bytes_raw())
    archive = tmp_path / "nested.zip"
    bundles_mod.write_signed_bundle(source_dir=src, out_path=archive, private_key=key)

    outcome = registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)
    assert outcome.ingested == 1
    assert outcome.validation_errors == []

    staged_egr = cfg.registry_dir / "skills" / "nested.egr.md"
    staged_asset = cfg.registry_dir / "skills" / "assets" / "helper.bin"
    assert staged_egr.is_file()
    assert staged_asset.is_file()
    assert staged_asset.read_bytes() == asset_bytes
