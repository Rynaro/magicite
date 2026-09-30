"""Durable stable-policy store (contracts.md C7 / S07).

Mandatory even when S10 is absent. File-based under the Magicite data dir
(no SQL migration — slots 5–7 reserved for S04/S09/S12). Atomic writes use
tmp + fsync + rename + dir fsync under the writer lease. Integrity digest/MAC
over stored state; corruption fails closed.

State path: ``candidate -> evaluated -> approved -> active -> retired``.
Activation and rollback use expected-current compare-and-swap.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from magicite.config import Config
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.errors import InvalidInputError, NotFoundError
from magicite.storage import lease as lease_mod

POLICY_STORE_SCHEMA = "PolicyStoreState/1"
_MAC_DOMAIN = b"magicite/policy-store/v1"

PolicyState = Literal["candidate", "evaluated", "approved", "active", "retired"]
EvaluationStatus = Literal["pass", "fail", "inconclusive", "unevaluated"]


@dataclass(frozen=True)
class PolicyManifest:
    """Reviewed policy artifact identity + matching index/calibration/config."""

    policy_id: str
    policy_digest: str
    policy_family: Literal["stable", "experimental"]
    config_digest: str
    calibration_digest: str | None
    index_generation_id: str | None
    snapshot_id: str | None
    selection: str
    reviewed: bool = False
    evaluation_status: EvaluationStatus = "unevaluated"
    evaluation_evidence: str = ""
    schema_version: str = "PolicyManifest/1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_digest": self.policy_digest,
            "policy_family": self.policy_family,
            "config_digest": self.config_digest,
            "calibration_digest": self.calibration_digest,
            "index_generation_id": self.index_generation_id,
            "snapshot_id": self.snapshot_id,
            "selection": self.selection,
            "reviewed": self.reviewed,
            "evaluation_status": self.evaluation_status,
            "evaluation_evidence": self.evaluation_evidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolicyManifest:
        return cls(
            policy_id=str(data["policy_id"]),
            policy_digest=str(data["policy_digest"]),
            policy_family=data["policy_family"],
            config_digest=str(data["config_digest"]),
            calibration_digest=(
                str(data["calibration_digest"]) if data.get("calibration_digest") else None
            ),
            index_generation_id=(
                str(data["index_generation_id"]) if data.get("index_generation_id") else None
            ),
            snapshot_id=str(data["snapshot_id"]) if data.get("snapshot_id") else None,
            selection=str(data.get("selection") or ""),
            reviewed=bool(data.get("reviewed", False)),
            evaluation_status=data.get("evaluation_status") or "unevaluated",
            evaluation_evidence=str(data.get("evaluation_evidence") or ""),
        )


@dataclass(frozen=True)
class PolicyRecord:
    digest: str
    state: PolicyState
    manifest: PolicyManifest
    approval_id: str | None = None


@dataclass(frozen=True)
class PolicyStoreStatus:
    active_digest: str | None
    prior_digest: str | None
    records: tuple[PolicyRecord, ...]
    state_digest: str


def policy_store_dir(cfg: Config) -> Path:
    return cfg.data_dir / "policy_store"


def policy_store_path(cfg: Config) -> Path:
    return policy_store_dir(cfg) / "state.json"


def _mac_subkey(cfg: Config) -> bytes:
    root = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return hmac.new(root, _MAC_DOMAIN, hashlib.sha256).digest()


def _canonical_body(state: Mapping[str, Any]) -> str:
    body = {k: v for k, v in state.items() if k != "integrity_mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_mac(cfg: Config, state: Mapping[str, Any]) -> str:
    return hmac.new(_mac_subkey(cfg), _canonical_body(state).encode("utf-8"), hashlib.sha256).hexdigest()


def _state_digest(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_body(state).encode("utf-8")).hexdigest()


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": POLICY_STORE_SCHEMA,
        "active_digest": None,
        "prior_digest": None,
        "records": {},
        "approvals": {},
        "audit": [],
    }


def _fsync_dir(directory: Path) -> None:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _load_raw(cfg: Config) -> dict[str, Any]:
    path = policy_store_path(cfg)
    if not path.is_file():
        return _empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInputError(f"policy store corrupt or unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidInputError("policy store corrupt: root must be object")
    if raw.get("schema_version") != POLICY_STORE_SCHEMA:
        raise InvalidInputError(
            f"unsupported policy store schema {raw.get('schema_version')!r}"
        )
    mac = str(raw.get("integrity_mac") or "")
    expected = _compute_mac(cfg, raw)
    if not mac or not hmac.compare_digest(mac, expected):
        raise InvalidInputError("policy store integrity MAC mismatch (fail closed)")
    return raw


def _save_raw(cfg: Config, state: dict[str, Any]) -> None:
    state = dict(state)
    state["schema_version"] = POLICY_STORE_SCHEMA
    state["integrity_mac"] = _compute_mac(cfg, state)
    text = json.dumps(state, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(policy_store_path(cfg), text)


def _record_from_dict(digest: str, data: Mapping[str, Any]) -> PolicyRecord:
    return PolicyRecord(
        digest=digest,
        state=str(data["state"]),  # type: ignore[arg-type]
        manifest=PolicyManifest.from_dict(data["manifest"]),
        approval_id=str(data["approval_id"]) if data.get("approval_id") else None,
    )


def _append_audit(state: dict[str, Any], event: str, **fields: Any) -> None:
    audit = list(state.get("audit") or [])
    entry = {"event": event, **fields}
    audit.append(entry)
    # Bound audit log to avoid unbounded growth in unit tests / long runs.
    state["audit"] = audit[-256:]


def register_evaluated(
    cfg: Config,
    manifest: PolicyManifest,
    *,
    evaluation_status: EvaluationStatus,
    evidence: str = "",
) -> PolicyRecord:
    """Register (or update) a candidate as evaluated. Does not activate."""
    if evaluation_status not in {"pass", "fail", "inconclusive", "unevaluated"}:
        raise InvalidInputError(f"invalid evaluation_status {evaluation_status!r}")
    updated = PolicyManifest(
        policy_id=manifest.policy_id,
        policy_digest=manifest.policy_digest,
        policy_family=manifest.policy_family,
        config_digest=manifest.config_digest,
        calibration_digest=manifest.calibration_digest,
        index_generation_id=manifest.index_generation_id,
        snapshot_id=manifest.snapshot_id,
        selection=manifest.selection,
        reviewed=False,
        evaluation_status=evaluation_status,
        evaluation_evidence=evidence,
    )
    digest = updated.policy_digest
    with lease_mod.writer_lease(holder="policy-store-register"):
        state = _load_raw(cfg)
        records = dict(state.get("records") or {})
        existing = records.get(digest)
        if existing and existing.get("state") == "active":
            raise InvalidInputError("refusing to overwrite an active policy record")
        records[digest] = {
            "state": "evaluated",
            "manifest": updated.to_dict(),
            "approval_id": None,
        }
        state["records"] = records
        _append_audit(
            state,
            "register_evaluated",
            digest=digest,
            evaluation_status=evaluation_status,
        )
        _save_raw(cfg, state)
    return PolicyRecord(digest=digest, state="evaluated", manifest=updated, approval_id=None)


def approve(cfg: Config, policy_digest: str, *, actor: str) -> str:
    """Mark an evaluated artifact as reviewed/approved. Returns approval_id."""
    with lease_mod.writer_lease(holder="policy-store-approve"):
        state = _load_raw(cfg)
        records = dict(state.get("records") or {})
        row = records.get(policy_digest)
        if row is None:
            raise NotFoundError(f"no policy record for digest {policy_digest!r}")
        if row["state"] not in {"evaluated", "approved"}:
            raise InvalidInputError(
                f"policy {policy_digest!r} in state {row['state']!r} cannot be approved"
            )
        manifest = PolicyManifest.from_dict(row["manifest"])
        if manifest.evaluation_status == "unevaluated":
            raise InvalidInputError("cannot approve an unevaluated policy artifact")
        approval_id = row.get("approval_id") or f"polappr_{uuid.uuid4().hex[:12]}"
        reviewed = PolicyManifest(
            policy_id=manifest.policy_id,
            policy_digest=manifest.policy_digest,
            policy_family=manifest.policy_family,
            config_digest=manifest.config_digest,
            calibration_digest=manifest.calibration_digest,
            index_generation_id=manifest.index_generation_id,
            snapshot_id=manifest.snapshot_id,
            selection=manifest.selection,
            reviewed=True,
            evaluation_status=manifest.evaluation_status,
            evaluation_evidence=manifest.evaluation_evidence,
        )
        records[policy_digest] = {
            "state": "approved",
            "manifest": reviewed.to_dict(),
            "approval_id": approval_id,
        }
        approvals = dict(state.get("approvals") or {})
        approvals[approval_id] = {
            "policy_digest": policy_digest,
            "actor": actor,
        }
        state["records"] = records
        state["approvals"] = approvals
        _append_audit(state, "approve", digest=policy_digest, approval_id=approval_id, actor=actor)
        _save_raw(cfg, state)
        return approval_id


def activate(
    cfg: Config,
    *,
    expected_current: str | None,
    candidate_digest: str,
    approval_id: str,
) -> PolicyStoreStatus:
    """Compare-and-swap activate a reviewed artifact.

    ``expected_current`` must match the live active digest (``None`` when no
    incumbent). Stale expected-current is rejected. Only reviewed+approved
    artifacts with a matching approval_id may activate.
    """
    with lease_mod.writer_lease(holder="policy-store-activate"):
        state = _load_raw(cfg)
        active = state.get("active_digest")
        if active != expected_current:
            raise InvalidInputError(
                "stale expected_current for policy activation",
                details={
                    "expected_current": expected_current,
                    "actual_current": active,
                    "reason": "stale_current",
                },
            )
        records = dict(state.get("records") or {})
        row = records.get(candidate_digest)
        if row is None:
            raise NotFoundError(f"no policy record for digest {candidate_digest!r}")
        if row["state"] not in {"approved", "active"}:
            raise InvalidInputError(
                f"policy {candidate_digest!r} is not approved (state={row['state']!r})"
            )
        manifest = PolicyManifest.from_dict(row["manifest"])
        if not manifest.reviewed:
            raise InvalidInputError("only reviewed artifacts can be activated")
        if row.get("approval_id") != approval_id:
            raise InvalidInputError("approval_id does not match the candidate record")
        approvals = state.get("approvals") or {}
        if approval_id not in approvals:
            raise InvalidInputError("unknown approval_id")

        # Retire previous active if present and distinct.
        if active and active != candidate_digest and active in records:
            prev = dict(records[active])
            prev_manifest = PolicyManifest.from_dict(prev["manifest"])
            records[active] = {
                "state": "retired",
                "manifest": prev_manifest.to_dict(),
                "approval_id": prev.get("approval_id"),
            }

        records[candidate_digest] = {
            "state": "active",
            "manifest": manifest.to_dict(),
            "approval_id": approval_id,
        }
        state["prior_digest"] = active
        state["active_digest"] = candidate_digest
        state["records"] = records
        _append_audit(
            state,
            "activate",
            digest=candidate_digest,
            expected_current=expected_current,
            approval_id=approval_id,
        )
        _save_raw(cfg, state)
        return status(cfg)


def rollback(
    cfg: Config,
    *,
    expected_current: str | None,
    prior_digest: str,
) -> PolicyStoreStatus:
    """Exact rollback to a previously approved incumbent (and its manifests)."""
    with lease_mod.writer_lease(holder="policy-store-rollback"):
        state = _load_raw(cfg)
        active = state.get("active_digest")
        if active != expected_current:
            raise InvalidInputError(
                "stale expected_current for policy rollback",
                details={
                    "expected_current": expected_current,
                    "actual_current": active,
                    "reason": "stale_current",
                },
            )
        records = dict(state.get("records") or {})
        prior_row = records.get(prior_digest)
        if prior_row is None:
            raise NotFoundError(f"no policy record for prior digest {prior_digest!r}")
        prior_manifest = PolicyManifest.from_dict(prior_row["manifest"])
        if not prior_manifest.reviewed:
            raise InvalidInputError("cannot rollback to an unreviewed artifact")
        if prior_row["state"] not in {"approved", "active", "retired"}:
            raise InvalidInputError(
                f"prior policy {prior_digest!r} is not rollback-eligible "
                f"(state={prior_row['state']!r})"
            )
        if not prior_row.get("approval_id"):
            raise InvalidInputError("prior policy lacks approval_id; refusing rollback")

        if active and active in records and active != prior_digest:
            cur = dict(records[active])
            cur_manifest = PolicyManifest.from_dict(cur["manifest"])
            records[active] = {
                "state": "retired",
                "manifest": cur_manifest.to_dict(),
                "approval_id": cur.get("approval_id"),
            }

        records[prior_digest] = {
            "state": "active",
            "manifest": prior_manifest.to_dict(),
            "approval_id": prior_row.get("approval_id"),
        }
        state["prior_digest"] = active
        state["active_digest"] = prior_digest
        state["records"] = records
        _append_audit(
            state,
            "rollback",
            digest=prior_digest,
            expected_current=expected_current,
        )
        _save_raw(cfg, state)
        return status(cfg)


def status(cfg: Config) -> PolicyStoreStatus:
    state = _load_raw(cfg)
    records_raw = state.get("records") or {}
    records = tuple(
        _record_from_dict(digest, row) for digest, row in sorted(records_raw.items())
    )
    return PolicyStoreStatus(
        active_digest=state.get("active_digest"),
        prior_digest=state.get("prior_digest"),
        records=records,
        state_digest=_state_digest(state),
    )


def get_active_manifest(cfg: Config) -> PolicyManifest | None:
    st = status(cfg)
    if st.active_digest is None:
        return None
    for rec in st.records:
        if rec.digest == st.active_digest:
            return rec.manifest
    return None


def retain_simple_incumbent_evidence(
    *,
    incumbent_policy_id: str,
    incumbent_digest: str,
    hybrid_status: EvaluationStatus,
    evidence: str,
) -> dict[str, Any]:
    """Preregistered paired-comparison harness hook (evaluation.md).

    When hybrid cannot be promoted, retain the frozen strongest simple
    incumbent with explicit failed/inconclusive/unevaluated evidence.
    Never records PASS without a real empirical run.
    """
    if hybrid_status == "pass":
        raise InvalidInputError(
            "refusing to record hybrid PASS without an externally verified run; "
            "use register_evaluated after real evaluation artifacts exist"
        )
    return {
        "selected_incumbent_policy_id": incumbent_policy_id,
        "selected_incumbent_digest": incumbent_digest,
        "hybrid_evaluation_status": hybrid_status,
        "evidence": evidence,
        "default_remains_simple_incumbent": True,
    }


__all__ = [
    "POLICY_STORE_SCHEMA",
    "EvaluationStatus",
    "PolicyManifest",
    "PolicyRecord",
    "PolicyStoreStatus",
    "activate",
    "approve",
    "get_active_manifest",
    "policy_store_dir",
    "policy_store_path",
    "register_evaluated",
    "retain_simple_incumbent_evidence",
    "rollback",
    "status",
]
