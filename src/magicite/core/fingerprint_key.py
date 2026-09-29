"""Local keyed HMAC fingerprint material (C6; provisional S00 → S09 handoff).

Context/query fingerprints MUST use a local keyed HMAC so low-entropy prompts
are not rainbow-tableable from persisted event payloads (contracts.md C6).

S00 ships a minimal key provider so route events can satisfy AC-S00-04 without
waiting on S09. S09 owns the durable evidence ledger, retention/deletion/export,
and HMAC key lifecycle (slice s09 action 3). When S09 lands it SHOULD:

- Adopt this module (or move it under ``core/evidence.py`` / privacy) as the
  single key authority for query and context fingerprints.
- Keep the on-disk path and ``hmac-sha256/local-v1`` scheme stable, or publish
  an explicit rotation/migration path.
- Never export or log the raw key bytes; export uses fresh scoped pseudonyms.

This module never logs key material.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from pathlib import Path

from magicite.config import Config

#: On-disk / wire scheme id for query fingerprints produced here.
FINGERPRINT_SCHEME = "hmac-sha256/local-v1"

#: Absolute key length for ``hmac-sha256/local-v1``.
KEY_BYTES = 32

#: Relative path under ``Config.data_dir`` (S09 may relocate with migration).
_KEY_RELATIVE_PATH = Path("runtime") / "fingerprint.key"

_override_lock = threading.Lock()
_key_override: bytes | None = None


def fingerprint_key_path(cfg: Config) -> Path:
    """Path of the local fingerprint key file (never logged with contents)."""
    return cfg.data_dir / _KEY_RELATIVE_PATH


def set_fingerprint_key_override(key: bytes | None) -> None:
    """In-memory/test override. ``None`` clears and resumes file-backed keys.

    Override bytes are never written to disk. Callers MUST pass exactly
    ``KEY_BYTES`` when setting a non-None key.
    """
    global _key_override
    if key is not None and len(key) != KEY_BYTES:
        raise ValueError(f"fingerprint key override must be {KEY_BYTES} bytes")
    with _override_lock:
        _key_override = key


def load_or_create_fingerprint_key(cfg: Config) -> bytes:
    """Return the local HMAC key, creating it atomically on first use.

    Creation uses ``O_CREAT|O_EXCL`` so concurrent processes cannot race to
    write different keys. Permissions are ``0o600``. Existing files are
    re-chmod'd to ``0o600`` if the filesystem permits.
    """
    with _override_lock:
        if _key_override is not None:
            return _key_override

    path = fingerprint_key_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_bytes()
        if len(key) != KEY_BYTES:
            raise ValueError(
                f"fingerprint key at {path.name} has length {len(key)}, expected {KEY_BYTES}"
            ) from None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key

    try:
        key = secrets.token_bytes(KEY_BYTES)
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def query_fingerprint(query: str, *, key: bytes) -> str:
    """HMAC-SHA256 hex digest of ``query`` under the local key (C6)."""
    if len(key) != KEY_BYTES:
        raise ValueError(f"fingerprint key must be {KEY_BYTES} bytes")
    return hmac.new(key, query.encode("utf-8"), hashlib.sha256).hexdigest()
