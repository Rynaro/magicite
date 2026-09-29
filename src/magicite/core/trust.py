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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from magicite.config import Config
from magicite.core.bundles import TrustRoot, public_key_fingerprint
from magicite.engram.digests import assets_manifest_digest, canonical_json_bytes, sha256_hex
from magicite.errors import InvalidInputError, NotFoundError
from magicite.storage import lease as lease_mod

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


class TrustLedgerCorruptError(InvalidInputError):
    """Corrupt or unreadable trust mirror — refuse until repaired (C10)."""


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


def _fsync_dir(path: Path) -> None:
    """Durability: fsync the parent directory after ``os.replace`` (finding 4)."""
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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
    with lease_mod.writer_lease(holder="trust-policy"):
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
        _fsync_dir(path)
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


def _decision_mirror_path(cfg: Config, decision_id: str) -> Path:
    return trust_decisions_dir(cfg) / f"{decision_id}.json"


def _load_decision_file(path: Path) -> TrustDecision:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TrustLedgerCorruptError(
                f"trust decision mirror is not an object: {path.name}",
                details={"path": str(path)},
            )
        return TrustDecision.from_dict(data)
    except TrustLedgerCorruptError:
        raise
    except InvalidInputError as exc:
        raise TrustLedgerCorruptError(
            f"trust decision mirror failed schema checks: {path.name}: {exc}",
            details={"path": str(path)},
        ) from exc
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TrustLedgerCorruptError(
            f"corrupt trust decision mirror: {path.name}: {exc}",
            details={"path": str(path)},
        ) from exc


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
    _fsync_dir(path)


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
    """File wins first (durable outside DB), then optional DB cache row.

    Acquires ``writer_lease`` (re-entrant) so off-path callers are safe and
    nested ``review_*`` callers do not deadlock (C0 / finding 5).
    """
    with lease_mod.writer_lease(holder="trust-persist"):
        _write_decision_mirror(cfg, decision)
        if conn is not None:
            _upsert_decision_row(conn, decision)
    return decision


def list_decisions(cfg: Config) -> list[TrustDecision]:
    """Load every decision mirror. Corrupt files fail closed (C10)."""
    ensure_trust_dirs(cfg)
    out: list[TrustDecision] = []
    for path in sorted(trust_decisions_dir(cfg).glob("*.json")):
        out.append(_load_decision_file(path))
    return out


def get_decision(cfg: Config, decision_id: str) -> TrustDecision | None:
    path = _decision_mirror_path(cfg, decision_id)
    if not path.is_file():
        return None
    return _load_decision_file(path)


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
            f"{label} digest precondition failed (stale_decision)",
            details={"expected": expected, "actual": actual, "reason": "stale_decision"},
        )


