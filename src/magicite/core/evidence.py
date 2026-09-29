"""C6 evidence receipts, durable ledger, and privacy (S09).

Authoritative store: ``<data_dir>/evidence/`` (segmented append-only files).
SQLite tables from migration 006 are rebuildable projections only — a DB
rebuild must never erase acknowledged evidence (contracts.md C6 / C0).

Hot-path contract
-----------------
Routine ``route`` / search MUST NOT call :func:`checkpoint` (no background
durable persistence). S07 may call :func:`enqueue_decision_receipt` on the
retrieval plane; that buffers an ephemeral receipt only. Crash before
checkpoint loses unacknowledged receipts (documented RPO). Checkpoint
acknowledgement means durable commit under the writer lease; retries use
``event_id`` and produce zero duplicate effects.

Privacy
-------
Default: no raw prompt, procedure output, secret, or absolute project path
is persisted or exported. Query/context fingerprints use
:mod:`magicite.core.fingerprint_key` (S09-owned authority; path + scheme
``hmac-sha256/local-v1`` kept stable). Export uses fresh scoped pseudonyms.
Retention defaults: operational 30d, audit 90d. Deletion writes tombstones;
S12 restores must replay the privacy overlay before exposing evidence.

Fingerprint key lifecycle is owned here (adopted from S00 provisional
provider). Never log or export raw key bytes. Concurrent first-create races
are handled in ``fingerprint_key`` (O_EXCL today; atomic tmp+link publish
tracked on ``codex/v1-s00-key-race`` — keep that behavior if restructuring).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from magicite.config import Config
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.errors import IdempotencyKeyConflictError, InvalidInputError
from magicite.storage.lease import assert_single_writer, writer_lease

#: Schema / ledger identity.
LEDGER_KIND = "EvidenceLedger/1"
EVIDENCE_EVENT_KIND = "EvidenceEvent/1"
REDACTION_VERSION = 1
RECOVERY_OVERLAY_KIND = "RecoveryOverlay/1"

EVENT_TYPES = frozenset({"decision", "outcome", "policy_transition", "deletion"})
OUTCOMES = frozenset({"success", "failure", "unknown"})
VERIFIER_TYPES = frozenset(
    {
        "deterministic_test",
        "external_verified",
        "user_judged",
        "self_reported",
        "inferred",
    }
)
RETENTION_CLASSES = frozenset({"operational", "audit"})

DEFAULT_OPERATIONAL_RETENTION_DAYS = 30
DEFAULT_AUDIT_RETENTION_DAYS = 90
DEFAULT_RECEIPT_BUFFER_LIMIT = 256
DEFAULT_BACKUP_EXPIRY_DAYS = 90

#: Payload keys that must never appear in durable/export evidence.
RAW_QUERY_LEAK_KEYS = frozenset(
    {
        "query",
        "raw_query",
        "prompt",
        "raw_prompt",
        "procedure_output",
        "secret",
        "adapter_token",
        "absolute_path",
        "project_path",
    }
)

_SEGMENTS_DIRNAME = "segments"
_META_FILENAME = "meta.json"
_TOMBSTONES_FILENAME = "tombstones.jsonl"
_INDEX_FILENAME = "event_index.json"
_OPEN_SEGMENT_NAME = "open.events.jsonl"
_OPEN_MANIFEST_NAME = "open.manifest.json"

_buffer_lock = threading.Lock()
#: Bounded ephemeral decision receipts (process-local). Crash loses these.
# Populated after DecisionReceipt is defined; typed loosely here to avoid
# a forward-reference cycle at module import time.
_receipt_buffer: OrderedDict[str, Any] = OrderedDict()
_receipt_buffer_limit = DEFAULT_RECEIPT_BUFFER_LIMIT


def evidence_dir(cfg: Config) -> Path:
    """Authoritative evidence domain root (outside disposable retrieval DB)."""
    return cfg.data_dir / "evidence"


def evidence_domain_paths(cfg: Config) -> dict[str, Path]:
    """Paths S12 backup/restore must cover for the evidence domain."""
    root = evidence_dir(cfg)
    return {
        "evidence_root": root,
        "segments": root / _SEGMENTS_DIRNAME,
        "meta": root / _META_FILENAME,
        "tombstones": root / _TOMBSTONES_FILENAME,
        "event_index": root / _INDEX_FILENAME,
        "fingerprint_key": fingerprint_key_mod.fingerprint_key_path(cfg),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def new_event_id() -> str:
    return f"ev_{uuid.uuid4().hex}"


def new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class VerifierRef:
    type: str
    id: str
    version: str
    artifact_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "version": self.version,
        }
        if self.artifact_digest is not None:
            d["artifact_digest"] = self.artifact_digest
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerifierRef:
        return cls(
            type=str(data["type"]),
            id=str(data["id"]),
            version=str(data["version"]),
            artifact_digest=data.get("artifact_digest"),
        )


@dataclass(frozen=True)
class EvidenceEvent:
    """``EvidenceEvent/1`` durable record (no raw query/context text)."""

    event_id: str
    decision_id: str
    event_type: str
    recorded_at: str
    candidate_ids: tuple[str, ...] = ()
    candidate_revisions: tuple[str, ...] = ()
    candidate_scores: tuple[float, ...] = ()
    chosen_action: str | None = None
    behavior_policy_id: str | None = None
    behavior_policy_digest: str | None = None
    propensity: float | None = None
    registry_fingerprint: str | None = None
    config_fingerprint: str | None = None
    model_fingerprint: str | None = None
    context_fingerprint: str | None = None
    query_fingerprint: str | None = None
    fingerprint_scheme: str | None = fingerprint_key_mod.FINGERPRINT_SCHEME
    outcome: str | None = None
    verifier: VerifierRef | None = None
    source_tier: int | None = None
    redaction_version: int = REDACTION_VERSION
    retention_class: str = "operational"
    kind: str = EVIDENCE_EVENT_KIND
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "event_id": self.event_id,
            "decision_id": self.decision_id,
            "event_type": self.event_type,
            "recorded_at": self.recorded_at,
            "candidate_ids": list(self.candidate_ids),
            "candidate_revisions": list(self.candidate_revisions),
            "candidate_scores": list(self.candidate_scores),
            "chosen_action": self.chosen_action,
            "behavior_policy_id": self.behavior_policy_id,
            "behavior_policy_digest": self.behavior_policy_digest,
            "propensity": self.propensity,
            "registry_fingerprint": self.registry_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "query_fingerprint": self.query_fingerprint,
            "fingerprint_scheme": self.fingerprint_scheme,
            "outcome": self.outcome,
            "verifier": self.verifier.to_dict() if self.verifier else None,
            "source_tier": self.source_tier,
            "redaction_version": self.redaction_version,
            "retention_class": self.retention_class,
        }
        if self.extra:
            d["extra"] = self.extra
        return d

    def payload_for_digest(self) -> dict[str, Any]:
        """Immutable payload identity for event-id idempotence."""
        return {k: v for k, v in self.to_dict().items() if k != "recorded_at"}


@dataclass(frozen=True)
class DecisionReceipt:
    """Ephemeral bounded decision receipt (not durable until checkpoint)."""

    receipt_id: str
    decision_id: str
    query_fingerprint: str
    fingerprint_scheme: str
    candidate_ids: tuple[str, ...]
    policy_id: str
    policy_digest: str
    source_tier: int
    created_at: str
    propensity: float | None = None
    context_fingerprint: str | None = None
    registry_fingerprint: str | None = None
    model_fingerprint: str | None = None
    config_fingerprint: str | None = None
    candidate_scores: tuple[float, ...] = ()
    candidate_revisions: tuple[str, ...] = ()
    chosen_action: str | None = None


@dataclass(frozen=True)
class CheckpointAck:
    """Durable acknowledgement — only returned after fsynced commit."""

    event_id: str
    sequence: int
    segment_id: str
    payload_digest: str
    durable: Literal[True] = True
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SupportVerdict:
    """OPE / delayed-feedback support classification (C6 / C7 handoff to S10)."""

    status: Literal["supported", "insufficient_support", "unknown"]
    reason: str
    counterfactual_efficacy: Literal["unknown"] | None = "unknown"
    verifier_type: str | None = None
    source_tier: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SanitizationReport:
    scanned_rows: int
    redacted_rows: int
    keys_removed: tuple[str, ...]
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrivacyOverlay:
    """Restore-time privacy/revocation overlay (S12 consumes)."""

    kind: str
    registry_id: str
    control_sequence: int
    deletion_records: tuple[dict[str, Any], ...]
    policy_digest: str
    content_hashes: tuple[str, ...]
    operator_provenance: str
    fingerprint_scheme: str = fingerprint_key_mod.FINGERPRINT_SCHEME

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "registry_id": self.registry_id,
            "control_sequence": self.control_sequence,
            "deletion_records": list(self.deletion_records),
            "policy_digest": self.policy_digest,
            "content_hashes": list(self.content_hashes),
            "operator_provenance": self.operator_provenance,
            "fingerprint_scheme": self.fingerprint_scheme,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrivacyOverlay:
        return cls(
            kind=str(data.get("kind", RECOVERY_OVERLAY_KIND)),
            registry_id=str(data["registry_id"]),
            control_sequence=int(data["control_sequence"]),
            deletion_records=tuple(data.get("deletion_records") or ()),
            policy_digest=str(data["policy_digest"]),
            content_hashes=tuple(data.get("content_hashes") or ()),
            operator_provenance=str(data["operator_provenance"]),
            fingerprint_scheme=str(
                data.get("fingerprint_scheme", fingerprint_key_mod.FINGERPRINT_SCHEME)
            ),
        )


def validate_evidence_event(event: EvidenceEvent) -> None:
    """Fail closed on schema / taxonomy violations."""
    if event.kind != EVIDENCE_EVENT_KIND:
        raise InvalidInputError(f"unsupported evidence kind {event.kind!r}")
    if event.event_type not in EVENT_TYPES:
        raise InvalidInputError(f"unsupported event_type {event.event_type!r}")
    if event.retention_class not in RETENTION_CLASSES:
        raise InvalidInputError(f"unsupported retention_class {event.retention_class!r}")
    if event.outcome is not None and event.outcome not in OUTCOMES:
        raise InvalidInputError(f"unsupported outcome {event.outcome!r}")
    if event.verifier is not None and event.verifier.type not in VERIFIER_TYPES:
        raise InvalidInputError(f"unsupported verifier type {event.verifier.type!r}")
    _assert_no_raw_leak(event.to_dict())


def _assert_no_raw_leak(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in RAW_QUERY_LEAK_KEYS:
                raise InvalidInputError(
                    f"refusing to persist/export raw privacy field {key!r}",
                    hint="use query_fingerprint / context_fingerprint (C6)",
                    details={"path": path, "key": key},
                )
            _assert_no_raw_leak(item, path=f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_no_raw_leak(item, path=f"{path}[{idx}]")


def set_receipt_buffer_limit(limit: int) -> None:
    """Test/operator hook for the ephemeral receipt bound."""
    global _receipt_buffer_limit
    if limit < 1:
        raise InvalidInputError("receipt buffer limit must be >= 1")
    with _buffer_lock:
        _receipt_buffer_limit = limit
        while len(_receipt_buffer) > _receipt_buffer_limit:
            _receipt_buffer.popitem(last=False)


def clear_receipt_buffer() -> None:
    with _buffer_lock:
        _receipt_buffer.clear()


def enqueue_decision_receipt(
    cfg: Config,
    *,
    query: str | None = None,
    query_fingerprint: str | None = None,
    candidate_ids: list[str] | tuple[str, ...],
    policy_id: str,
    policy_digest: str,
    source_tier: int = 0,
    decision_id: str | None = None,
    chosen_action: str | None = None,
    propensity: float | None = None,
    candidate_scores: list[float] | tuple[float, ...] | None = None,
    candidate_revisions: list[str] | tuple[str, ...] | None = None,
    context_fingerprint: str | None = None,
    registry_fingerprint: str | None = None,
    model_fingerprint: str | None = None,
    config_fingerprint: str | None = None,
) -> DecisionReceipt:
    """Buffer an ephemeral decision receipt (S07 hot-path seam).

    Does **not** acquire the writer lease and does **not** touch the durable
    ledger. Pass either ``query`` (fingerprinted locally) or a precomputed
    ``query_fingerprint``. Raw ``query`` is never stored on the receipt.
    """
    if query_fingerprint is None:
        if query is None:
            raise InvalidInputError("enqueue_decision_receipt requires query or query_fingerprint")
        key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
        query_fingerprint = fingerprint_key_mod.query_fingerprint(query, key=key)
    scheme = fingerprint_key_mod.FINGERPRINT_SCHEME
    receipt = DecisionReceipt(
        receipt_id=f"rcpt_{uuid.uuid4().hex[:12]}",
        decision_id=decision_id or new_decision_id(),
        query_fingerprint=query_fingerprint,
        fingerprint_scheme=scheme,
        candidate_ids=tuple(candidate_ids),
        policy_id=policy_id,
        policy_digest=policy_digest,
        source_tier=source_tier,
        created_at=_now(),
        propensity=propensity,
        context_fingerprint=context_fingerprint,
        registry_fingerprint=registry_fingerprint,
        model_fingerprint=model_fingerprint,
        config_fingerprint=config_fingerprint,
        candidate_scores=tuple(candidate_scores or ()),
        candidate_revisions=tuple(candidate_revisions or ()),
        chosen_action=chosen_action or (candidate_ids[0] if candidate_ids else None),
    )
    with _buffer_lock:
        _receipt_buffer[receipt.receipt_id] = receipt
        while len(_receipt_buffer) > _receipt_buffer_limit:
            _receipt_buffer.popitem(last=False)
    return receipt


def get_receipt(receipt_id: str) -> DecisionReceipt | None:
    with _buffer_lock:
        return _receipt_buffer.get(receipt_id)


def list_pending_receipts() -> list[DecisionReceipt]:
    with _buffer_lock:
        return list(_receipt_buffer.values())


def receipt_to_event(receipt: DecisionReceipt, *, event_id: str | None = None) -> EvidenceEvent:
    return EvidenceEvent(
        event_id=event_id or new_event_id(),
        decision_id=receipt.decision_id,
        event_type="decision",
        recorded_at=_now(),
        candidate_ids=receipt.candidate_ids,
        candidate_revisions=receipt.candidate_revisions,
        candidate_scores=receipt.candidate_scores,
        chosen_action=receipt.chosen_action,
        behavior_policy_id=receipt.policy_id,
        behavior_policy_digest=receipt.policy_digest,
        propensity=receipt.propensity,
        registry_fingerprint=receipt.registry_fingerprint,
        config_fingerprint=receipt.config_fingerprint,
        model_fingerprint=receipt.model_fingerprint,
        context_fingerprint=receipt.context_fingerprint,
        query_fingerprint=receipt.query_fingerprint,
        fingerprint_scheme=receipt.fingerprint_scheme,
        source_tier=receipt.source_tier,
        retention_class="operational",
    )


def make_outcome_event(
    *,
    decision_id: str,
    outcome: str,
    verifier: VerifierRef,
    source_tier: int,
    event_id: str | None = None,
    behavior_policy_id: str | None = None,
    behavior_policy_digest: str | None = None,
    chosen_action: str | None = None,
    propensity: float | None = None,
    retention_class: str = "operational",
    query_fingerprint: str | None = None,
) -> EvidenceEvent:
    """Build an outcome event. Caller provenance is ``source_tier`` (server).

    A supplied ``verifier`` label never upgrades trust; see
    :func:`evaluate_support`.
    """
    if outcome not in OUTCOMES:
        raise InvalidInputError(f"unsupported outcome {outcome!r}")
    return EvidenceEvent(
        event_id=event_id or new_event_id(),
        decision_id=decision_id,
        event_type="outcome",
        recorded_at=_now(),
        chosen_action=chosen_action,
        behavior_policy_id=behavior_policy_id,
        behavior_policy_digest=behavior_policy_digest,
        propensity=propensity,
        query_fingerprint=query_fingerprint,
        outcome=outcome,
        verifier=verifier,
        source_tier=source_tier,
        retention_class=retention_class,
    )


def evaluate_support(
    *,
    behavior_policy_id: str | None,
    propensity: float | None,
    chosen_action: str | None,
    candidate_action: str | None,
    verifier: VerifierRef | None,
    outcome: str | None,
    delayed_feedback: bool = False,
    source_tier: int | None = None,
) -> SupportVerdict:
    """Classify whether evidence supports counterfactual / efficacy claims.

    AC-S09-04: deterministic selection (propensity in {0,1} or None with a
    fixed chosen action) cannot support counterfactual efficacy for a
    different action. Delayed self-reported feedback remains ``unknown``.
    Verifier type and source tier stay distinct axes.
    """
    vtype = verifier.type if verifier else None

    if delayed_feedback and vtype == "self_reported":
        return SupportVerdict(
            status="unknown",
            reason="delayed self-reported feedback cannot assert efficacy",
            counterfactual_efficacy="unknown",
            verifier_type=vtype,
            source_tier=source_tier,
        )

    if (
        candidate_action is not None
        and chosen_action is not None
        and candidate_action != chosen_action
    ):
        deterministic = propensity is None or propensity in (0.0, 1.0)
        if deterministic:
            return SupportVerdict(
                status="insufficient_support",
                reason="deterministic selection provides no support for counterfactual actions",
                counterfactual_efficacy="unknown",
                verifier_type=vtype,
                source_tier=source_tier,
            )
        if propensity is not None and propensity <= 0.0:
            return SupportVerdict(
                status="insufficient_support",
                reason="zero action propensity under behavior policy",
                counterfactual_efficacy="unknown",
                verifier_type=vtype,
                source_tier=source_tier,
            )

    if outcome == "unknown" or outcome is None:
        return SupportVerdict(
            status="unknown",
            reason="outcome remains unknown",
            counterfactual_efficacy="unknown",
            verifier_type=vtype,
            source_tier=source_tier,
        )

    if vtype in {"self_reported", "inferred"}:
        return SupportVerdict(
            status="unknown",
            reason=f"verifier type {vtype!r} is not pooled with verified labels",
            counterfactual_efficacy="unknown",
            verifier_type=vtype,
            source_tier=source_tier,
        )

    return SupportVerdict(
        status="supported",
        reason="verified outcome under behavior policy support",
        counterfactual_efficacy=None,
        verifier_type=vtype,
        source_tier=source_tier,
    )


# ── durable ledger I/O ──────────────────────────────────────────────────────


def _ensure_ledger_dirs(cfg: Config) -> Path:
    root = evidence_dir(cfg)
    (root / _SEGMENTS_DIRNAME).mkdir(parents=True, exist_ok=True)
    return root


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    raw = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_meta(root: Path) -> dict[str, Any]:
    return _read_json(
        root / _META_FILENAME,
        {
            "kind": LEDGER_KIND,
            "ledger_version": LEDGER_KIND,
            "last_sequence": 0,
            "open_segment_id": "00000001",
            "redaction_version": REDACTION_VERSION,
            "retention_operational_days": DEFAULT_OPERATIONAL_RETENTION_DAYS,
            "retention_audit_days": DEFAULT_AUDIT_RETENTION_DAYS,
            "backup_expiry_days": DEFAULT_BACKUP_EXPIRY_DAYS,
        },
    )


def _load_index(root: Path) -> dict[str, Any]:
    return _read_json(root / _INDEX_FILENAME, {"events": {}})


def _repair_torn_open_segment(segments: Path) -> None:
    """Truncate a torn final line on the open segment (C6)."""
    path = segments / _OPEN_SEGMENT_NAME
    if not path.is_file():
        return
    data = path.read_bytes()
    if not data:
        return
    if data.endswith(b"\n"):
        # Validate last complete line parses; if not, drop it.
        lines = data.split(b"\n")
        # trailing empty from final newline
        complete = [ln for ln in lines[:-1] if ln]
        if not complete:
            return
        try:
            json.loads(complete[-1])
            return
        except json.JSONDecodeError:
            complete = complete[:-1]
            rebuilt = b"\n".join(complete) + (b"\n" if complete else b"")
            tmp = path.with_name(path.name + ".repair.tmp")
            tmp.write_bytes(rebuilt)
            os.replace(tmp, path)
            return
    # No trailing newline → torn write; truncate to last complete line.
    last_nl = data.rfind(b"\n")
    repaired = data[: last_nl + 1] if last_nl >= 0 else b""
    tmp = path.with_name(path.name + ".repair.tmp")
    tmp.write_bytes(repaired)
    os.replace(tmp, path)


def _append_line_fsync(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        raw = (line if line.endswith("\n") else line + "\n").encode("utf-8")
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


def _event_record(
    event: EvidenceEvent,
    *,
    sequence: int,
    segment_id: str,
    digest: str,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "segment_id": segment_id,
        "payload_digest": digest,
        "event": event.to_dict(),
    }


def _project_upsert(
    conn: sqlite3.Connection,
    *,
    event: EvidenceEvent,
    sequence: int,
    segment_id: str,
    digest: str,
) -> None:
    assert_single_writer()
    conn.execute(
        """
        INSERT INTO evidence_event_projection (
          event_id, sequence, decision_id, event_type, payload_digest,
          recorded_at, retention_class, source_tier, outcome, segment_id, tombstoned
        ) VALUES (?,?,?,?,?,?,?,?,?,?,0)
        ON CONFLICT(event_id) DO UPDATE SET
          sequence=excluded.sequence,
          decision_id=excluded.decision_id,
          event_type=excluded.event_type,
          payload_digest=excluded.payload_digest,
          recorded_at=excluded.recorded_at,
          retention_class=excluded.retention_class,
          source_tier=excluded.source_tier,
          outcome=excluded.outcome,
          segment_id=excluded.segment_id
        """,
        (
            event.event_id,
            sequence,
            event.decision_id,
            event.event_type,
            digest,
            event.recorded_at,
            event.retention_class,
            event.source_tier,
            event.outcome,
            segment_id,
        ),
    )
    conn.execute(
        """
        UPDATE evidence_meta
        SET last_sequence = ?, last_segment_id = ?, redaction_version = ?, updated_at = ?
        WHERE id = 1
        """,
        (sequence, segment_id, event.redaction_version, _now()),
    )


def checkpoint(
    cfg: Config,
    conn: sqlite3.Connection,
    event: EvidenceEvent,
    *,
    holder: str = "evidence-checkpoint",
) -> CheckpointAck:
    """Lease-guarded durable checkpoint with event-id idempotence (AC-S09-01).

    Acknowledgement is returned only after the segment line and index are
    fsynced. Identical payload retries return the prior ack (``replayed``).
    Conflicting payloads for the same ``event_id`` raise
    :class:`IdempotencyKeyConflictError`.
    """
    validate_evidence_event(event)
    digest = payload_digest(event.payload_for_digest())

    with writer_lease(holder):
        root = _ensure_ledger_dirs(cfg)
        segments = root / _SEGMENTS_DIRNAME
        _repair_torn_open_segment(segments)

        index = _load_index(root)
        existing = index.get("events", {}).get(event.event_id)
        if existing is not None:
            if existing.get("payload_digest") != digest:
                raise IdempotencyKeyConflictError(
                    f"event_id {event.event_id!r} already committed with a different payload",
                    hint="retries must reuse the original immutable payload",
                    details={
                        "event_id": event.event_id,
                        "existing_digest": existing.get("payload_digest"),
                        "incoming_digest": digest,
                    },
                )
            return CheckpointAck(
                event_id=event.event_id,
                sequence=int(existing["sequence"]),
                segment_id=str(existing["segment_id"]),
                payload_digest=str(existing["payload_digest"]),
                replayed=True,
            )

        meta = _load_meta(root)
        sequence = int(meta.get("last_sequence", 0)) + 1
        segment_id = str(meta.get("open_segment_id", "00000001"))
        record = _event_record(event, sequence=sequence, segment_id=segment_id, digest=digest)
        line = _canonical_json(record)
        _append_line_fsync(segments / _OPEN_SEGMENT_NAME, line)

        # Update open manifest checksums (atomic replace).
        open_path = segments / _OPEN_SEGMENT_NAME
        file_digest = hashlib.sha256(open_path.read_bytes()).hexdigest()
        _atomic_write_json(
            segments / _OPEN_MANIFEST_NAME,
            {
                "segment_id": segment_id,
                "kind": LEDGER_KIND,
                "record_count": sequence,  # approximate within open segment
                "sha256": file_digest,
                "sealed": False,
                "updated_at": _now(),
            },
        )

        events = dict(index.get("events") or {})
        events[event.event_id] = {
            "sequence": sequence,
            "segment_id": segment_id,
            "payload_digest": digest,
            "decision_id": event.decision_id,
            "event_type": event.event_type,
        }
        _atomic_write_json(root / _INDEX_FILENAME, {"events": events})

        meta["last_sequence"] = sequence
        meta["open_segment_id"] = segment_id
        meta["updated_at"] = _now()
        _atomic_write_json(root / _META_FILENAME, meta)

        _project_upsert(
            conn, event=event, sequence=sequence, segment_id=segment_id, digest=digest
        )

        return CheckpointAck(
            event_id=event.event_id,
            sequence=sequence,
            segment_id=segment_id,
            payload_digest=digest,
            replayed=False,
        )


def checkpoint_receipt(
    cfg: Config,
    conn: sqlite3.Connection,
    receipt_id: str,
    *,
    event_id: str | None = None,
) -> CheckpointAck:
    """Promote a pending ephemeral receipt to a durable decision event."""
    receipt = get_receipt(receipt_id)
    if receipt is None:
        raise InvalidInputError(f"unknown receipt_id {receipt_id!r}")
    event = receipt_to_event(receipt, event_id=event_id)
    ack = checkpoint(cfg, conn, event)
    with _buffer_lock:
        _receipt_buffer.pop(receipt_id, None)
    return ack


def load_event(cfg: Config, event_id: str) -> EvidenceEvent | None:
    """Load an acknowledged event from the authoritative file ledger."""
    root = evidence_dir(cfg)
    index = _load_index(root)
    entry = index.get("events", {}).get(event_id)
    if entry is None:
        return None
    if _is_tombstoned(root, event_id):
        return None
    record = _find_record(root, event_id=event_id, sequence=int(entry["sequence"]))
    if record is None:
        return None
    return _event_from_dict(record["event"])


def _is_tombstoned(root: Path, event_id: str) -> bool:
    path = root / _TOMBSTONES_FILENAME
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("target_event_id") == event_id:
            return True
    return False


def _find_record(root: Path, *, event_id: str, sequence: int) -> dict[str, Any] | None:
    segments = root / _SEGMENTS_DIRNAME
    _repair_torn_open_segment(segments)
    candidates = sorted(segments.glob("*.events.jsonl"))
    for path in candidates:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(row.get("sequence", -1)) == sequence or row.get("event", {}).get("event_id") == event_id:
                return row
    return None


def _event_from_dict(data: dict[str, Any]) -> EvidenceEvent:
    verifier_raw = data.get("verifier")
    verifier = VerifierRef.from_dict(verifier_raw) if isinstance(verifier_raw, dict) else None
    return EvidenceEvent(
        event_id=str(data["event_id"]),
        decision_id=str(data["decision_id"]),
        event_type=str(data["event_type"]),
        recorded_at=str(data["recorded_at"]),
        candidate_ids=tuple(data.get("candidate_ids") or ()),
        candidate_revisions=tuple(data.get("candidate_revisions") or ()),
        candidate_scores=tuple(float(x) for x in (data.get("candidate_scores") or ())),
        chosen_action=data.get("chosen_action"),
        behavior_policy_id=data.get("behavior_policy_id"),
        behavior_policy_digest=data.get("behavior_policy_digest"),
        propensity=data.get("propensity"),
        registry_fingerprint=data.get("registry_fingerprint"),
        config_fingerprint=data.get("config_fingerprint"),
        model_fingerprint=data.get("model_fingerprint"),
        context_fingerprint=data.get("context_fingerprint"),
        query_fingerprint=data.get("query_fingerprint"),
        fingerprint_scheme=data.get("fingerprint_scheme"),
        outcome=data.get("outcome"),
        verifier=verifier,
        source_tier=data.get("source_tier"),
        redaction_version=int(data.get("redaction_version", REDACTION_VERSION)),
        retention_class=str(data.get("retention_class", "operational")),
        kind=str(data.get("kind", EVIDENCE_EVENT_KIND)),
        extra=dict(data.get("extra") or {}),
    )


def rebuild_projections(cfg: Config, conn: sqlite3.Connection) -> int:
    """Rebuild SQLite projections from the file ledger (AC-S09-02).

    Requires writer lease. Returns the number of projected (non-tombstoned)
    events. Does not mutate the authoritative file domain.
    """
    with writer_lease("evidence-rebuild"):
        root = _ensure_ledger_dirs(cfg)
        segments = root / _SEGMENTS_DIRNAME
        _repair_torn_open_segment(segments)
        tombstone_path = root / _TOMBSTONES_FILENAME
        tombstoned: set[str] = set()
        if tombstone_path.is_file():
            for line in tombstone_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    tombstoned.add(str(json.loads(line)["target_event_id"]))

        assert_single_writer()
        conn.execute("DELETE FROM evidence_event_projection")
        conn.execute("DELETE FROM evidence_tombstone_projection")
        count = 0
        last_seq = 0
        last_seg: str | None = None
        for path in sorted(segments.glob("*.events.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = _event_from_dict(row["event"])
                seq = int(row["sequence"])
                seg = str(row["segment_id"])
                digest = str(row["payload_digest"])
                last_seq = max(last_seq, seq)
                last_seg = seg
                if event.event_id in tombstoned:
                    continue
                _project_upsert(conn, event=event, sequence=seq, segment_id=seg, digest=digest)
                count += 1

        if (root / _TOMBSTONES_FILENAME).is_file():
            for line in (root / _TOMBSTONES_FILENAME).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO evidence_tombstone_projection
                      (tombstone_id, target_event_id, sequence, deleted_at, reason, actor)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        row["tombstone_id"],
                        row["target_event_id"],
                        int(row["sequence"]),
                        row["deleted_at"],
                        row.get("reason"),
                        row.get("actor"),
                    ),
                )
                conn.execute(
                    "UPDATE evidence_event_projection SET tombstoned = 1 WHERE event_id = ?",
                    (row["target_event_id"],),
                )

        conn.execute(
            """
            UPDATE evidence_meta
            SET last_sequence = ?, last_segment_id = ?, updated_at = ?
            WHERE id = 1
            """,
            (last_seq, last_seg, _now()),
        )
        return count


def delete_event(
    cfg: Config,
    conn: sqlite3.Connection,
    event_id: str,
    *,
    reason: str | None = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Append a privacy deletion tombstone and mark the projection."""
    with writer_lease("evidence-delete"):
        root = _ensure_ledger_dirs(cfg)
        index = _load_index(root)
        entry = index.get("events", {}).get(event_id)
        if entry is None:
            raise InvalidInputError(f"unknown event_id {event_id!r}")
        meta = _load_meta(root)
        sequence = int(meta.get("last_sequence", 0)) + 1
        tombstone = {
            "tombstone_id": f"tomb_{uuid.uuid4().hex[:12]}",
            "target_event_id": event_id,
            "sequence": sequence,
            "deleted_at": _now(),
            "reason": reason,
            "actor": actor,
        }
        _append_line_fsync(root / _TOMBSTONES_FILENAME, _canonical_json(tombstone))
        meta["last_sequence"] = sequence
        meta["updated_at"] = _now()
        _atomic_write_json(root / _META_FILENAME, meta)
        assert_single_writer()
        conn.execute(
            """
            INSERT OR REPLACE INTO evidence_tombstone_projection
              (tombstone_id, target_event_id, sequence, deleted_at, reason, actor)
            VALUES (?,?,?,?,?,?)
            """,
            (
                tombstone["tombstone_id"],
                event_id,
                sequence,
                tombstone["deleted_at"],
                reason,
                actor,
            ),
        )
        conn.execute(
            "UPDATE evidence_event_projection SET tombstoned = 1 WHERE event_id = ?",
            (event_id,),
        )
        return tombstone


