"""Round-3 ATLAS regressions: reserved staging paths + publish journal."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles as bundles_mod
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.engram.digests import sha256_hex
from magicite.errors import InvalidInputError

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
        "  does: Provide a lint-valid leftover staging subject\n"
        "  use_when: probing sync visibility of import staging\n"
        "  not_when: staging leftovers are ingested as authored\n"
        "triggers:\n"
        "  positive: [staging leftover, sync visibility, reserved path]\n"
        "  negative: [silent ingest from staging]\n"
        "---\n"
        "## Procedure\n"
        "1. Keep staging invisible to sync and register.\n"
        "## Pitfalls\n"
        "- Ingesting .import-staging leftovers as authored/verified\n"
        "## Examples\n"
        "+ reserved path skipped\n"
        "- staging leftover ingested\n"
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


# ── MAJOR 1: .import-staging must not be sync/register-visible ────────────


def test_leftover_import_staging_not_ingested_by_sync_or_register(
    cfg, db_conn, embedder
) -> None:
    leftover = (
        cfg.registry_dir
        / registry_mod._IMPORT_STAGING_DIRNAME
        / "crash-job"
        / "skills"
        / "leftover.egr.md"
    )
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_text(
        _lint_valid_egr(name="staging-leftover", eid="egr_57a91e01"),
        encoding="utf-8",
    )

    sync_out = registry_mod.sync(cfg, db_conn, embedder)
    assert db_conn.execute(
        "SELECT id FROM engram WHERE id = ?", ("egr_57a91e01",)
    ).fetchone() is None
    _ = sync_out

    reg_out = registry_mod.register(
        cfg,
        db_conn,
        embedder,
        path=str(cfg.registry_dir.relative_to(cfg.project_root)),
        fmt="egr",
    )
    assert all(e.id != "egr_57a91e01" for e in reg_out.registered)
    assert db_conn.execute(
        "SELECT id FROM engram WHERE id = ?", ("egr_57a91e01",)
    ).fetchone() is None

    # rebuild == sync after wipe projection; leftover must still stay out.
    rebuild = registry_mod.sync(cfg, db_conn, embedder)
    assert db_conn.execute(
        "SELECT id, origin, verification_status FROM engram WHERE id = ?",
        ("egr_57a91e01",),
    ).fetchone() is None
    assert rebuild.synced == db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]


def test_direct_register_of_import_staging_path_refused(cfg, db_conn, embedder) -> None:
    staging_file = (
        cfg.registry_dir
        / registry_mod._IMPORT_STAGING_DIRNAME
        / "job"
        / "direct.egr.md"
    )
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    staging_file.write_text(
        _lint_valid_egr(name="direct-staging", eid="egr_d1ec7001"),
        encoding="utf-8",
    )
    rel = str(staging_file.relative_to(cfg.project_root))
    with pytest.raises(InvalidInputError, match="reserved|staging|import-staging"):
        registry_mod.register(cfg, db_conn, embedder, path=rel, fmt="egr")
    assert db_conn.execute(
        "SELECT id FROM engram WHERE id = ?", ("egr_d1ec7001",)
    ).fetchone() is None


def test_is_reserved_registry_path_helper() -> None:
    root = Path("/tmp/registry")
    assert registry_mod._is_reserved_registry_path(
        root / ".import-staging" / "x.egr.md", registry_root=root
    )
    assert not registry_mod._is_reserved_registry_path(
        root / "skills" / "ok.egr.md", registry_root=root
    )


# ── MINOR 2: publish journal + compensating rollback ──────────────────────


def test_partial_publish_exception_removes_published_members(
    cfg, db_conn, embedder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = {
        "skills/a.egr.md": _lint_valid_egr(name="pub-a", eid="egr_a0000001").encode(),
        "skills/b.egr.md": _lint_valid_egr(name="pub-b", eid="egr_b0000002").encode(),
    }
    archive = _signed_bundle(tmp_path, cfg, members)

    real_replace = os.replace
    calls: list[str] = []

    def flaky_replace(src: Any, dst: Any) -> None:
        dst_s = str(dst)
        # Only fail final registry publishes — journal writes stay under staging.
        if registry_mod._IMPORT_STAGING_DIRNAME not in Path(dst_s).parts:
            calls.append(dst_s)
            if len(calls) >= 2:
                raise OSError("injected publish failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    with pytest.raises(OSError, match="injected publish failure"):
        registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)

    assert not (cfg.registry_dir / "skills" / "a.egr.md").exists()
    assert not (cfg.registry_dir / "skills" / "b.egr.md").exists()
    assert db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"] == 0


def test_incomplete_publish_journal_rolled_back_on_sync(
    cfg, db_conn, embedder
) -> None:
    """Simulate SIGKILL: incomplete journal + one published member left behind."""
    rel = "skills/orphaned.egr.md"
    payload = _lint_valid_egr(name="orphaned-pub", eid="egr_0faded01").encode()
    digest = sha256_hex(payload)
    dest = cfg.registry_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    job = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / "killed-job"
    job.mkdir(parents=True, exist_ok=True)
    journal = {
        "schema": "BundlePublishJournal/1",
        "bundle_id": "deadbeef",
        "complete": False,
        "members": [
            {
                "path": rel,
                "sha256": digest,
                "size": len(payload),
                "pre_existing": False,
            }
        ],
    }
    journal_path = job / registry_mod._PUBLISH_JOURNAL_NAME
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    registry_mod.sync(cfg, db_conn, embedder)

    assert not dest.exists()
    assert not job.exists()
    assert db_conn.execute(
        "SELECT id FROM engram WHERE id = ?", ("egr_0faded01",)
    ).fetchone() is None