def live_content_digest(conn: sqlite3.Connection, engram_id: str) -> str:
    """Authoritative on-disk content digest from the durable engram row."""
    row = conn.execute(
        "SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()
    if row is None:
        raise InvalidInputError(
            f"no staged subject for engram {engram_id!r}",
            details={"engram_id": engram_id, "reason": "stale_decision"},
        )
    return str(row["content_sha256"])


def live_resource_digest(cfg: Config, conn: sqlite3.Connection, engram_id: str) -> str:
    """Live assets-manifest digest for the engram (empty map when no assets)."""
    row = conn.execute("SELECT path FROM engram WHERE id = ?", (engram_id,)).fetchone()
    if row is None:
        raise InvalidInputError(
            f"no staged subject for engram {engram_id!r}",
            details={"engram_id": engram_id, "reason": "stale_decision"},
        )
    return compute_resource_digest_at(cfg, relpath=str(row["path"]))


def compute_resource_digest_at(cfg: Config, *, relpath: str) -> str:
    """Compute ``assets_manifest_digest`` from live files under the registry."""
    from magicite.engram import parser as parser_mod
    from magicite.engram.assets import resolve_asset_path
    from magicite.engram.digests import asset_bytes_digest
    from magicite.engram.model_v1 import EngramV1

    full = (cfg.project_root / relpath).resolve()
    try:
        artifact, _doc = parser_mod.parse_artifact_file(
            full, registry_root=cfg.registry_dir, admit=False, require_asset_files=False
        )
    except Exception:
        # 0.2 / unreadable → empty resource binding (content digest still binds).
        return assets_manifest_digest({})
    if not isinstance(artifact, EngramV1) or not artifact.frontmatter.assets:
        return assets_manifest_digest({})

    live: dict[str, dict[str, Any]] = {}
    for rel, desc in artifact.frontmatter.assets.items():
        try:
            raw = resolve_asset_path(cfg.registry_dir, rel).read_bytes()
        except Exception:
            # Missing/escaped asset → digest that cannot match a prior admit.
            live[rel] = {
                "sha256": "0" * 64,
                "size": -1,
                "media_type": getattr(desc, "media_type", None),
            }
            continue
        live[rel] = {
            "sha256": asset_bytes_digest(raw),
            "size": len(raw),
            "media_type": getattr(desc, "media_type", None),
        }
    return assets_manifest_digest(live)


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
    conn: sqlite3.Connection,
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
    """Admit routing for exactly the **live** content digest under current policy.

    ``expected_digest`` must equal the durable row's ``content_sha256``. A
    missing subject or mismatch fails closed as ``stale_decision``. Concurrent
    retries with the same ``event_id`` are idempotent.
    """
    with lease_mod.writer_lease(holder="trust-approve"):
        if event_id:
            for existing in list_decisions(cfg):
                if existing.event_id == event_id and existing.decision == "admit":
                    return existing

        live = live_content_digest(conn, engram_id)
        _require_expected_digest(expected=expected_digest, actual=live, label="approve")

        live_resource = live_resource_digest(cfg, conn, engram_id)
        if resource_digest is not None and resource_digest != live_resource:
            raise InvalidInputError(
                "approve resource_digest does not match live assets (stale_decision)",
                details={
                    "expected": resource_digest,
                    "actual": live_resource,
                    "reason": "stale_decision",
                },
            )

        policy = load_policy(cfg)
        prior = latest_decision_for(cfg, engram_id)
        if prior is not None:
            if source_channel is None:
                source_channel = prior.source_channel
            if signature_valid is None:
                signature_valid = prior.signature_valid
            if signer_fingerprint is None:
                signer_fingerprint = prior.signer_fingerprint
            if resource_digest is None and prior.resource_digest is not None:
                # Prior staged resource must still match live assets.
                if prior.resource_digest != live_resource:
                    raise InvalidInputError(
                        "staged resource_digest no longer matches live assets (stale_decision)",
                        details={
                            "staged": prior.resource_digest,
                            "live": live_resource,
                            "reason": "stale_decision",
                        },
                    )

        bound_resource = resource_digest if resource_digest is not None else live_resource

        decision = TrustDecision(
            decision_id=new_decision_id(),
            engram_id=engram_id,
            content_digest=live,
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
            resource_digest=bound_resource,
            event_id=event_id,
        )
        _write_decision_mirror(cfg, decision)
        _upsert_decision_row(conn, decision)

        if not admission_still_valid(
            cfg,
            engram_id=engram_id,
            content_digest=live,
            resource_digest=bound_resource,
        ):
            raise InvalidInputError(
                "admission would not be valid after write (stale_decision)",
                details={"engram_id": engram_id, "reason": "stale_decision"},
            )
        return decision


def reject(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> TrustDecision:
    with lease_mod.writer_lease(holder="trust-reject"):
        live = live_content_digest(conn, engram_id)
        _require_expected_digest(expected=expected_digest, actual=live, label="reject")
        policy = load_policy(cfg)
        prior = latest_decision_for(cfg, engram_id)
        decision = TrustDecision(
            decision_id=new_decision_id(),
            engram_id=engram_id,
            content_digest=live,
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
            resource_digest=prior.resource_digest if prior else live_resource_digest(cfg, conn, engram_id),
            event_id=event_id,
        )
        _write_decision_mirror(cfg, decision)
        _upsert_decision_row(conn, decision)
        return decision


def revoke(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    expected_digest: str | None = None,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> TrustDecision:
    """Revoke local admission. Keeps signature/audit history; does not delete mirrors."""
    with lease_mod.writer_lease(holder="trust-revoke"):
        policy = load_policy(cfg)
        prior = latest_decision_for(cfg, engram_id)
        if prior is None:
            raise NotFoundError(f"no trust decision for engram {engram_id!r}")
        live = live_content_digest(conn, engram_id)
        if expected_digest is not None:
            _require_expected_digest(expected=expected_digest, actual=live, label="revoke")
        decision = TrustDecision(
            decision_id=new_decision_id(),
            engram_id=engram_id,
            content_digest=live,
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
        _write_decision_mirror(cfg, decision)
        _upsert_decision_row(conn, decision)
        return decision


def admission_still_valid(
    cfg: Config,
    *,
    engram_id: str,
    content_digest: str,
    resource_digest: str | None = None,
) -> bool:
    """True iff the latest decision admits these digests under the *current* policy.

    When ``resource_digest`` is omitted, only an empty assets-manifest binding
    (or an unbound decision) can remain valid — callers that bind real assets
    must pass the live resource digest (C10).
    """
    empty_resource = assets_manifest_digest({})
    try:
        decision = latest_decision_for(cfg, engram_id)
    except TrustLedgerCorruptError:
        return False
    if decision is None or decision.decision != "admit":
        return False
    if decision.content_digest != content_digest:
        return False
    if decision.resource_digest is not None:
        check = empty_resource if resource_digest is None else resource_digest
        if decision.resource_digest != check:
            return False
    try:
        policy = load_policy(cfg)
    except InvalidInputError:
        return False
    if decision.policy_digest != policy.digest() or decision.policy_revision != policy.revision:
        return False
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
    resource_digest: str | None = None,
) -> TrustDecisionView:
    """Build a TrustDecisionView-compatible object for S06 eligibility.

    Corrupt ledgers fail closed: ``admitted=False`` (finding 2).
    """
    try:
        decision = latest_decision_for(cfg, engram_id)
        admitted = admission_still_valid(
            cfg,
            engram_id=engram_id,
            content_digest=content_digest,
            resource_digest=resource_digest,
        )
    except TrustLedgerCorruptError:
        decision = None
        admitted = False
    quarantined = verification_status == "quarantined" or (
        decision is not None and decision.decision == "quarantine"
    )
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