def export_evidence(
    cfg: Config,
    *,
    event_ids: list[str] | None = None,
    export_dir: Path | None = None,
) -> Path:
    """Export acknowledged events with fresh scoped pseudonyms (C6).

    Never includes raw queries, fingerprint key bytes, or tombstoned rows.
    """
    root = evidence_dir(cfg)
    index = _load_index(root)
    selected = event_ids or list(index.get("events", {}).keys())
    pseudonym_scope = uuid.uuid4().hex
    out_dir = export_dir or (root / "exports" / f"export_{pseudonym_scope[:12]}")
    out_dir.mkdir(parents=True, exist_ok=True)

    id_map: dict[str, str] = {}
    events_out: list[dict[str, Any]] = []
    for event_id in selected:
        if _is_tombstoned(root, event_id):
            continue
        event = load_event(cfg, event_id)
        if event is None:
            continue
        payload = event.to_dict()
        _assert_no_raw_leak(payload)
        # Fresh scoped pseudonyms for correlatable ids.
        for field_name in ("event_id", "decision_id"):
            original = str(payload[field_name])
            if original not in id_map:
                digest = hashlib.sha256((pseudonym_scope + original).encode()).hexdigest()
                id_map[original] = f"pseudo_{digest[:16]}"
            payload[field_name] = id_map[original]
        events_out.append(payload)

    manifest = {
        "kind": "EvidenceExport/1",
        "pseudonym_scope": pseudonym_scope,
        "fingerprint_scheme": fingerprint_key_mod.FINGERPRINT_SCHEME,
        "redaction_version": REDACTION_VERSION,
        "exported_at": _now(),
        "event_count": len(events_out),
        # Explicitly document that key material is never exported.
        "fingerprint_key_exported": False,
    }
    _assert_no_raw_leak(manifest)
    _atomic_write_json(out_dir / "manifest.json", manifest)
    export_path = out_dir / "events.jsonl"
    with export_path.open("w", encoding="utf-8") as fh:
        for row in events_out:
            fh.write(_canonical_json(row) + "\n")
    return out_dir


