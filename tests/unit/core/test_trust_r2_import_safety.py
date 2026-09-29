"""Round-2 ATLAS regressions: non-clobbering import + casefold collisions."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles as bundles_mod
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.errors import InvalidInputError

pytestmark = pytest.mark.acceptance


def _minimal_egr(*, name: str, eid: str, body_extra: str = "") -> str:
    return (
        "---\n"
        "spec: engram/0.2\n"
        f"name: {name}\n"
        f"id: {eid}\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: Occupy a registry path\n"
        "  use_when: testing import_bundle collision safety\n"
        "  not_when: paths are clobbered\n"
        "triggers:\n"
        "  positive: [path collision]\n"
        "  negative: [silent overwrite]\n"
        "---\n"
        "## Procedure\n"
        "1. Keep existing bytes.\n"
        "## Pitfalls\n"
        "- Overwriting authored skills on import\n"
        "## Examples\n"
        "+ preserved path\n"
        "- clobbered path\n"
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


# ── MAJOR 1: refuse clobber of existing registry paths ────────────────────


def test_import_bundle_refuses_overwrite_existing(cfg, db_conn, embedder, tmp_path: Path) -> None:
    victim_rel = "skills/victim.egr.md"
    victim = cfg.registry_dir / victim_rel
    victim.parent.mkdir(parents=True, exist_ok=True)
    original = _minimal_egr(name="victim", eid="egr_v1c71f01").encode("utf-8")
    victim.write_bytes(original)

    attacker = _minimal_egr(
        name="attacker",
        eid="egr_a77ac001",
        body_extra="<!-- clobber -->\n",
    ).encode("utf-8")
    archive = _signed_bundle(tmp_path, cfg, {victim_rel: attacker})

    with pytest.raises(InvalidInputError, match="exists|clobber|collision|overwrite"):
        registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)

    assert victim.read_bytes() == original
    assert db_conn.execute("SELECT id FROM engram WHERE name='attacker'").fetchone() is None


def test_import_bundle_idempotent_same_engram_bytes(cfg, db_conn, embedder, tmp_path: Path) -> None:
    rel = "skills/reimport.egr.md"
    payload = _minimal_egr(name="reimport", eid="egr_ae100001").encode("utf-8")
    dest = cfg.registry_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    archive = _signed_bundle(tmp_path, cfg, {rel: payload})
    outcome = registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)
    assert dest.read_bytes() == payload
    assert outcome.validation_errors == []
    assert outcome.ingested == 1
    assert outcome.registered[0].id == "egr_ae100001"


# ── MAJOR 2: casefold collision ───────────────────────────────────────────


def test_casefold_index_detects_collision_pure() -> None:
    """Pure-logic: NFC/NFKC + casefold index refuses distinct spellings."""
    existing = {
        registry_mod.normalize_registry_path_key("Skills/CaseCollide.egr.md"): Path(
            "Skills/CaseCollide.egr.md"
        )
    }
    with pytest.raises(InvalidInputError, match="casefold|collision"):
        registry_mod.assert_no_registry_path_collision(
            existing,
            candidate="skills/casecollide.egr.md",
        )


def test_import_bundle_refuses_casefold_collision(cfg, db_conn, embedder, tmp_path: Path) -> None:
    planted = cfg.registry_dir / "CaseCollide.egr.md"
    planted.write_text(_minimal_egr(name="case-collide", eid="egr_ca5ec001"), encoding="utf-8")
    original = planted.read_bytes()

    incoming = _minimal_egr(
        name="casecollide",
        eid="egr_ca5ec002",
        body_extra="<!-- different -->\n",
    ).encode("utf-8")
    archive = _signed_bundle(tmp_path, cfg, {"casecollide.egr.md": incoming})

    with pytest.raises(InvalidInputError, match="casefold|collision|exists|clobber"):
        registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)

    # On case-insensitive FS the planted path is the same inode; bytes must remain.
    assert planted.read_bytes() == original
