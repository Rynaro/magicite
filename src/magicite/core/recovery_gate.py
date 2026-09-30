"""S12 recovery gate leaf (C8).

Tiny import-safe module: evidence and (later) router call these gates without
importing ``backup`` (avoids cycles). Backup writes generation markers and
activation seals; this module only *reads* them to decide fail-closed status.

Gate clearance rule (B2):
  ``reconciliation_required`` clears **only** when a valid HMAC activation seal
  matches the durable restore-generation markers stamped into ``runtime/`` and
  each restored authoritative domain root. Deleting ``recovery/``, forging a
  journal ``activate_complete`` line, or deleting ``state.json`` cannot clear
  the gate while unmatched generation markers remain.

Limits: an attacker who can delete *all* domain markers *and* the runtime
marker (full write access to every authoritative root) can erase the evidence
of an incomplete restore — that is equivalent to wiping the registry and is
outside the "delete recovery/ alone" threat model this marker set closes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.errors import InvalidInputError

ACTIVATION_SEAL_KIND = "recovery_activation/1"
RESTORE_GENERATION_KIND = "restore_generation/1"
DOMAIN_MARKER_NAME = ".magicite-restore-generation.json"
RUNTIME_MARKER_NAME = ".restore-generation"

_SEAL_MAC_LABEL = b"magicite/recovery-activation/v1"
_GENERATION_MAC_LABEL = b"magicite/restore-generation/v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _mac_hex(key: bytes, label: bytes, payload: bytes) -> str:
    sub = hmac.new(key, label, hashlib.sha256).digest()
    return hmac.new(sub, payload, hashlib.sha256).hexdigest()


def recovery_dir(cfg: Config) -> Path:
    return cfg.data_dir / "recovery"


def recovery_activation_path(cfg: Config) -> Path:
    return recovery_dir(cfg) / "activation.json"


def runtime_generation_marker_path(cfg: Config) -> Path:
    return cfg.runtime_dir / RUNTIME_MARKER_NAME


def domain_marker_path(domain_root: Path) -> Path:
    return domain_root / DOMAIN_MARKER_NAME


def _authoritative_domain_roots(cfg: Config) -> dict[str, Path]:
    """Roots that may carry a restore-generation marker (outside recovery/)."""
    return {
        "runtime": cfg.runtime_dir,
        "registry": cfg.registry_dir,
        "evidence": cfg.data_dir / "evidence",
        "trust": cfg.data_dir / "trust",
        "approvals": cfg.approvals_dir,
        "policy_store": cfg.data_dir / "policy_store",
        "archive": cfg.archive_dir,
        "config": cfg.data_dir,  # marker sits beside magicite.toml as DOMAIN_MARKER_NAME
    }


def _try_load_key(cfg: Config) -> bytes | None:
    path = fingerprint_key_mod.fingerprint_key_path(cfg)
    if not path.is_file():
        return None
    try:
        return fingerprint_key_mod._read_complete_key(path)  # noqa: SLF001
    except (OSError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _verify_generation_marker(body: dict[str, Any], *, key: bytes) -> bool:
    if body.get("kind") != RESTORE_GENERATION_KIND:
        return False
    mac = str(body.get("mac") or "")
    check = {k: v for k, v in body.items() if k != "mac"}
    expected = _mac_hex(key, _GENERATION_MAC_LABEL, _canonical_json(check).encode("utf-8"))
    return bool(mac) and hmac.compare_digest(mac, expected)


def _verify_activation_seal_body(body: dict[str, Any], *, key: bytes) -> bool:
    if body.get("kind") != ACTIVATION_SEAL_KIND:
        return False
    mac = str(body.get("mac") or "")
    check = {k: v for k, v in body.items() if k != "mac"}
    expected = _mac_hex(key, _SEAL_MAC_LABEL, _canonical_json(check).encode("utf-8"))
    return bool(mac) and hmac.compare_digest(mac, expected)


def collect_generation_markers(cfg: Config) -> list[dict[str, Any]]:
    """Return raw marker bodies found under runtime + domain roots."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, root in _authoritative_domain_roots(cfg).items():
        if name == "runtime":
            path = runtime_generation_marker_path(cfg)
        elif name == "config":
            path = cfg.data_dir / DOMAIN_MARKER_NAME
        else:
            path = domain_marker_path(root)
        if not path.is_file():
            continue
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        body = _read_json(path)
        if body is not None:
            found.append({"path": str(path), "domain": name, "body": body})
    return found


