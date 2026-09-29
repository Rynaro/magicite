"""Digest-bound local trust and TrustDecision/1 (C10 / S04).

Integrity (signature), origin authenticity, local review admission, and
lifecycle eligibility are independent dimensions. An imported signature alone
never grants routability. Approvals bind content digest + policy revision;
any byte or policy change invalidates prior admission.

Durable ledger lives outside the disposable retrieval DB (JSON mirrors under
``.magicite/trust/``), reloaded on ``sync()`` like approvals.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from magicite.config import Config
from magicite.core.bundles import TrustRoot, public_key_fingerprint
from magicite.engram.digests import canonical_json_bytes, sha256_hex
from magicite.errors import InvalidInputError, NotFoundError

TRUST_DECISION_SCHEMA = "TrustDecision/1"
TRUST_POLICY_SCHEMA = "TrustPolicy/1"
DEFAULT_POLICY_ID = "local-trust-v1"

DecisionKind = Literal["admit", "reject", "revoke", "quarantine", "pending"]
SourceChannel = Literal[
    "local_authored",
    "local_register",
    "bundle_import",
    "external_file",
    "skillmd_import",
    "unknown",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def trust_dir(cfg: Config) -> Path:
    return cfg.data_dir / "trust"


def trust_decisions_dir(cfg: Config) -> Path:
    return trust_dir(cfg) / "decisions"


def trust_policy_path(cfg: Config) -> Path:
    return trust_dir(cfg) / "policy.json"


def trust_roots_path(cfg: Config) -> Path:
    return trust_dir(cfg) / "roots.json"


def ensure_trust_dirs(cfg: Config) -> None:
    trust_decisions_dir(cfg).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    """Local trust policy pin (keys + revocation + revision)."""

    policy_id: str
    revision: int
    roots: tuple[TrustRoot, ...]
    scanner_revision: str = "scanner/1"
    schema: str = TRUST_POLICY_SCHEMA

    def digest(self) -> str:
        payload = {
            "policy_id": self.policy_id,
            "revision": self.revision,
            "roots": [
                {"fingerprint": r.fingerprint, "revoked": r.revoked}
                for r in sorted(self.roots, key=lambda x: x.fingerprint)
            ],
            "scanner_revision": self.scanner_revision,
            "schema": self.schema,
        }
        return sha256_hex(canonical_json_bytes(payload))

    def active_roots(self) -> tuple[TrustRoot, ...]:
        return tuple(r for r in self.roots if not r.revoked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "policy_id": self.policy_id,
            "revision": self.revision,
            "scanner_revision": self.scanner_revision,
            "roots": [
                {
                    "fingerprint": r.fingerprint,
                    "public_key_sha256": r.fingerprint,
                    "public_key_hex": r.public_key_bytes.hex(),
                    "revoked": r.revoked,
                }
                for r in self.roots
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustPolicy:
        roots: list[TrustRoot] = []
        for item in data.get("roots") or []:
            raw_hex = item.get("public_key_hex")
            if not isinstance(raw_hex, str):
                raise InvalidInputError("trust root missing public_key_hex")
            key_bytes = bytes.fromhex(raw_hex)
            fp = item.get("fingerprint") or public_key_fingerprint(key_bytes)
            if fp != public_key_fingerprint(key_bytes):
                raise InvalidInputError(f"trust root fingerprint mismatch for {fp!r}")
            roots.append(
                TrustRoot(
                    fingerprint=fp,
                    public_key_bytes=key_bytes,
                    revoked=bool(item.get("revoked")),
                )
            )
        return cls(
            policy_id=str(data.get("policy_id") or DEFAULT_POLICY_ID),
            revision=int(data.get("revision") or 1),
            roots=tuple(roots),
            scanner_revision=str(data.get("scanner_revision") or "scanner/1"),
            schema=str(data.get("schema") or TRUST_POLICY_SCHEMA),
        )


def default_policy() -> TrustPolicy:
    return TrustPolicy(policy_id=DEFAULT_POLICY_ID, revision=1, roots=())


def load_policy(cfg: Config) -> TrustPolicy:
    path = trust_policy_path(cfg)
    if not path.is_file():
        return default_policy()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInputError(f"corrupt trust policy at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("trust policy root must be an object")
    return TrustPolicy.from_dict(data)


def save_policy(cfg: Config, policy: TrustPolicy) -> TrustPolicy:
    ensure_trust_dirs(cfg)
    path = trust_policy_path(cfg)
    tmp = path.with_name(path.name + ".tmp")
    content = json.dumps(policy.to_dict(), indent=2, sort_keys=True) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    return policy


def pin_trust_root(cfg: Config, *, public_key_bytes: bytes, revoke: bool = False) -> TrustPolicy:
    """Add or update a pinned public key under a new policy revision."""
    fp = public_key_fingerprint(public_key_bytes)
    current = load_policy(cfg)
    roots = [r for r in current.roots if r.fingerprint != fp]
    roots.append(TrustRoot(fingerprint=fp, public_key_bytes=public_key_bytes, revoked=revoke))
    updated = TrustPolicy(
        policy_id=current.policy_id,
        revision=current.revision + 1,
        roots=tuple(sorted(roots, key=lambda r: r.fingerprint)),
        scanner_revision=current.scanner_revision,
    )
    return save_policy(cfg, updated)


def revoke_trust_root(cfg: Config, *, fingerprint: str) -> TrustPolicy:
    current = load_policy(cfg)
    found = False
    roots: list[TrustRoot] = []
    for root in current.roots:
        if root.fingerprint == fingerprint:
            found = True
            roots.append(
                TrustRoot(
                    fingerprint=root.fingerprint,
                    public_key_bytes=root.public_key_bytes,
                    revoked=True,
                )
            )
        else:
            roots.append(root)
    if not found:
        raise NotFoundError(f"no trust root with fingerprint {fingerprint!r}")
    updated = TrustPolicy(
        policy_id=current.policy_id,
        revision=current.revision + 1,
        roots=tuple(roots),
        scanner_revision=current.scanner_revision,
    )
    return save_policy(cfg, updated)


@dataclass(frozen=True, slots=True)
class TrustDecision:
    """C10 TrustDecision/1 — durable local review record."""

    decision_id: str
    engram_id: str
    content_digest: str
    decision: DecisionKind
    source_channel: SourceChannel
    policy_id: str
    policy_revision: int
    policy_digest: str
    actor: str
    timestamp: str
    reasons: tuple[str, ...] = ()
    signer_fingerprint: str | None = None
    signature_valid: bool | None = None
    scanner_revision: str = "scanner/1"
    resource_digest: str | None = None
    event_id: str | None = None  # idempotency / replay key
    schema: str = TRUST_DECISION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "decision_id": self.decision_id,
            "engram_id": self.engram_id,
            "content_digest": self.content_digest,
            "resource_digest": self.resource_digest,
            "decision": self.decision,
            "source_channel": self.source_channel,
            "signer_fingerprint": self.signer_fingerprint,
            "signature_valid": self.signature_valid,
            "policy_id": self.policy_id,
            "policy_revision": self.policy_revision,
            "policy_digest": self.policy_digest,
            "scanner_revision": self.scanner_revision,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "reasons": list(self.reasons),
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustDecision:
        if data.get("schema") not in (None, TRUST_DECISION_SCHEMA):
            raise InvalidInputError(f"unsupported trust decision schema {data.get('schema')!r}")
        reasons_raw = data.get("reasons") or []
        return cls(
            decision_id=str(data["decision_id"]),
            engram_id=str(data["engram_id"]),
            content_digest=str(data["content_digest"]),
            decision=cast(DecisionKind, data["decision"]),
            source_channel=cast(SourceChannel, data["source_channel"]),
            policy_id=str(data["policy_id"]),
            policy_revision=int(data["policy_revision"]),
            policy_digest=str(data["policy_digest"]),
            actor=str(data["actor"]),
            timestamp=str(data["timestamp"]),
            reasons=tuple(str(r) for r in reasons_raw),
            signer_fingerprint=data.get("signer_fingerprint"),
            signature_valid=data.get("signature_valid"),
            scanner_revision=str(data.get("scanner_revision") or "scanner/1"),
            resource_digest=data.get("resource_digest"),
            event_id=data.get("event_id"),
            schema=TRUST_DECISION_SCHEMA,
        )


@dataclass(frozen=True, slots=True)
class TrustDecisionView:
    """Duck-typed projection consumed by S06 ``TrustDecisionView`` Protocol.

    Field mapping (frozen contract with S06 eligibility.py):

    | View field         | Source |
    |--------------------|--------|
    | engram_id          | TrustDecision.engram_id / subject id |
    | content_digest     | TrustDecision.content_digest (bound) |
    | quarantined        | verification_status==quarantined OR decision==quarantine |
    | lifecycle_status   | durable engram.status (server FSM) |
    | origin_trusted     | server intake channel is locally authored OR admitted import |
    | signature_valid    | TrustDecision.signature_valid (None if N/A) |
    | admitted           | local admit decision still valid under current digest+policy |

    Imported signature alone never sets ``admitted=True``.
    """

    engram_id: str
    content_digest: str
    quarantined: bool
    lifecycle_status: str
    origin_trusted: bool
    signature_valid: bool | None
    admitted: bool


def new_decision_id() -> str:
    return f"td_{uuid.uuid4().hex[:12]}"


_ledger_lock = threading.Lock()


def _decision_mirror_path(cfg: Config, decision_id: str) -> Path:
    return trust_decisions_dir(cfg) / f"{decision_id}.json"


def _write_decision_mirror(cfg: Config, decision: TrustDecision) -> None:
    ensure_trust_dirs(cfg)
    path = _decision_mirror_path(cfg, decision.decision_id)
    tmp = path.with_name(path.name + ".tmp")
    content = json.dumps(decision.to_dict(), indent=2, sort_keys=True) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _upsert_decision_row(conn: sqlite3.Connection, decision: TrustDecision) -> None:
    """Best-effort DB cache. No-op if migration 004 tables are absent."""
    try:
        conn.execute(
            """
            INSERT INTO trust_decision (
              decision_id, engram_id, content_digest, resource_digest, decision,
              source_channel, signer_fingerprint, signature_valid,
              policy_id, policy_revision, policy_digest, scanner_revision,
              actor, timestamp, reasons_json, event_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(decision_id) DO UPDATE SET
              engram_id=excluded.engram_id,
              content_digest=excluded.content_digest,
              resource_digest=excluded.resource_digest,
              decision=excluded.decision,
              source_channel=excluded.source_channel,
              signer_fingerprint=excluded.signer_fingerprint,
              signature_valid=excluded.signature_valid,
              policy_id=excluded.policy_id,
              policy_revision=excluded.policy_revision,
              policy_digest=excluded.policy_digest,
              scanner_revision=excluded.scanner_revision,
              actor=excluded.actor,
              timestamp=excluded.timestamp,
              reasons_json=excluded.reasons_json,
              event_id=excluded.event_id
            """,
            (
                decision.decision_id,
                decision.engram_id,
                decision.content_digest,
                decision.resource_digest,
                decision.decision,
                decision.source_channel,
                decision.signer_fingerprint,
                (
                    None
                    if decision.signature_valid is None
                    else (1 if decision.signature_valid else 0)
                ),
                decision.policy_id,
                decision.policy_revision,
                decision.policy_digest,
                decision.scanner_revision,
                decision.actor,
                decision.timestamp,
                json.dumps(list(decision.reasons)),
                decision.event_id,
            ),
        )
    except sqlite3.OperationalError:
        # Table not yet migrated — file mirror remains authoritative.
        return


def persist_decision(
    cfg: Config,
    conn: sqlite3.Connection | None,
    decision: TrustDecision,
) -> TrustDecision:
    """File wins first (durable outside DB), then optional DB cache row."""
    with _ledger_lock:
        _write_decision_mirror(cfg, decision)
        if conn is not None:
            _upsert_decision_row(conn, decision)
    return decision


def list_decisions(cfg: Config) -> list[TrustDecision]:
    ensure_trust_dirs(cfg)
    out: list[TrustDecision] = []
    for path in sorted(trust_decisions_dir(cfg).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(TrustDecision.from_dict(data))
        except (OSError, json.JSONDecodeError, KeyError, InvalidInputError, TypeError):
            continue
    return out


def get_decision(cfg: Config, decision_id: str) -> TrustDecision | None:
    path = _decision_mirror_path(cfg, decision_id)
    if not path.is_file():
        return None
    return TrustDecision.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest_decision_for(cfg: Config, engram_id: str) -> TrustDecision | None:
    matches = [d for d in list_decisions(cfg) if d.engram_id == engram_id]
    if not matches:
        return None
    return max(matches, key=lambda d: (d.timestamp, d.decision_id))


def reload_from_mirror(cfg: Config, conn: sqlite3.Connection) -> int:
    """Repopulate ``trust_decision`` cache from JSON mirrors after DB rebuild."""
    count = 0
    for decision in list_decisions(cfg):
        _upsert_decision_row(conn, decision)
        count += 1
    return count


def _require_expected_digest(*, expected: str | None, actual: str, label: str) -> None:
    if expected is None:
        raise InvalidInputError(f"{label} requires expected_digest precondition")
    if expected != actual:
        raise InvalidInputError(
            f"{label} digest precondition failed",
            details={"expected": expected, "actual": actual},
        )


def record_pending_intake(
    cfg: Config,
    conn: sqlite3.Connection | None,
    *,
    engram_id: str,
    content_digest: str,
    source_channel: SourceChannel,
    actor: str,
    signature_valid: bool | None = None,
    signer_fingerprint: str | None = None,
    resource_digest: str | None = None,
    reasons: tuple[str, ...] = (),
    event_id: str | None = None,
) -> TrustDecision:
    """Mandatory pending/quarantine staging record for every external intake."""
    policy = load_policy(cfg)
    decision = TrustDecision(
        decision_id=new_decision_id(),
        engram_id=engram_id,
        content_digest=content_digest,
        decision="pending",
        source_channel=source_channel,
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor=actor,
        timestamp=_now(),
        reasons=reasons or ("awaiting local admission",),
        signer_fingerprint=signer_fingerprint,
        signature_valid=signature_valid,
        scanner_revision=policy.scanner_revision,
        resource_digest=resource_digest,
        event_id=event_id,
    )
    return persist_decision(cfg, conn, decision)


def approve(
    cfg: Config,
    conn: sqlite3.Connection | None,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
    source_channel: SourceChannel | None = None,
    signature_valid: bool | None = None,
    signer_fingerprint: str | None = None,
    resource_digest: str | None = None,
) -> TrustDecision:
    """Admit routing for exactly ``expected_digest`` under the current policy.

    Concurrent retries with the same ``event_id`` are idempotent: the first
    applied admit wins and subsequent calls return the same decision.
    """
    with _ledger_lock:
        if event_id:
            for existing in list_decisions(cfg):
                if existing.event_id == event_id and existing.decision == "admit":
                    return existing

        policy = load_policy(cfg)
        prior = latest_decision_for(cfg, engram_id)
        actual_digest = expected_digest
        if prior is not None and prior.content_digest != expected_digest:
            # Caller must pass the digest they reviewed; mismatch fails closed.
            raise InvalidInputError(
                "approve expected_digest does not match the staged content digest",
                details={"expected": expected_digest, "staged": prior.content_digest},
            )
        if prior is not None:
            _require_expected_digest(
                expected=expected_digest, actual=prior.content_digest, label="approve"
            )
            actual_digest = prior.content_digest
            if source_channel is None:
                source_channel = prior.source_channel
            if signature_valid is None:
                signature_valid = prior.signature_valid
            if signer_fingerprint is None:
                signer_fingerprint = prior.signer_fingerprint
            if resource_digest is None:
                resource_digest = prior.resource_digest

        decision = TrustDecision(
            decision_id=new_decision_id(),
            engram_id=engram_id,
            content_digest=actual_digest,
            decision="admit",
            source_channel=source_channel or "unknown",
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_digest=policy.digest(),
            actor=actor,
            timestamp=_now(),
            reasons=(reason,) if reason else ("local review admitted",),
            signer_fingerprint=signer_fingerprint,
            signature_valid=signature_valid,
            scanner_revision=policy.scanner_revision,
            resource_digest=resource_digest,
            event_id=event_id,
        )
        _write_decision_mirror(cfg, decision)
        if conn is not None:
            _upsert_decision_row(conn, decision)
        return decision


def reject(
    cfg: Config,
    conn: sqlite3.Connection | None,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> TrustDecision:
    policy = load_policy(cfg)
    prior = latest_decision_for(cfg, engram_id)
    if prior is not None:
        _require_expected_digest(expected=expected_digest, actual=prior.content_digest, label="reject")
    decision = TrustDecision(
        decision_id=new_decision_id(),
        engram_id=engram_id,
        content_digest=expected_digest,
        decision="reject",
        source_channel=prior.source_channel if prior else "unknown",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor=actor,
        timestamp=_now(),
        reasons=(reason,) if reason else ("local review rejected",),
        signer_fingerprint=prior.signer_fingerprint if prior else None,
        signature_valid=prior.signature_valid if prior else None,
        scanner_revision=policy.scanner_revision,
        resource_digest=prior.resource_digest if prior else None,
        event_id=event_id,
    )
    return persist_decision(cfg, conn, decision)


def revoke(
    cfg: Config,
    conn: sqlite3.Connection | None,
    *,
    engram_id: str,
    expected_digest: str | None = None,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> TrustDecision:
    """Revoke local admission. Keeps signature/audit history; does not delete mirrors."""
    policy = load_policy(cfg)
    prior = latest_decision_for(cfg, engram_id)
    if prior is None:
        raise NotFoundError(f"no trust decision for engram {engram_id!r}")
    if expected_digest is not None:
        _require_expected_digest(expected=expected_digest, actual=prior.content_digest, label="revoke")
    decision = TrustDecision(
        decision_id=new_decision_id(),
        engram_id=engram_id,
        content_digest=prior.content_digest,
        decision="revoke",
        source_channel=prior.source_channel,
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor=actor,
        timestamp=_now(),
        reasons=(reason,) if reason else ("local admission revoked",),
        signer_fingerprint=prior.signer_fingerprint,
        signature_valid=prior.signature_valid,
        scanner_revision=policy.scanner_revision,
        resource_digest=prior.resource_digest,
        event_id=event_id,
    )
    return persist_decision(cfg, conn, decision)


def admission_still_valid(
    cfg: Config,
    *,
    engram_id: str,
    content_digest: str,
) -> bool:
    """True iff the latest decision admits this digest under the *current* policy."""
    decision = latest_decision_for(cfg, engram_id)
    if decision is None or decision.decision != "admit":
        return False
    if decision.content_digest != content_digest:
        return False
    policy = load_policy(cfg)
    if decision.policy_digest != policy.digest() or decision.policy_revision != policy.revision:
        return False
    # Revoked signing key invalidates cached admission for signed imports.
    if decision.signer_fingerprint:
        for root in policy.roots:
            if root.fingerprint == decision.signer_fingerprint and root.revoked:
                return False
    return True


def origin_trusted_for_channel(channel: SourceChannel, *, admitted: bool) -> bool:
    """Server-owned origin trust. Imported channels are trusted only after admission."""
    if channel in ("local_authored", "local_register"):
        return True
    return admitted


def project_trust_view(
    cfg: Config,
    *,
    engram_id: str,
    content_digest: str,
    lifecycle_status: str,
    verification_status: str,
    intake_channel: SourceChannel,
) -> TrustDecisionView:
    """Build a TrustDecisionView-compatible object for S06 eligibility."""
    decision = latest_decision_for(cfg, engram_id)
    quarantined = verification_status == "quarantined" or (
        decision is not None and decision.decision == "quarantine"
    )
    admitted = admission_still_valid(cfg, engram_id=engram_id, content_digest=content_digest)
    channel = decision.source_channel if decision is not None else intake_channel
    sig: bool | None = decision.signature_valid if decision is not None else None
    return TrustDecisionView(
        engram_id=engram_id,
        content_digest=content_digest,
        quarantined=quarantined,
        lifecycle_status=lifecycle_status,
        origin_trusted=origin_trusted_for_channel(channel, admitted=admitted),
        signature_valid=sig,
        admitted=admitted,
    )


def classify_intake_channel(
    *,
    fmt: str | None = None,
    from_bundle: bool = False,
    path_inside_registry: bool = False,
) -> SourceChannel:
    """Server-assigned intake channel. Never taken from artifact declarations."""
    if from_bundle:
        return "bundle_import"
    if fmt == "skill":
        return "skillmd_import"
    if path_inside_registry:
        return "local_register"
    return "external_file"
