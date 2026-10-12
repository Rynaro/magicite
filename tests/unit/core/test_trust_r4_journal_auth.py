"""Round-4 ATLAS regressions: skip_publish indent + authenticated journal recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles as bundles_mod
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.engram.digests import asset_bytes_digest, routing_body_digest, sha256_hex

pytestmark = pytest.mark.acceptance


def _lint_valid_egr(*, name: str, eid: str, body_extra: str = "") -> str:
    return (
        "---\n"
        "spec: engram/0.2\n"
        f"name: {name}\n"
        f"id: {eid}\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: Occupy a registry path for Round-4 probes\n"
        "  use_when: testing idempotent multi-file import and journal auth\n"
        "  not_when: skip_publish is corrupted by loop indent bugs\n"
        "triggers:\n"
        "  positive: [idempotent reimport, journal mac, quarantine]\n"
        "  negative: [forced rewrite, attacker journal deletes]\n"
        "---\n"
        "## Procedure\n"
        "1. Preserve existing bytes across re-import.\n"
        "## Pitfalls\n"
        "- Dedented skip_publish forcing the last member False\n"
        "## Examples\n"
        "+ multi-file skip\n"
        "- last-member rewrite\n"
        f"{body_extra}"
    )


def _signed_bundle(tmp_path: Path, cfg, members: dict[str, bytes]) -> Path:
    src = tmp_path / "bundle-src"
    for rel, data in members.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    key = Ed25519PrivateKey.generate()
    trust_mod.pin_trust_root(cfg, public_key_bytes=key.public_key().public_bytes_raw())
    archive = tmp_path / "bundle.zip"
    bundles_mod.write_signed_bundle(source_dir=src, out_path=archive, private_key=key)
    return archive


def _asset_bound_egr(*, name: str, eid: str, asset_rel: str, asset_bytes: bytes) -> bytes:
    body = (
        "## Procedure\n"
        "1. Read the bundled asset.\n"
        "## Pitfalls\n"
        "- Dropping assets on re-import\n"
        "## Examples\n"
        "+ asset preserved\n"
        "- asset rewritten\n"
    )
    body_digest = routing_body_digest(body)
    sha = asset_bytes_digest(asset_bytes)
    return (
        "---\n"
        "spec: engram/1.0\n"
        f"name: {name}\n"
        f"id: {eid}\n"
        "version: 1\n"
        "intent:\n"
        "  does: Carry an asset through idempotent re-import\n"
        "  use_when: multi-file skip_publish must stay True\n"
        "  not_when: assets are rewritten on re-import\n"
        "routing:\n"
        "  positive: [idempotent asset, multi file, skip publish]\n"
        "  negative: [forced rewrite]\n"
        f'  body_digest: "{body_digest}"\n'
        "origin:\n"
        "  channel: imported\n"
        "  verification_status: pending\n"
        "assets:\n"
        f"  {asset_rel}:\n"
        f'    sha256: "{sha}"\n'
        f"    size: {len(asset_bytes)}\n"
        "    media_type: application/octet-stream\n"
        "---\n"
        f"{body}"
    ).encode()


# ── BLOCKER 1: multi-file skip_publish indent ──────────────────────────────


def test_assert_import_destinations_all_skip_on_idempotent_reimport(
    cfg, db_conn, embedder, tmp_path: Path
) -> None:
    asset_rel = "skills/assets/helper.bin"
    asset_bytes = b"helper-v1"
    members = {
        "skills/a.egr.md": _lint_valid_egr(name="idem-a", eid="egr_a1d00001").encode(),
        "skills/b.egr.md": _asset_bound_egr(
            name="idem-b", eid="egr_b1d00002", asset_rel=asset_rel, asset_bytes=asset_bytes
        ),
        asset_rel: asset_bytes,
    }
    archive = _signed_bundle(tmp_path, cfg, members)
    first = registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)
    assert first.ingested == 2

    paths = {rel: cfg.registry_dir / rel for rel in members}
    target_bytes = {rel: p.read_bytes() for rel, p in paths.items()}
    meta = {rel: (p.stat().st_mtime_ns, getattr(p.stat(), "st_ino", None)) for rel, p in paths.items()}

    # Direct pre-check must mark every destination skip_publish=True.
    verified = bundles_mod.verify_bundle(
        archive,
        roots=list(trust_mod.load_policy(cfg).active_roots()),
        staging_parent=cfg.runtime_dir / "bundle-staging-r4",
    )
    assert verified.staging_dir is not None and verified.manifest is not None
    skip, _pre = registry_mod._assert_import_destinations_safe(
        conn=db_conn,
        cfg=cfg,
        registry_root=cfg.registry_dir.resolve(),
        verified_staging=verified.staging_dir,
        manifest_entries=list(verified.manifest.entries),
        transformed_bytes=target_bytes,
    )
    assert skip == {rel: True for rel in members}

    second = registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)
    assert second.validation_errors == []
    for rel, p in paths.items():
        st = p.stat()
        assert (st.st_mtime_ns, getattr(st, "st_ino", None)) == meta[rel], rel
        assert p.read_bytes() == target_bytes[rel]


# ── MAJOR 2: authenticated journal recovery ────────────────────────────────


def test_planted_unauthenticated_journal_does_not_delete_authored(cfg, db_conn, embedder) -> None:
    victim_rel = "skills/victim.egr.md"
    victim = cfg.registry_dir / victim_rel
    victim.parent.mkdir(parents=True, exist_ok=True)
    original = _lint_valid_egr(name="victim", eid="egr_a1c71f01").encode()
    victim.write_bytes(original)

    job = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / "attacker-job"
    job.mkdir(parents=True, exist_ok=True)
    forged = {
        "schema": "BundlePublishJournal/1",
        "bundle_id": "attacker",
        "complete": False,
        "members": [
            {
                "path": victim_rel,
                "sha256": sha256_hex(original),
                "size": len(original),
                "pre_existing": False,
            }
        ],
        "pre_existing_paths": [],
        "aborted_engram_ids": [],
        # no mac / wrong mac
        "mac": "0" * 64,
    }
    (job / registry_mod._PUBLISH_JOURNAL_NAME).write_text(json.dumps(forged), encoding="utf-8")

    registry_mod.sync(cfg, db_conn, embedder)

    assert victim.read_bytes() == original
    assert not job.exists()
    quarantine_root = cfg.data_dir / "quarantine" / "import-rollback"
    assert quarantine_root.is_dir()
    quarantined = list(quarantine_root.rglob("publish-journal.json"))
    assert quarantined, "unauthenticated job must be quarantined for review"


def test_authenticated_journal_skips_path_owned_by_other_engram(cfg, db_conn, embedder) -> None:
    victim_rel = "skills/owned.egr.md"
    victim = cfg.registry_dir / victim_rel
    victim.parent.mkdir(parents=True, exist_ok=True)
    original = _lint_valid_egr(name="owned", eid="egr_a0ced001").encode()
    victim.write_bytes(original)
    # Seed durable ownership via register of registry contents.
    registry_mod.register(
        cfg,
        db_conn,
        embedder,
        path=str(cfg.registry_dir.relative_to(cfg.project_root)),
        fmt="egr",
    )
    assert db_conn.execute("SELECT id FROM engram WHERE id = ?", ("egr_a0ced001",)).fetchone()

    published = victim.read_bytes()
    assert published != original
    job = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / "owned-job"
    job.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "BundlePublishJournal/1",
        "bundle_id": "owned-probe",
        "complete": False,
        "members": [
            {
                "path": victim_rel,
                "sha256": sha256_hex(published),
                "size": len(published),
                "pre_existing": False,
            }
        ],
        "pre_existing_paths": [],
        "aborted_engram_ids": ["egr_ab007ed1"],  # different from owner
    }
    signed = registry_mod._sign_publish_journal(cfg, payload)
    (job / registry_mod._PUBLISH_JOURNAL_NAME).write_text(
        json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    registry_mod.sync(cfg, db_conn, embedder)

    assert victim.read_bytes() == published
    assert victim.exists()


def test_authenticated_incomplete_journal_quarantines_published_members(cfg, db_conn, embedder) -> None:
    rel = "skills/crash-only.egr.md"
    payload = _lint_valid_egr(name="crash-only", eid="egr_cfa50001").encode()
    digest = sha256_hex(payload)
    dest = cfg.registry_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    job_id = "genuine-crash"
    job = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / job_id
    job.mkdir(parents=True, exist_ok=True)
    journal = registry_mod._sign_publish_journal(
        cfg,
        {
            "schema": "BundlePublishJournal/1",
            "bundle_id": "genuine",
            "complete": False,
            "members": [
                {
                    "path": rel,
                    "sha256": digest,
                    "size": len(payload),
                    "pre_existing": False,
                }
            ],
            "pre_existing_paths": [],
            "aborted_engram_ids": ["egr_cfa50001"],
        },
    )
    (job / registry_mod._PUBLISH_JOURNAL_NAME).write_text(
        json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    registry_mod.sync(cfg, db_conn, embedder)

    assert not dest.exists()
    assert not job.exists()
    q = cfg.data_dir / "quarantine" / "import-rollback" / job_id / rel
    assert q.is_file()
    assert q.read_bytes() == payload
    assert db_conn.execute("SELECT id FROM engram WHERE id = ?", ("egr_cfa50001",)).fetchone() is None
