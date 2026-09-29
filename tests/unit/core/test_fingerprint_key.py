"""Local keyed HMAC fingerprint key provider (C6 / S00 provisional)."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from magicite.core import fingerprint_key as fk


@pytest.fixture(autouse=True)
def _clear_key_override():
    fk.set_fingerprint_key_override(None)
    yield
    fk.set_fingerprint_key_override(None)


def test_same_query_same_fingerprint_with_same_key(cfg) -> None:
    key = fk.load_or_create_fingerprint_key(cfg)
    a = fk.query_fingerprint("hello world", key=key)
    b = fk.query_fingerprint("hello world", key=key)
    assert a == b
    assert len(a) == 64


def test_different_key_different_fingerprint(cfg) -> None:
    key_a = bytes(range(fk.KEY_BYTES))
    key_b = bytes(range(fk.KEY_BYTES - 1, -1, -1))
    assert fk.query_fingerprint("hello", key=key_a) != fk.query_fingerprint("hello", key=key_b)


def test_key_file_created_with_0600_permissions(cfg) -> None:
    cfg.ensure_dirs()
    path = fk.fingerprint_key_path(cfg)
    assert not path.exists()
    key = fk.load_or_create_fingerprint_key(cfg)
    assert path.is_file()
    assert len(key) == fk.KEY_BYTES
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
    # Second call reuses the same key bytes.
    assert fk.load_or_create_fingerprint_key(cfg) == key


def test_override_bypasses_disk_and_is_deterministic(cfg) -> None:
    override = os.urandom(fk.KEY_BYTES)
    fk.set_fingerprint_key_override(override)
    assert fk.load_or_create_fingerprint_key(cfg) == override
    assert not fk.fingerprint_key_path(cfg).exists()
    assert fk.query_fingerprint("q", key=override) == fk.query_fingerprint("q", key=override)


def test_fingerprint_is_not_unsalted_sha256() -> None:
    key = os.urandom(fk.KEY_BYTES)
    query = "low entropy"
    fp = fk.query_fingerprint(query, key=key)
    assert fp != hashlib.sha256(query.encode("utf-8")).hexdigest()


def test_concurrent_first_create_yields_one_key(cfg) -> None:
    cfg.ensure_dirs()
    path = fk.fingerprint_key_path(cfg)
    assert not path.exists()

    def _load() -> bytes:
        return fk.load_or_create_fingerprint_key(cfg)

    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: _load(), range(16)))
    assert len(set(keys)) == 1
    assert path.read_bytes() == keys[0]