def last_activation_sequence(cfg: Config) -> int | None:
    """Return control_sequence from a *structurally present* activation seal file.

    Integrity is checked separately when a key is available; this helper is for
    monotonicity comparisons during restore planning.
    """
    path = recovery_activation_path(cfg)
    body = _read_json(path)
    if body is None:
        return None
    try:
        return int(body.get("control_sequence") or 0)
    except (TypeError, ValueError):
        return None


def reconciliation_status(cfg: Config) -> dict[str, Any]:
    """Fail-closed status. Cleared only by a valid activation seal (B2)."""
    markers = collect_generation_markers(cfg)
    if not markers:
        # No restore evidence outside recovery/ — operational.
        return {"reconciliation_required": False, "reason": "no restore generation markers"}

    key = _try_load_key(cfg)
    if key is None:
        return {
            "reconciliation_required": True,
            "reason": "restore generation markers present but fingerprint key absent",
            "marker_count": len(markers),
        }

    verified_gens: set[str] = set()
    for item in markers:
        body = item["body"]
        if not _verify_generation_marker(body, key=key):
            return {
                "reconciliation_required": True,
                "reason": "corrupt or forged restore generation marker",
                "path": item["path"],
            }
        verified_gens.add(str(body.get("generation_id") or ""))

    if not verified_gens or "" in verified_gens:
        return {
            "reconciliation_required": True,
            "reason": "restore generation marker missing generation_id",
        }

    seal_path = recovery_activation_path(cfg)
    seal = _read_json(seal_path) if seal_path.is_file() else None
    if seal is None:
        return {
            "reconciliation_required": True,
            "reason": "restore generation markers present without activation seal",
            "generations": sorted(verified_gens),
        }
    if not _verify_activation_seal_body(seal, key=key):
        return {
            "reconciliation_required": True,
            "reason": "activation seal missing or MAC invalid",
            "generations": sorted(verified_gens),
        }
    seal_gen = str(seal.get("generation_id") or "")
    if seal_gen not in verified_gens:
        return {
            "reconciliation_required": True,
            "reason": "activation seal generation_id does not match domain markers",
            "seal_generation": seal_gen,
            "marker_generations": sorted(verified_gens),
        }
    # All markers for other generations would also need matching seals; V1 requires
    # a single active generation — any extra generation_id fails closed.
    if verified_gens != {seal_gen}:
        return {
            "reconciliation_required": True,
            "reason": "multiple restore generations present without matching seal",
            "marker_generations": sorted(verified_gens),
            "seal_generation": seal_gen,
        }
    return {
        "reconciliation_required": False,
        "reason": "activated",
        "generation_id": seal_gen,
        "control_sequence": seal.get("control_sequence"),
    }


def is_reconciliation_required(cfg: Config) -> bool:
    return bool(reconciliation_status(cfg).get("reconciliation_required"))


def assert_routing_allowed(cfg: Config) -> None:
    """Serve-path gate for route() — wire after S07 merges (C9)."""
    if is_reconciliation_required(cfg):
        raise InvalidInputError(
            "routing disabled: reconciliation_required",
            details={**reconciliation_status(cfg), "reconciliation_required": True},
        )


def assert_evidence_access_allowed(cfg: Config) -> None:
    """Serve-path gate for evidence READ/EXPORT entry points."""
    if is_reconciliation_required(cfg):
        raise InvalidInputError(
            "evidence access disabled: reconciliation_required",
            details={**reconciliation_status(cfg), "reconciliation_required": True},
        )


def sign_generation_marker(
    *,
    generation_id: str,
    registry_id: str,
    control_sequence: int,
    key: bytes,
) -> dict[str, Any]:
    body = {
        "kind": RESTORE_GENERATION_KIND,
        "generation_id": generation_id,
        "registry_id": registry_id,
        "control_sequence": control_sequence,
    }
    body["mac"] = _mac_hex(key, _GENERATION_MAC_LABEL, _canonical_json(body).encode("utf-8"))
    return body


def sign_activation_seal(
    *,
    generation_id: str,
    registry_id: str,
    control_sequence: int,
    overlay_digest: str,
    anchor_mac: str,
    activated_at: str,
    key: bytes,
) -> dict[str, Any]:
    body = {
        "kind": ACTIVATION_SEAL_KIND,
        "generation_id": generation_id,
        "registry_id": registry_id,
        "control_sequence": control_sequence,
        "overlay_digest": overlay_digest,
        "anchor_mac": anchor_mac,
        "activated_at": activated_at,
    }
    body["mac"] = _mac_hex(key, _SEAL_MAC_LABEL, _canonical_json(body).encode("utf-8"))
    return body
