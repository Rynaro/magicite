"""S12 backup / restore domain APIs (C0, C6, C8).

Consistent snapshot and restore of authoritative *file-domain* stores under
the existing shared writer lease (no second lock system). The SQLite retrieval
DB is a rebuildable projection: restore file domains, then rebuild.

RecoveryOverlay / sequence-anchor reconciliation (C8):
- In-place restore preserves the live overlay + anchor outside replaced trees,
  verifies them, then merges before activation.
- Clean-machine / disaster restore requires a separately supplied authenticated
  ``RecoveryOverlay/1`` and operator-held sequence anchor. Missing or stale
  inputs leave the instance offline with ``reconciliation_required``.
- Overlay/anchor authenticity is HMAC-bound (domain-separated subkey of the
  local fingerprint key, or an operator-supplied custody key). Forgeable
  on-disk flags alone never lift ``reconciliation_required``.

Secrets (``runtime/fingerprint.key`` and other key material) are excluded
unless the operator supplies an encrypted-custody path. Without the HMAC key
on a clean machine, tombstone MAC verification and overlay authentication
fail closed — never silently re-key and orphan tombstone MACs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import stat
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import approvals as approvals_mod
from magicite.core import evidence as evidence_mod
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.errors import InvalidInputError
from magicite.storage import lease as lease_mod
from magicite.storage.migrations.registry import (
    MAX_KNOWN_SCHEMA_VERSION,
    SUPPORTED_BACKUP_MANIFEST_KINDS,
)

BACKUP_MANIFEST_KIND = "backup/1"
RECOVERY_OVERLAY_KIND = "RecoveryOverlay/1"
SEQUENCE_ANCHOR_KIND = "SequenceAnchor/1"
RECOVERY_STATE_KIND = "recovery_state/1"
ACTIVATION_SEAL_KIND = "recovery_activation/1"

_OVERLAY_MAC_LABEL = b"magicite/recovery-overlay/v1"
_ANCHOR_MAC_LABEL = b"magicite/sequence-anchor/v1"
_SEAL_MAC_LABEL = b"magicite/recovery-activation/v1"

#: Explicit registered backup domains. ``policy_store`` is included when the
#: directory exists (opaque file-level copy); full semantic verification is a
#: forward for after S07 merges — do not import S07 code here.
BACKUP_DOMAINS: tuple[str, ...] = (
    "registry",
    "evidence",
    "trust",
    "approvals",
    "config",
    "policy_store",
    "archive",
)

_SECRET_RELATIVE_PATHS: frozenset[str] = frozenset(
    {
        "runtime/fingerprint.key",
    }
)

_RECOVERY_DIRNAME = "recovery"
_STATE_FILENAME = "state.json"
_ACTIVATION_FILENAME = "activation.json"
_LIVE_OVERLAY_FILENAME = "live_overlay.json"
_LIVE_ANCHOR_FILENAME = "live_anchor.json"
_JOURNAL_FILENAME = "journal.jsonl"
_STAGING_DIRNAME = "staging"

FaultHook = Callable[[str], None] | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def recovery_dir(cfg: Config) -> Path:
    return cfg.data_dir / _RECOVERY_DIRNAME


def recovery_state_path(cfg: Config) -> Path:
    return recovery_dir(cfg) / _STATE_FILENAME


def recovery_activation_path(cfg: Config) -> Path:
    return recovery_dir(cfg) / _ACTIVATION_FILENAME


def live_overlay_path(cfg: Config) -> Path:
    return recovery_dir(cfg) / _LIVE_OVERLAY_FILENAME


def live_anchor_path(cfg: Config) -> Path:
    return recovery_dir(cfg) / _LIVE_ANCHOR_FILENAME


def recovery_journal_path(cfg: Config) -> Path:
    return recovery_dir(cfg) / _JOURNAL_FILENAME


def recovery_staging_dir(cfg: Config) -> Path:
    return recovery_dir(cfg) / _STAGING_DIRNAME


def policy_store_dir(cfg: Config) -> Path:
    """Forward-compatible path for S07 policy store (opaque until S07 merges)."""
    return cfg.data_dir / "policy_store"


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_secure(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_bytes_durable(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    _mkdir_secure(path.parent)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, mode)
    _fsync_dir(path.parent)


def _write_json_durable(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    _write_bytes_durable(path, (_canonical_json(payload) + "\n").encode("utf-8"), mode=mode)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _constrained_under(root: Path, rel: str, *, label: str) -> Path:
    if not isinstance(rel, str) or not rel:
        raise InvalidInputError(f"{label} path must be a non-empty relative string")
    candidate = Path(rel)
    if candidate.is_absolute():
        raise InvalidInputError(
            f"{label} path must be relative, got absolute {rel!r}",
            details={"path": rel},
        )
    if any(part == ".." for part in candidate.parts):
        raise InvalidInputError(
            f"{label} path escapes root via '..': {rel!r}",
            details={"path": rel},
        )
    root_resolved = root.resolve()
    cursor = root_resolved
    for part in candidate.parts:
        cursor = cursor / part
        if _is_symlink(cursor):
            raise InvalidInputError(
                f"{label} path contains a symlink: {rel!r}",
                details={"path": rel, "symlink": str(cursor)},
            )
    resolved = cursor.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise InvalidInputError(
            f"{label} path escapes backup/data root: {rel!r}",
            details={"path": rel, "resolved": str(resolved), "root": str(root_resolved)},
        ) from exc
    return resolved


def _copy_file_durable(src: Path, dest: Path, *, mode: int = 0o600) -> dict[str, Any]:
    if _is_symlink(src):
        raise InvalidInputError(f"refusing to copy symlink: {src}")
    _mkdir_secure(dest.parent)
    source_sha = _sha256_file(src)
    source_size = src.stat().st_size
    tmp = dest.with_suffix(dest.suffix + f".{os.getpid()}.tmp")
    with open(src, "rb") as rf, open(tmp, "wb") as wf:
        shutil.copyfileobj(rf, wf, length=1024 * 1024)
        wf.flush()
        os.fsync(wf.fileno())
    os.replace(tmp, dest)
    os.chmod(dest, mode)
    _fsync_dir(dest.parent)
    dest_sha = _sha256_file(dest)
    dest_size = dest.stat().st_size
    if dest_sha != source_sha or dest_size != source_size:
        raise InvalidInputError(
            f"backup copy integrity failure for {dest.name}",
            details={
                "source_sha256": source_sha,
                "dest_sha256": dest_sha,
                "source_size": source_size,
                "dest_size": dest_size,
            },
        )
    return {"sha256": dest_sha, "size": dest_size}


def _rel_under(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_secret_rel(rel: str) -> bool:
    if rel in _SECRET_RELATIVE_PATHS:
        return True
    name = Path(rel).name
    if name.endswith(".key") and "runtime/" in rel:
        return True
    return False


def ensure_registry_id(cfg: Config) -> str:
    """Stable local registry identity (non-secret). Created under lease only."""
    path = cfg.runtime_dir / "registry.id"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = f"reg_{uuid.uuid4().hex}"
    _mkdir_secure(path.parent)
    _write_bytes_durable(path, (value + "\n").encode("utf-8"))
    return value


def load_registry_id(cfg: Config) -> str | None:
    path = cfg.runtime_dir / "registry.id"
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _derive_mac_key(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, label, hashlib.sha256).digest()


def _resolve_auth_key(
    cfg: Config,
    *,
    custody_key: bytes | None = None,
    allow_create: bool = False,
) -> bytes:
    if custody_key is not None:
        if len(custody_key) != fingerprint_key_mod.KEY_BYTES:
            raise InvalidInputError(
                f"custody key must be {fingerprint_key_mod.KEY_BYTES} bytes",
            )
        return custody_key
    path = fingerprint_key_mod.fingerprint_key_path(cfg)
    if path.is_file():
        return fingerprint_key_mod._read_complete_key(path)  # noqa: SLF001
    if allow_create:
        return fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    raise InvalidInputError(
        "fingerprint key absent; cannot authenticate RecoveryOverlay/anchor "
        "without operator-supplied custody key",
        hint=(
            "supply custody_key= for clean-machine restore, or restore the "
            "encrypted-custody fingerprint.key separately — never silently re-key"
        ),
        details={"reconciliation_required": True},
    )


def _mac_hex(key: bytes, label: bytes, payload: bytes) -> str:
    sub = _derive_mac_key(key, label)
    return hmac.new(sub, payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class SequenceAnchor:
    kind: str
    registry_id: str
    control_sequence: int
    overlay_digest: str
    issued_at: str
    mac: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "registry_id": self.registry_id,
            "control_sequence": self.control_sequence,
            "overlay_digest": self.overlay_digest,
            "issued_at": self.issued_at,
            "mac": self.mac,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SequenceAnchor:
        return cls(
            kind=str(data.get("kind", SEQUENCE_ANCHOR_KIND)),
            registry_id=str(data["registry_id"]),
            control_sequence=int(data["control_sequence"]),
            overlay_digest=str(data["overlay_digest"]),
            issued_at=str(data["issued_at"]),
            mac=str(data["mac"]),
        )


@dataclass(frozen=True, slots=True)
class RecoveryOverlay:
    """Authenticated current revocation/deletion/policy overlay (C8)."""

    kind: str
    registry_id: str
    control_sequence: int
    deletion_records: tuple[dict[str, Any], ...]
    revocation_records: tuple[dict[str, Any], ...]
    policy_digest: str
    content_hashes: tuple[str, ...]
    operator_provenance: str
    mac: str

    def body_for_mac(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "registry_id": self.registry_id,
            "control_sequence": self.control_sequence,
            "deletion_records": list(self.deletion_records),
            "revocation_records": list(self.revocation_records),
            "policy_digest": self.policy_digest,
            "content_hashes": list(self.content_hashes),
            "operator_provenance": self.operator_provenance,
        }

    def to_dict(self) -> dict[str, Any]:
        body = self.body_for_mac()
        body["mac"] = self.mac
        return body

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryOverlay:
        return cls(
            kind=str(data.get("kind", RECOVERY_OVERLAY_KIND)),
            registry_id=str(data["registry_id"]),
            control_sequence=int(data["control_sequence"]),
            deletion_records=tuple(data.get("deletion_records") or ()),
            revocation_records=tuple(data.get("revocation_records") or ()),
            policy_digest=str(data["policy_digest"]),
            content_hashes=tuple(data.get("content_hashes") or ()),
            operator_provenance=str(data["operator_provenance"]),
            mac=str(data.get("mac") or ""),
        )

    def content_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.body_for_mac()).encode("utf-8"))


def sign_overlay(overlay_body: dict[str, Any], *, key: bytes) -> str:
    payload = _canonical_json({k: v for k, v in overlay_body.items() if k != "mac"}).encode()
    return _mac_hex(key, _OVERLAY_MAC_LABEL, payload)


def verify_overlay(overlay: RecoveryOverlay, *, key: bytes) -> None:
    if overlay.kind != RECOVERY_OVERLAY_KIND:
        raise InvalidInputError(f"unsupported overlay kind {overlay.kind!r}")
    expected = sign_overlay(overlay.body_for_mac(), key=key)
    if not overlay.mac or not hmac.compare_digest(expected, overlay.mac):
        raise InvalidInputError(
            "RecoveryOverlay MAC verification failed",
            details={"reconciliation_required": True},
        )
    recomputed = tuple(
        sorted(
            _sha256_bytes(_canonical_json(r).encode("utf-8"))
            for r in (*overlay.deletion_records, *overlay.revocation_records)
        )
    )
    if recomputed != tuple(overlay.content_hashes):
        raise InvalidInputError(
            "RecoveryOverlay content_hashes mismatch",
            details={"reconciliation_required": True},
        )


def sign_anchor(body: dict[str, Any], *, key: bytes) -> str:
    payload = _canonical_json({k: v for k, v in body.items() if k != "mac"}).encode()
    return _mac_hex(key, _ANCHOR_MAC_LABEL, payload)


def verify_anchor(anchor: SequenceAnchor, *, key: bytes, overlay: RecoveryOverlay) -> None:
    if anchor.kind != SEQUENCE_ANCHOR_KIND:
        raise InvalidInputError(f"unsupported sequence anchor kind {anchor.kind!r}")
    expected = sign_anchor(anchor.to_dict(), key=key)
    if not anchor.mac or not hmac.compare_digest(expected, anchor.mac):
        raise InvalidInputError(
            "SequenceAnchor MAC verification failed",
            details={"reconciliation_required": True},
        )
    if anchor.registry_id != overlay.registry_id:
        raise InvalidInputError(
            "SequenceAnchor registry_id mismatch",
            details={"reconciliation_required": True},
        )
    if anchor.control_sequence < overlay.control_sequence:
        raise InvalidInputError(
            "SequenceAnchor is stale relative to overlay",
            details={
                "anchor_sequence": anchor.control_sequence,
                "overlay_sequence": overlay.control_sequence,
                "reconciliation_required": True,
            },
        )
    if anchor.overlay_digest != overlay.content_digest():
        raise InvalidInputError(
            "SequenceAnchor overlay_digest mismatch",
            details={"reconciliation_required": True},
        )


def build_recovery_overlay(
    cfg: Config,
    *,
    control_sequence: int,
    operator_provenance: str,
    key: bytes | None = None,
    custody_key: bytes | None = None,
) -> RecoveryOverlay:
    """Snapshot current privacy tombstones + trust revocations for restore."""
    auth_key = key or _resolve_auth_key(cfg, custody_key=custody_key, allow_create=True)
    registry_id = ensure_registry_id(cfg)
    deletion_records: list[dict[str, Any]] = []
    tomb_path = evidence_mod.evidence_dir(cfg) / "tombstones.jsonl"
    if tomb_path.is_file():
        for line in tomb_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                deletion_records.append(json.loads(line))
    revocation_records: list[dict[str, Any]] = []
    for decision in trust_mod.list_decisions(cfg):
        if decision.decision == "revoke":
            revocation_records.append(decision.to_dict())
    try:
        policy_digest = trust_mod.load_policy(cfg).digest()
    except Exception:
        policy_digest = _sha256_bytes(b"")
    content_hashes = tuple(
        sorted(
            _sha256_bytes(_canonical_json(r).encode("utf-8"))
            for r in (*deletion_records, *revocation_records)
        )
    )
    body = {
        "kind": RECOVERY_OVERLAY_KIND,
        "registry_id": registry_id,
        "control_sequence": control_sequence,
        "deletion_records": deletion_records,
        "revocation_records": revocation_records,
        "policy_digest": policy_digest,
        "content_hashes": list(content_hashes),
        "operator_provenance": operator_provenance,
    }
    mac = sign_overlay(body, key=auth_key)
    return RecoveryOverlay(
        kind=RECOVERY_OVERLAY_KIND,
        registry_id=registry_id,
        control_sequence=control_sequence,
        deletion_records=tuple(deletion_records),
        revocation_records=tuple(revocation_records),
        policy_digest=policy_digest,
        content_hashes=content_hashes,
        operator_provenance=operator_provenance,
        mac=mac,
    )


def issue_sequence_anchor(
    overlay: RecoveryOverlay,
    *,
    key: bytes,
    control_sequence: int | None = None,
) -> SequenceAnchor:
    seq = overlay.control_sequence if control_sequence is None else control_sequence
    body = {
        "kind": SEQUENCE_ANCHOR_KIND,
        "registry_id": overlay.registry_id,
        "control_sequence": seq,
        "overlay_digest": overlay.content_digest(),
        "issued_at": _now(),
    }
    mac = sign_anchor(body, key=key)
    return SequenceAnchor(
        kind=SEQUENCE_ANCHOR_KIND,
        registry_id=overlay.registry_id,
        control_sequence=seq,
        overlay_digest=str(body["overlay_digest"]),
        issued_at=str(body["issued_at"]),
        mac=mac,
    )


def _append_journal(cfg: Config, entry: dict[str, Any]) -> None:
    path = recovery_journal_path(cfg)
    _mkdir_secure(path.parent)
    line = _canonical_json({"ts": _now(), **entry}) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_dir(path.parent)


def _write_state(cfg: Config, *, reconciliation_required: bool, reason: str, **extra: Any) -> None:
    payload = {
        "kind": RECOVERY_STATE_KIND,
        "reconciliation_required": reconciliation_required,
        "reason": reason,
        "updated_at": _now(),
        **extra,
    }
    _write_json_durable(recovery_state_path(cfg), payload)


def _write_activation_seal(
    cfg: Config,
    *,
    overlay: RecoveryOverlay,
    anchor: SequenceAnchor,
    key: bytes,
) -> None:
    body = {
        "kind": ACTIVATION_SEAL_KIND,
        "registry_id": overlay.registry_id,
        "control_sequence": anchor.control_sequence,
        "overlay_digest": overlay.content_digest(),
        "anchor_mac": anchor.mac,
        "activated_at": _now(),
    }
    payload = _canonical_json(body).encode("utf-8")
    body["mac"] = _mac_hex(key, _SEAL_MAC_LABEL, payload)
    _write_json_durable(recovery_activation_path(cfg), body)
    _write_state(cfg, reconciliation_required=False, reason="activated")


def _verify_activation_seal(cfg: Config, *, key: bytes | None) -> bool:
    path = recovery_activation_path(cfg)
    if not path.is_file():
        return False
    try:
        body = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    if body.get("kind") != ACTIVATION_SEAL_KIND:
        return False
    if key is None:
        return False
    mac = str(body.get("mac") or "")
    check = {k: v for k, v in body.items() if k != "mac"}
    expected = _mac_hex(key, _SEAL_MAC_LABEL, _canonical_json(check).encode("utf-8"))
    return bool(mac) and hmac.compare_digest(mac, expected)


def reconciliation_status(cfg: Config) -> dict[str, Any]:
    state_path = recovery_state_path(cfg)
    if not state_path.is_file():
        # No recovery activity — operational unless interrupted journal says otherwise.
        journal = recovery_journal_path(cfg)
        if journal.is_file():
            lines = [ln for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                try:
                    last = json.loads(lines[-1])
                except json.JSONDecodeError:
                    return {
                        "reconciliation_required": True,
                        "reason": "corrupt recovery journal",
                    }
                if last.get("step") != "activate_complete":
                    return {
                        "reconciliation_required": True,
                        "reason": "interrupted restore journal",
                        "last_step": last.get("step"),
                    }
        return {"reconciliation_required": False, "reason": "no recovery state"}
    try:
        state = _read_json(state_path)
    except (OSError, json.JSONDecodeError):
        return {"reconciliation_required": True, "reason": "corrupt recovery state"}
    if state.get("reconciliation_required"):
        return {
            "reconciliation_required": True,
            "reason": state.get("reason") or "reconciliation_required",
            "state": state,
        }
    # Fail closed: claimed-clear state without a verifiable activation seal.
    try:
        key = _resolve_auth_key(cfg, allow_create=False)
    except InvalidInputError:
        return {
            "reconciliation_required": True,
            "reason": "activation seal unverifiable without fingerprint/custody key",
        }
    if not _verify_activation_seal(cfg, key=key):
        return {
            "reconciliation_required": True,
            "reason": "missing or invalid activation seal",
        }
    return {"reconciliation_required": False, "reason": state.get("reason") or "activated"}


def is_reconciliation_required(cfg: Config) -> bool:
    return bool(reconciliation_status(cfg).get("reconciliation_required"))


def assert_routing_allowed(cfg: Config) -> None:
    if is_reconciliation_required(cfg):
        raise InvalidInputError(
            "routing disabled: reconciliation_required",
            details=reconciliation_status(cfg),
        )


def assert_evidence_access_allowed(cfg: Config) -> None:
    if is_reconciliation_required(cfg):
        raise InvalidInputError(
            "evidence access disabled: reconciliation_required",
            details=reconciliation_status(cfg),
        )


@contextmanager
def _backup_lease(
    cfg: Config, conn: sqlite3.Connection, holder: str
) -> Iterator[None]:
    if lease_mod.cross_process_lease_held():
        with lease_mod.writer_lease(holder=holder):
            yield
        return
    cross = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path,
        conn=conn,
        holder=holder,
    )
    with cross.acquire(), lease_mod.writer_lease(holder=holder):
        yield


def _domain_sequences(cfg: Config) -> dict[str, int]:
    evidence_seq = 0
    root = evidence_mod.evidence_dir(cfg)
    meta_path = root / "meta.json"
    if meta_path.is_file():
        try:
            evidence_seq = int(_read_json(meta_path).get("last_sequence") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            evidence_seq = 0
    trust_seq = len(list(trust_mod.list_decisions(cfg))) if trust_mod.trust_dir(cfg).is_dir() else 0
    approvals_seq = 0
    if cfg.approvals_dir.is_dir():
        for path in cfg.approvals_dir.glob("*.json"):
            try:
                data = _read_json(path)
                approvals_seq = max(approvals_seq, len(data.get("audit_log") or []))
            except (OSError, json.JSONDecodeError):
                continue
    registry_seq = 0
    if cfg.registry_dir.is_dir():
        registry_seq = len(registry_mod._iter_registry_files(cfg.registry_dir, "*.egr.md"))
    policy_seq = 0
    ps = policy_store_dir(cfg)
    if ps.is_dir():
        policy_seq = sum(1 for p in ps.rglob("*") if p.is_file() and not _is_symlink(p))
    return {
        "evidence": evidence_seq,
        "trust": trust_seq,
        "approvals": approvals_seq,
        "registry": registry_seq,
        "policy_store": policy_seq,
        "config": 1 if cfg.toml_path.is_file() else 0,
        "archive": 0,
    }


def _iter_domain_files(cfg: Config, domain: str) -> Iterable[tuple[str, Path]]:
    """Yield ``(archive_relative_path, source_path)`` for a domain."""
    if domain == "registry":
        if not cfg.registry_dir.is_dir():
            return
        for path in registry_mod._iter_registry_files(cfg.registry_dir, "*"):
            if _is_symlink(path) or not path.is_file():
                continue
            rel = _rel_under(cfg.registry_dir, path)
            yield f"registry/{rel}", path
        return
    if domain == "evidence":
        root = evidence_mod.evidence_dir(cfg)
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _is_symlink(path):
                continue
            rel = _rel_under(cfg.data_dir, path)
            if _is_secret_rel(rel):
                continue
            yield rel, path
        return
    if domain == "trust":
        root = trust_mod.trust_dir(cfg)
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if path.is_file() and not _is_symlink(path):
                rel = _rel_under(cfg.data_dir, path)
                yield rel, path
        return
    if domain == "approvals":
        if not cfg.approvals_dir.is_dir():
            return
        for path in sorted(cfg.approvals_dir.glob("*.json")):
            if path.is_file() and not _is_symlink(path):
                yield f"approvals/{path.name}", path
        return
    if domain == "config":
        if cfg.toml_path.is_file() and not _is_symlink(cfg.toml_path):
            yield "config/magicite.toml", cfg.toml_path
        # Non-secret runtime identity (registry.id) — not the fingerprint key.
        rid = cfg.runtime_dir / "registry.id"
        if rid.is_file() and not _is_symlink(rid):
            yield "runtime/registry.id", rid
        return
    if domain == "policy_store":
        root = policy_store_dir(cfg)
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if path.is_file() and not _is_symlink(path):
                rel = _rel_under(cfg.data_dir, path)
                yield rel, path
        return
    if domain == "archive":
        if not cfg.archive_dir.is_dir():
            return
        for path in sorted(cfg.archive_dir.rglob("*")):
            if path.is_file() and not _is_symlink(path):
                rel = _rel_under(cfg.archive_dir, path)
                yield f"archive/{rel}", path
        return
    raise InvalidInputError(f"unknown backup domain {domain!r}")


def create_snapshot(
    cfg: Config,
    conn: sqlite3.Connection,
    dest: str | Path,
    *,
    domains: Iterable[str] | None = None,
    include_secrets: bool = False,
    encrypted_custody_path: str | Path | None = None,
    holder: str = "backup-snapshot",
    fault_hook: FaultHook = None,
) -> dict[str, Any]:
    """Create a consistent ``backup/1`` snapshot under the shared writer lease.

    Takes the recovery-point sequence after evidence checkpoint-friendly state
    (caller should checkpoint durable events first). Secrets are excluded
    unless ``include_secrets`` with ``encrypted_custody_path``.
    """
    selected = tuple(domains) if domains is not None else BACKUP_DOMAINS
    for name in selected:
        if name not in BACKUP_DOMAINS:
            raise InvalidInputError(f"unknown backup domain {name!r}")

    dest_root = Path(dest)
    with _backup_lease(cfg, conn, holder):
        lease_mod.assert_single_writer()
        registry_id = ensure_registry_id(cfg)
        sequences = _domain_sequences(cfg)

        if dest_root.exists():
            shutil.rmtree(dest_root)
        _mkdir_secure(dest_root)
        files_root = dest_root / "files"
        _mkdir_secure(files_root)

        entries: list[dict[str, Any]] = []
        for domain in selected:
            for rel, src in _iter_domain_files(cfg, domain):
                try:
                    data_rel = _rel_under(cfg.data_dir, src)
                except ValueError:
                    data_rel = rel
                if _is_secret_rel(data_rel) and not include_secrets:
                    continue
                if fault_hook is not None:
                    fault_hook(f"boundary:backup_file:{rel}")
                dest_file = _constrained_under(files_root, rel, label="backup archive")
                meta = _copy_file_durable(src, dest_file)
                entries.append(
                    {
                        "path": rel,
                        "sha256": meta["sha256"],
                        "size": meta["size"],
                        "domain": domain,
                    }
                )

        custody_note = "excluded"
        if include_secrets:
            if encrypted_custody_path is None:
                raise InvalidInputError(
                    "include_secrets requires encrypted_custody_path",
                    hint="copy fingerprint.key into operator-managed encrypted custody separately",
                )
            key_src = fingerprint_key_mod.fingerprint_key_path(cfg)
            if key_src.is_file():
                custody = Path(encrypted_custody_path)
                _mkdir_secure(custody.parent)
                _copy_file_durable(key_src, custody)
                custody_note = str(custody)

        entries.sort(key=lambda e: e["path"])
        manifest = {
            "manifest_kind": BACKUP_MANIFEST_KIND,
            "schema_version": 1,
            "created_at": _now(),
            "registry_id": registry_id,
            "domains": list(selected),
            "recovery_point_sequences": sequences,
            "max_known_schema_version_at_backup": MAX_KNOWN_SCHEMA_VERSION,
            "secrets_excluded": not include_secrets,
            "fingerprint_key_custody": custody_note,
            "files": entries,
            "notes": {
                "sqlite_projection": "rebuildable; restore file domains then rebuild",
                "hmac_dependent_stores": (
                    "evidence tombstones.mac and recovery overlays require the "
                    "fingerprint key (or custody key). Clean-machine restore without "
                    "the key fails closed with reconciliation_required — never re-key."
                ),
                "policy_store": (
                    "opaque file-level copy when present; semantic verification forward "
                    "for S07 merge"
                ),
            },
        }
        if BACKUP_MANIFEST_KIND not in SUPPORTED_BACKUP_MANIFEST_KINDS:
            raise InvalidInputError(
                f"build does not accept manifest kind {BACKUP_MANIFEST_KIND!r}",
                details={"supported": sorted(SUPPORTED_BACKUP_MANIFEST_KINDS)},
            )
        manifest_path = dest_root / "manifest.json"
        payload = _canonical_json(manifest).encode("utf-8")
        _write_bytes_durable(manifest_path, payload)
        if fault_hook is not None:
            fault_hook("boundary:backup_committed")
        return {
            **manifest,
            "manifest_digest": _sha256_bytes(payload),
            "backup_root": str(dest_root),
        }


def _load_backup_manifest(backup_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    root = Path(backup_path)
    if root.is_file() and root.name == "manifest.json":
        manifest_path = root
        files_root = root.parent / "files"
    elif (root / "manifest.json").is_file():
        manifest_path = root / "manifest.json"
        files_root = root / "files"
    else:
        raise InvalidInputError(f"no backup/1 manifest at {backup_path}")
    if not files_root.is_dir():
        raise InvalidInputError(f"backup missing files/ directory beside {manifest_path}")
    manifest = _read_json(manifest_path)
    kind = manifest.get("manifest_kind")
    if kind not in SUPPORTED_BACKUP_MANIFEST_KINDS:
        raise InvalidInputError(
            f"unsupported backup manifest kind {kind!r}",
            details={"supported": sorted(SUPPORTED_BACKUP_MANIFEST_KINDS)},
        )
    return files_root, manifest_path, manifest


def _verify_backup_archive(files_root: Path, manifest: dict[str, Any]) -> None:
    root = files_root.resolve()
    for entry in manifest.get("files", []):
        rel = str(entry["path"])
        path = _constrained_under(root, rel, label="backup manifest")
        if _is_symlink(path):
            raise InvalidInputError(f"backup entry is a symlink: {rel!r}")
        if not path.is_file():
            raise InvalidInputError(f"backup missing file {rel!r}")
        actual = _sha256_file(path)
        if actual != entry.get("sha256"):
            raise InvalidInputError(
                f"backup file digest mismatch for {rel!r}",
                details={"expected": entry.get("sha256"), "actual": actual},
            )
        if int(entry.get("size") or -1) != path.stat().st_size:
            raise InvalidInputError(f"backup file size mismatch for {rel!r}")


def _clear_tree_files(dest: Path) -> None:
    if not dest.exists():
        return
    for child in list(dest.iterdir()):
        if _is_symlink(child):
            raise InvalidInputError(f"refusing to clear symlink in restore dest: {child}")
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def _archive_rel_to_dest(dest_data_dir: Path, rel: str) -> Path:
    """Map a backup archive relative path onto ``dest_data_dir``."""
    if rel.startswith("registry/"):
        mapped = f"engrams/{rel[len('registry/') :]}"
    elif rel == "config/magicite.toml":
        mapped = "magicite.toml"
    else:
        mapped = rel
    return _constrained_under(dest_data_dir, mapped, label="restore dest")


def _restore_files_into(
    files_root: Path,
    manifest: dict[str, Any],
    *,
    dest_data_dir: Path,
    domains: Iterable[str],
    fault_hook: FaultHook = None,
) -> None:
    selected = set(domains)
    _mkdir_secure(dest_data_dir)
    for entry in manifest.get("files", []):
        domain = str(entry.get("domain") or "")
        if domain and domain not in selected:
            continue
        rel = str(entry["path"])
        src = _constrained_under(files_root, rel, label="backup restore src")
        dest = _archive_rel_to_dest(dest_data_dir, rel)
        if fault_hook is not None:
            fault_hook(f"boundary:restore_file:{rel}")
        _mkdir_secure(dest.parent)
        _copy_file_durable(src, dest)


def _preserve_live_overlay(
    cfg: Config, *, key: bytes
) -> tuple[RecoveryOverlay | None, SequenceAnchor | None]:
    """Capture live overlay/anchor outside replaced stores (in-place restore)."""
    if not evidence_mod.evidence_dir(cfg).exists() and not trust_mod.trust_dir(cfg).is_dir():
        return None, None
    sequences = _domain_sequences(cfg)
    control = max(sequences.get("evidence", 0), sequences.get("trust", 0), 1)
    overlay = build_recovery_overlay(
        cfg,
        control_sequence=control,
        operator_provenance="live-preserve",
        key=key,
    )
    anchor = issue_sequence_anchor(overlay, key=key)
    _write_json_durable(live_overlay_path(cfg), overlay.to_dict())
    _write_json_durable(live_anchor_path(cfg), anchor.to_dict())
    return overlay, anchor


def _apply_overlay(
    cfg: Config,
    conn: sqlite3.Connection,
    overlay: RecoveryOverlay,
    *,
    key: bytes,
) -> dict[str, Any]:
    verify_overlay(overlay, key=key)
    privacy = evidence_mod.PrivacyOverlay(
        kind=evidence_mod.RECOVERY_OVERLAY_KIND,
        registry_id=overlay.registry_id,
        control_sequence=overlay.control_sequence,
        deletion_records=overlay.deletion_records,
        policy_digest=overlay.policy_digest,
        content_hashes=tuple(
            sorted(
                _sha256_bytes(_canonical_json(r).encode("utf-8"))
                for r in overlay.deletion_records
            )
        ),
        operator_provenance=overlay.operator_provenance,
    )
    privacy_result = evidence_mod.apply_privacy_overlay(
        cfg,
        conn,
        privacy,
        expected_registry_id=overlay.registry_id,
        minimum_sequence=None,
    )
    applied_revokes = 0
    for record in overlay.revocation_records:
        engram_id = str(record.get("engram_id") or "")
        if not engram_id:
            continue
        latest = trust_mod.latest_decision_for(cfg, engram_id)
        if latest is not None and latest.decision == "revoke":
            continue
        decision = trust_mod.TrustDecision.from_dict(record)
        trust_mod.ensure_trust_dirs(cfg)
        trust_mod._write_decision_mirror(cfg, decision)  # noqa: SLF001
        trust_mod._upsert_decision_row(conn, decision)  # noqa: SLF001
        applied_revokes += 1
    return {"privacy": privacy_result, "revokes_applied": applied_revokes}


def _rebuild_projections(cfg: Config, conn: sqlite3.Connection) -> dict[str, Any]:
    evidence_n = 0
    if evidence_mod.evidence_dir(cfg).exists():
        evidence_mod.verify_segments(evidence_mod.evidence_dir(cfg))
        evidence_n = evidence_mod.rebuild_projections(cfg, conn)
    trust_n = trust_mod.reload_from_mirror(cfg, conn)
    approvals_n = approvals_mod.reload_from_mirror(cfg, conn)
    return {
        "evidence_events": evidence_n,
        "trust_decisions": trust_n,
        "approvals": approvals_n,
    }


def restore_snapshot(
    cfg: Config,
    conn: sqlite3.Connection,
    backup_path: str | Path,
    *,
    overlay: RecoveryOverlay | dict[str, Any] | None = None,
    sequence_anchor: SequenceAnchor | dict[str, Any] | None = None,
    custody_key: bytes | None = None,
    preserve_live_overlay: bool = True,
    holder: str = "backup-restore",
    fault_hook: FaultHook = None,
) -> dict[str, Any]:
    """Restore a ``backup/1`` snapshot under the shared writer lease (C8).

    On missing/stale/unauthenticated overlay+anchor, restores only into the
    recovery staging area and leaves routing/evidence gated with
    ``reconciliation_required``. Interrupted restores stay offline via the
    recovery journal (never serve a mixed-generation registry).
    """
    files_root, _manifest_path, manifest = _load_backup_manifest(Path(backup_path))
    _verify_backup_archive(files_root, manifest)

    overlay_obj: RecoveryOverlay | None = None
    if isinstance(overlay, RecoveryOverlay):
        overlay_obj = overlay
    elif isinstance(overlay, dict):
        overlay_obj = RecoveryOverlay.from_dict(overlay)

    anchor_obj: SequenceAnchor | None = None
    if isinstance(sequence_anchor, SequenceAnchor):
        anchor_obj = sequence_anchor
    elif isinstance(sequence_anchor, dict):
        anchor_obj = SequenceAnchor.from_dict(sequence_anchor)

    with _backup_lease(cfg, conn, holder):
        lease_mod.assert_single_writer()
        _mkdir_secure(recovery_dir(cfg))
        _append_journal(cfg, {"step": "restore_begin", "backup": str(backup_path)})
        if fault_hook is not None:
            fault_hook("boundary:restore_begin")

        auth_key: bytes | None = None
        try:
            auth_key = _resolve_auth_key(cfg, custody_key=custody_key, allow_create=False)
        except InvalidInputError:
            auth_key = custody_key

        preserved: tuple[RecoveryOverlay | None, SequenceAnchor | None] = (None, None)
        if preserve_live_overlay and auth_key is not None:
            try:
                preserved = _preserve_live_overlay(cfg, key=auth_key)
                _append_journal(cfg, {"step": "live_overlay_preserved"})
            except InvalidInputError:
                preserved = (None, None)

        if overlay_obj is None and preserved[0] is not None:
            overlay_obj = preserved[0]
        if anchor_obj is None and preserved[1] is not None:
            anchor_obj = preserved[1]

        can_activate = False
        activate_error: str | None = None
        if overlay_obj is None or anchor_obj is None or auth_key is None:
            can_activate = False
            activate_error = "missing overlay, sequence anchor, or auth key"
        else:
            try:
                verify_overlay(overlay_obj, key=auth_key)
                verify_anchor(anchor_obj, key=auth_key, overlay=overlay_obj)
                backup_reg = str(manifest.get("registry_id") or "")
                if backup_reg and overlay_obj.registry_id != backup_reg:
                    # Clean-machine may reuse registry id from overlay; mismatch fails.
                    if load_registry_id(cfg) not in (None, overlay_obj.registry_id, backup_reg):
                        raise InvalidInputError("registry_id mismatch between overlay and live id")
                can_activate = True
            except InvalidInputError as exc:
                can_activate = False
                activate_error = str(exc)

        if not can_activate:
            staging = recovery_staging_dir(cfg)
            if staging.exists():
                shutil.rmtree(staging)
            _mkdir_secure(staging)
            _append_journal(cfg, {"step": "staging_begin", "reason": activate_error})
            if fault_hook is not None:
                fault_hook("boundary:restore_staging")
            # Clear only staging; leave live trees untouched / offline.
            _restore_files_into(
                files_root,
                manifest,
                dest_data_dir=staging,
                domains=manifest.get("domains") or BACKUP_DOMAINS,
                fault_hook=fault_hook,
            )
            _write_state(
                cfg,
                reconciliation_required=True,
                reason=activate_error or "reconciliation_required",
                staging_dir=str(staging),
                recovery_point_sequences=manifest.get("recovery_point_sequences"),
            )
            # Remove activation seal if any — fail closed.
            recovery_activation_path(cfg).unlink(missing_ok=True)
            _append_journal(cfg, {"step": "staging_complete", "reconciliation_required": True})
            if fault_hook is not None:
                fault_hook("boundary:restore_offline")
            return {
                "status": "reconciliation_required",
                "reconciliation_required": True,
                "reason": activate_error,
                "staging_dir": str(staging),
                "activated": False,
            }

        assert overlay_obj is not None and anchor_obj is not None and auth_key is not None

        # In-place replace of authoritative domains (not recovery/, not DB).
        _append_journal(cfg, {"step": "replace_begin"})
        for domain in ("registry", "evidence", "trust", "approvals", "config", "policy_store", "archive"):
            if domain == "registry":
                target = cfg.registry_dir
            elif domain == "evidence":
                target = evidence_mod.evidence_dir(cfg)
            elif domain == "trust":
                target = trust_mod.trust_dir(cfg)
            elif domain == "approvals":
                target = cfg.approvals_dir
            elif domain == "policy_store":
                target = policy_store_dir(cfg)
            elif domain == "archive":
                target = cfg.archive_dir
            else:
                target = None
            domains = manifest.get("domains") or BACKUP_DOMAINS
            if target is not None and target.exists() and domain in domains:
                if domain == "evidence":
                    # Preserve nothing under evidence; live overlay already saved.
                    _clear_tree_files(target)
                elif domain in {"trust", "approvals", "policy_store", "archive"}:
                    _clear_tree_files(target)
                elif domain == "registry":
                    for path in registry_mod._iter_registry_files(cfg.registry_dir, "*"):
                        if path.is_file():
                            path.unlink()

        if fault_hook is not None:
            fault_hook("boundary:restore_cleared")

        _restore_files_into(
            files_root,
            manifest,
            dest_data_dir=cfg.data_dir,
            domains=manifest.get("domains") or BACKUP_DOMAINS,
            fault_hook=fault_hook,
        )
        _append_journal(cfg, {"step": "files_restored"})
        if fault_hook is not None:
            fault_hook("boundary:restore_files")

        # Apply current overlay (revocations/deletions) BEFORE activation.
        overlay_result = _apply_overlay(cfg, conn, overlay_obj, key=auth_key)
        _append_journal(cfg, {"step": "overlay_applied", "result": overlay_result})
        if fault_hook is not None:
            fault_hook("boundary:overlay_applied")

        rebuild = _rebuild_projections(cfg, conn)
        _append_journal(cfg, {"step": "projections_rebuilt", "result": rebuild})
        if fault_hook is not None:
            fault_hook("boundary:rebuild_done")

        _write_activation_seal(cfg, overlay=overlay_obj, anchor=anchor_obj, key=auth_key)
        _append_journal(cfg, {"step": "activate_complete"})
        if fault_hook is not None:
            fault_hook("boundary:restore_complete")

        return {
            "status": "ok",
            "reconciliation_required": False,
            "activated": True,
            "overlay": overlay_result,
            "rebuild": rebuild,
            "recovery_point_sequences": manifest.get("recovery_point_sequences"),
            "registry_id": overlay_obj.registry_id,
        }


def resume_or_rollback_restore(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    rollback: bool = False,
    holder: str = "backup-resume",
) -> dict[str, Any]:
    """Resume an interrupted restore or roll back to offline staging (C8)."""
    with _backup_lease(cfg, conn, holder):
        lease_mod.assert_single_writer()
        journal = recovery_journal_path(cfg)
        if not journal.is_file():
            return {"status": "noop", "reconciliation_required": is_reconciliation_required(cfg)}
        lines = [json.loads(ln) for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines and lines[-1].get("step") == "activate_complete":
            return {"status": "already_complete", "reconciliation_required": False}
        if rollback:
            staging = recovery_staging_dir(cfg)
            _write_state(
                cfg,
                reconciliation_required=True,
                reason="restore rolled back; offline until reconciled",
                staging_dir=str(staging) if staging.exists() else None,
            )
            recovery_activation_path(cfg).unlink(missing_ok=True)
            _append_journal(cfg, {"step": "rollback_complete"})
            return {"status": "rolled_back", "reconciliation_required": True}
        _write_state(
            cfg,
            reconciliation_required=True,
            reason="interrupted restore; resume requires verified overlay/anchor",
        )
        return {"status": "offline", "reconciliation_required": True}