def apply_retention(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Tombstone expired operational/audit records per retention policy."""
    root = evidence_dir(cfg)
    meta = _load_meta(root)
    now_dt = now or datetime.now(UTC)
    op_days = int(meta.get("retention_operational_days", DEFAULT_OPERATIONAL_RETENTION_DAYS))
    audit_days = int(meta.get("retention_audit_days", DEFAULT_AUDIT_RETENTION_DAYS))
    deleted: list[str] = []
    index = _load_index(root)
    for event_id, _entry in list(index.get("events", {}).items()):
        if _is_tombstoned(root, event_id):
            continue
        event = load_event(cfg, event_id)
        if event is None:
            continue
        recorded = datetime.fromisoformat(event.recorded_at)
        limit_days = audit_days if event.retention_class == "audit" else op_days
        if recorded + timedelta(days=limit_days) <= now_dt:
            delete_event(cfg, conn, event_id, reason="retention_expiry", actor="retention")
            deleted.append(event_id)
    return deleted


def backup_expiry_days(cfg: Config) -> int:
    meta = _load_meta(evidence_dir(cfg)) if evidence_dir(cfg).exists() else {}
    return int(meta.get("backup_expiry_days", DEFAULT_BACKUP_EXPIRY_DAYS))


def build_privacy_overlay(
    cfg: Config,
    *,
    registry_id: str,
    control_sequence: int,
    policy_digest: str,
    operator_provenance: str,
) -> PrivacyOverlay:
    """Snapshot deletion tombstones for S12 restore-time overlay."""
    root = evidence_dir(cfg)
    records: list[dict[str, Any]] = []
    path = root / _TOMBSTONES_FILENAME
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    content_hashes = tuple(
        sorted(
            hashlib.sha256(_canonical_json(r).encode()).hexdigest() for r in records
        )
    )
    return PrivacyOverlay(
        kind=RECOVERY_OVERLAY_KIND,
        registry_id=registry_id,
        control_sequence=control_sequence,
        deletion_records=tuple(records),
        policy_digest=policy_digest,
        content_hashes=content_hashes,
        operator_provenance=operator_provenance,
    )


def apply_privacy_overlay(
    cfg: Config,
    conn: sqlite3.Connection,
    overlay: PrivacyOverlay,
    *,
    expected_registry_id: str | None = None,
    minimum_sequence: int | None = None,
) -> dict[str, Any]:
    """Replay deletion tombstones after restore (S12 hook).

    Rejects registry mismatch or stale sequence. Does not reactivate
    deleted personal data from backups.
    """
    if overlay.kind != RECOVERY_OVERLAY_KIND:
        raise InvalidInputError(f"unsupported overlay kind {overlay.kind!r}")
    if expected_registry_id is not None and overlay.registry_id != expected_registry_id:
        raise InvalidInputError(
            "privacy overlay registry_id mismatch",
            details={
                "expected": expected_registry_id,
                "got": overlay.registry_id,
            },
        )
    if minimum_sequence is not None and overlay.control_sequence < minimum_sequence:
        raise InvalidInputError(
            "privacy overlay sequence is stale",
            details={
                "minimum_sequence": minimum_sequence,
                "got": overlay.control_sequence,
            },
        )
    applied = 0
    for record in overlay.deletion_records:
        event_id = str(record["target_event_id"])
        if _is_tombstoned(evidence_dir(cfg), event_id):
            continue
        # Only tombstone if the event is known in the restored ledger.
        index = _load_index(evidence_dir(cfg))
        if event_id not in index.get("events", {}):
            # Still record tombstone so restore cannot reactivate later injects.
            with writer_lease("evidence-overlay"):
                root = _ensure_ledger_dirs(cfg)
                meta = _load_meta(root)
                sequence = int(meta.get("last_sequence", 0)) + 1
                tombstone = {
                    "tombstone_id": record.get("tombstone_id") or f"tomb_{uuid.uuid4().hex[:12]}",
                    "target_event_id": event_id,
                    "sequence": sequence,
                    "deleted_at": record.get("deleted_at") or _now(),
                    "reason": record.get("reason") or "overlay_replay",
                    "actor": record.get("actor") or "privacy_overlay",
                }
                _append_line_fsync(root / _TOMBSTONES_FILENAME, _canonical_json(tombstone))
                meta["last_sequence"] = sequence
                _atomic_write_json(root / _META_FILENAME, meta)
            applied += 1
            continue
        delete_event(
            cfg,
            conn,
            event_id,
            reason=str(record.get("reason") or "overlay_replay"),
            actor=str(record.get("actor") or "privacy_overlay"),
        )
        applied += 1
    return {
        "applied": applied,
        "control_sequence": overlay.control_sequence,
        "status": "ok",
    }


_ABS_PATH_RE = re.compile(r"(^|[\s\"'])(/[\w.-]+(?:/[\w.-]+)+)")


def sanitize_ephemeral_history(conn: sqlite3.Connection) -> SanitizationReport:
    """Redact raw-query / secret fields from historical ``eph_event`` rows.

    Used on upgrade (AC-S09-03). Returns an explicit sanitization report.
    """
    rows = conn.execute("SELECT id, payload_json FROM eph_event").fetchall()
    redacted = 0
    keys_removed: set[str] = set()
    details: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        new_payload, removed = _redact_mapping(payload)
        if removed:
            keys_removed.update(removed)
            conn.execute(
                "UPDATE eph_event SET payload_json = ? WHERE id = ?",
                (json.dumps(new_payload, sort_keys=True, default=str), row["id"]),
            )
            redacted += 1
            details.append(f"eph_event.id={row['id']}: removed {sorted(removed)}")
    return SanitizationReport(
        scanned_rows=len(rows),
        redacted_rows=redacted,
        keys_removed=tuple(sorted(keys_removed)),
        details=tuple(details),
    )


def _redact_mapping(value: Any) -> tuple[Any, set[str]]:
    removed: set[str] = set()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in RAW_QUERY_LEAK_KEYS:
                removed.add(str(key))
                continue
            child, child_removed = _redact_mapping(item)
            removed |= child_removed
            if isinstance(child, str):
                child = _ABS_PATH_RE.sub(r"\1<redacted_path>", child)
            out[key] = child
        return out, removed
    if isinstance(value, list):
        items = []
        for item in value:
            child, child_removed = _redact_mapping(item)
            removed |= child_removed
            items.append(child)
        return items, removed
    if isinstance(value, str):
        return _ABS_PATH_RE.sub(r"\1<redacted_path>", value), removed
    return value, removed


def inventory_raw_query_leakage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Scan durable projections + eph_event for residual raw-query keys."""
    findings: list[dict[str, Any]] = []
    for row in conn.execute("SELECT id, tool, payload_json FROM eph_event").fetchall():
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        leaks = _find_leak_keys(payload)
        if leaks:
            findings.append(
                {
                    "store": "eph_event",
                    "id": row["id"],
                    "tool": row["tool"],
                    "keys": sorted(leaks),
                }
            )
    return findings


def _find_leak_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in RAW_QUERY_LEAK_KEYS:
                found.add(str(key))
            found |= _find_leak_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _find_leak_keys(item)
    return found


def privacy_gate_evidence(cfg: Config) -> dict[str, Any]:
    """Data-map fragment for release gate PRIVACY (S16 consumes)."""
    paths = {k: str(v) for k, v in evidence_domain_paths(cfg).items()}
    return {
        "gate": "PRIVACY",
        "owner": "S09",
        "ledger_kind": LEDGER_KIND,
        "authoritative_paths": paths,
        "default_raw_capture": False,
        "fingerprint_scheme": fingerprint_key_mod.FINGERPRINT_SCHEME,
        "fingerprint_key_exported": False,
        "retention_operational_days": DEFAULT_OPERATIONAL_RETENTION_DAYS,
        "retention_audit_days": DEFAULT_AUDIT_RETENTION_DAYS,
        "backup_expiry_days": backup_expiry_days(cfg),
        "checkpoint_rpo": "unacknowledged_ephemeral_receipts_may_be_lost",
        "redaction_version": REDACTION_VERSION,
    }


def rotate_fingerprint_key(cfg: Config, *, new_key: bytes | None = None) -> Path:
    """Rotate the local HMAC key (S09 key lifecycle).

    Writes a new key file with mode 0600. Does not rewrite historical
    fingerprints (they remain under the prior key epoch). Never returns
    key bytes.
    """
    path = fingerprint_key_mod.fingerprint_key_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = new_key if new_key is not None else os.urandom(fingerprint_key_mod.KEY_BYTES)
    if len(key) != fingerprint_key_mod.KEY_BYTES:
        raise InvalidInputError(f"fingerprint key must be {fingerprint_key_mod.KEY_BYTES} bytes")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
