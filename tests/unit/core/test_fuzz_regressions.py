"""Regressions for defects found by the bounded fuzz harness (bounded-fuzz-adversarial)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles
from magicite.core.trust import default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.errors import InvalidInputError

BAD_ROOTS = [None, [[]], [], 1, "x", {}, {"revoked": False}, {"revoked": False, "public_key_hex": 5}]


@pytest.mark.parametrize("root", BAD_ROOTS)
def test_policy_non_dict_or_malformed_root_raises_custodian_error(root):
    """bounded-fuzz-adversarial: type-confused policy roots raise CustodianError, never AttributeError."""
    payload = default_policy().to_dict()
    payload["roots"] = [root]
    with pytest.raises(CustodianError):
        CustodianStore._validate_payload("policy_snapshot", payload)


def test_policy_valid_still_accepted():
    """Positive control: the default policy still validates."""
    CustodianStore._validate_payload("policy_snapshot", default_policy().to_dict())


def _zip(tmp: Path) -> bytes:
    src = tmp / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha\n")
    manifest = bundles.build_manifest_from_directory(src)
    mbytes = manifest.canonical_bytes()
    key = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "alpha\n")
        zf.writestr(bundles.MANIFEST_NAME, mbytes)
        zf.writestr(bundles.SIGNATURE_NAME, key.sign(mbytes))
    return buf.getvalue()


def _root():
    pub = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32).public_key().public_bytes_raw()
    return bundles.TrustRoot(bundles.public_key_fingerprint(pub), pub)


def _corruptions(good: bytes):
    crc = bytearray(good)
    crc[good.index(b"alpha") if b"alpha" in good else 40] ^= 0xFF  # stored/deflated payload damage
    magic = bytearray(good)
    magic[0:4] = b"K\x03\x04\x14"
    cd = good.index(b"PK\x01\x02")
    badoff = bytearray(good)
    badoff[cd + 42 : cd + 46] = (0xFFFFFF).to_bytes(4, "little")  # local header offset out of range
    method = bytearray(good)
    method[8:10] = (99).to_bytes(2, "little")  # local header method
    method[cd + 10 : cd + 12] = (99).to_bytes(2, "little")  # unsupported compression
    seek = bytearray(good)
    seek[cd + 42 : cd + 46] = (0).to_bytes(4, "little")
    seek[-6:-2] = (0xFFFFFFF0).to_bytes(4, "little")  # central-directory offset corrupted
    return {"crc": bytes(crc), "magic": bytes(magic), "badoff": bytes(badoff), "method": bytes(method),
            "cdoffset": bytes(seek), "truncated": good[: len(good) // 2]}


def test_valid_bundle_still_verifies(tmp_path):
    """Positive control: the unmodified bundle verifies and keeps its staging dir."""
    result = bundles.verify_bundle(_zip(tmp_path), roots=[_root()], staging_parent=tmp_path / "st")
    assert result.ok and result.staging_dir is not None and result.staging_dir.exists()


@pytest.mark.parametrize("name", ["crc", "magic", "badoff", "method", "cdoffset", "truncated"])
def test_corrupt_archive_raises_invalid_input_and_leaves_no_staging(tmp_path, name):
    """bounded-fuzz-adversarial: archive corruption is InvalidInputError; staging is cleaned."""
    bad = _corruptions(_zip(tmp_path))[name]
    parent = tmp_path / "st"
    with pytest.raises(InvalidInputError) as info:
        bundles.verify_bundle(bad, roots=[_root()], staging_parent=parent)
    assert not list(parent.iterdir())
    assert str(info.value) in {
        "bundle is not a valid zip archive",
        "bundle archive is corrupt or uses an unsupported zip feature",
    }


def test_manifest_json_error_does_not_leak_staging(tmp_path):
    """Failure after extraction (bad manifest) also removes the staged tree."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(bundles.MANIFEST_NAME, json.dumps({"kind": "nope"}))
    parent = tmp_path / "st"
    with pytest.raises(InvalidInputError):
        bundles.verify_bundle(buf.getvalue(), roots=[_root()], staging_parent=parent)
    assert not list(parent.iterdir())
