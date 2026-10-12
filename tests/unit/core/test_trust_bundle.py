"""AC-S04-02 — signed bundle tamper and archive escape rejection."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles as bundles_mod
from magicite.core import trust as trust_mod
from magicite.errors import InvalidInputError

pytestmark = pytest.mark.acceptance


def _minimal_engram(name: str = "bundled-skill") -> str:
    return (
        "---\n"
        f"spec: engram/0.2\n"
        f"name: {name}\n"
        "id: egr_b0b0b0b0\n"
        "version: 1\n"
        "provenance: imported\n"
        "intent:\n"
        "  does: Ship inside a signed bundle\n"
        "  use_when: verifying offline signatures\n"
        "  not_when: accepting tampered resources\n"
        "triggers:\n"
        "  positive: [signed bundle]\n"
        "  negative: [tampered resource]\n"
        "---\n"
        "## Procedure\n"
        "1. Stay byte-identical to the signed manifest.\n"
        "## Pitfalls\n"
        "- Mutating a resource after signing\n"
        "## Examples\n"
        "+ clean bundle\n"
        "- tampered asset\n"
    )


def test_tamper_and_escape(cfg, tmp_path: Path) -> None:
    """AC-S04-02: altered resource or unsafe archive member is rejected."""
    src = tmp_path / "bundle-src"
    src.mkdir()
    (src / "bundled-skill.egr.md").write_text(_minimal_engram(), encoding="utf-8")
    (src / "asset.bin").write_bytes(b"clean-bytes")

    key = Ed25519PrivateKey.generate()
    trust_mod.pin_trust_root(cfg, public_key_bytes=key.public_key().public_bytes_raw())
    policy = trust_mod.load_policy(cfg)

    clean_zip = tmp_path / "clean.zip"
    manifest, _fp = bundles_mod.write_signed_bundle(
        source_dir=src, out_path=clean_zip, private_key=key
    )
    ok = bundles_mod.verify_bundle(clean_zip, roots=list(policy.active_roots()))
    assert ok.ok
    assert ok.manifest is not None
    assert ok.manifest.digest() == manifest.digest()

    # Tamper: alter a resource after signing while keeping the original signature.
    buf = io.BytesIO(clean_zip.read_bytes())
    with zipfile.ZipFile(buf, "r") as zin:
        members = {info.filename: zin.read(info) for info in zin.infolist()}
    members["asset.bin"] = b"TAMPERED"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for name, data in members.items():
            zout.writestr(name, data)
    with pytest.raises(InvalidInputError, match="digest mismatch|size mismatch"):
        bundles_mod.verify_bundle(out.getvalue(), roots=list(policy.active_roots()))

    # Zip-slip / absolute path escape.
    escape = io.BytesIO()
    with zipfile.ZipFile(escape, "w") as zf:
        zf.writestr("../escape.egr.md", _minimal_engram("escape"))
        zf.writestr("manifest.json", b"{}")
    with pytest.raises(InvalidInputError, match="escape|unsafe|absolute"):
        bundles_mod.extract_bundle_archive(escape.getvalue(), dest=tmp_path / "stage-escape")

    # Symlink member rejected.
    symlink_zip = io.BytesIO()
    with zipfile.ZipFile(symlink_zip, "w") as zf:
        info = zipfile.ZipInfo("link-out")
        info.create_system = 3  # Unix
        info.external_attr = 0o120777 << 16  # symlink
        zf.writestr(info, b"/tmp/evil")
    with pytest.raises(InvalidInputError, match="symlink"):
        bundles_mod.extract_bundle_archive(symlink_zip.getvalue(), dest=tmp_path / "stage-link")
