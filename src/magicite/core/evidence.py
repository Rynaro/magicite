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
``hmac-sha256/local-v1`` kept stable). Export uses fresh scoped
pseudonyms and re-HMACs every correlator under a per-export random key
that is never persisted. Retention defaults: operational 30d, audit 90d.
Deletion physically erases payloads from ledger segments and keeps a
minimal tombstone (event id, deletion time, reason) so audit reconciles.
Segment stubs and tombstones intentionally retain ``event_id`` /
``target_event_id`` as the erasure residual for audit reconciliation —
they MUST NOT carry raw query, context text, secrets, or absolute paths.

Managed export artifacts live under ``evidence/exports/``. Additional write
roots may be declared via ``Config.evidence_export_roots`` /
``MAGICITE_EVIDENCE_EXPORT_ROOTS`` (``os.pathsep``-separated). Roots that
overlap the data dir / evidence ledger / control trees are refused; only
``evidence/exports/`` is allowed inside the data dir. Destinations outside
allowed roots are refused. Registered export files (exact paths +
sha256/size/inode) are HMAC-authenticated with a domain-separated subkey
of the local fingerprint key so privacy deletion cannot follow a poisoned
registry. Unregistered operator copies remain out of scope (AC-S09-05);
every export manifest carries a notice that deletion cannot follow such
copies (AC-S09-06).

Backup handling (C6): backups are documented separately. Backup expiry
and restore-time tombstone replay belong to S12 — operators must replay
the privacy overlay before exposing restored evidence; do not reactivate
deleted personal data from backups without that replay.

Fingerprint key lifecycle is owned here (adopted from S00 provisional
provider). Never log or export raw key bytes. Concurrent first-create
races are handled in ``fingerprint_key`` (fixed upstream; do not
reintroduce a second key-publish path here).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import stat
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from magicite.config import Config
from magicite.core import fingerprint_key as fingerprint_key_mod
from magicite.errors import IdempotencyKeyConflictError, InvalidInputError
from magicite.storage import lease as lease_mod
from magicite.storage.lease import assert_single_writer, writer_lease

logger = logging.getLogger(__name__)

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
#: Seal the open segment and open a new one once this size is exceeded.
DEFAULT_SEGMENT_MAX_BYTES = 1_048_576

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
_REGISTERED_EXPORTS_FILENAME = "registered_exports.json"
_PURGE_COMPLETE_FILENAME = "purge_complete.marker"
_TOMBSTONE_MAC_FILENAME = "tombstones.mac"
_REGISTRY_MAC_LABEL = b"magicite/export-registry/v1"
_REGISTRY_MAC_SCHEME = "hmac-sha256/export-registry-v1"
_REGISTRY_MAC_VERSION_PREFIX = b"v1|"
_PURGE_MARKER_MAC_LABEL = b"magicite/evidence-purge-marker/v1"
_PURGE_MARKER_MAC_SCHEME = "hmac-sha256/evidence-purge-marker-v1"
_TOMBSTONE_MAC_LABEL = b"magicite/evidence-tombstones/v1"
_TOMBSTONE_MAC_SCHEME = "hmac-sha256/evidence-tombstones-v1"

EXPORT_COPY_DELETION_NOTICE = (
    "Privacy deletion covers the managed evidence/exports directory and any "
    "paths registered in registered_exports.json. Operator copies outside "
    "those locations cannot be followed or purged automatically."
)

_buffer_lock = threading.Lock()
#: Bounded ephemeral decision receipts (process-local). Crash loses these.
# Populated after DecisionReceipt is defined; typed loosely here to avoid
# a forward-reference cycle at module import time.
_receipt_buffer: OrderedDict[str, Any] = OrderedDict()
_receipt_buffer_limit = DEFAULT_RECEIPT_BUFFER_LIMIT
_enqueue_missing_fingerprint = 0
_enqueue_drop_count = 0
_checkpoint_fault_hook: Callable[[str], None] | None = None


def set_checkpoint_fault_hook(hook: Callable[[str], None] | None) -> None:
    """Test-only crash-injection hook.

    Labels include: after_segment, after_index, before_meta, after_tombstone,
    after_purge_sealed, mid_rotation_after_seal.
    """
    global _checkpoint_fault_hook
    _checkpoint_fault_hook = hook


def _maybe_fault(label: str) -> None:
    if _checkpoint_fault_hook is not None:
        _checkpoint_fault_hook(label)


def reset_enqueue_counters() -> None:
    global _enqueue_missing_fingerprint, _enqueue_drop_count
    _enqueue_missing_fingerprint = 0
    _enqueue_drop_count = 0


def reset_enqueue_drop_count() -> None:
    """Alias used by atlas unit fixtures."""
    reset_enqueue_counters()


def enqueue_missing_fingerprint_count() -> int:
    return _enqueue_missing_fingerprint


def enqueue_drop_count() -> int:
    return _enqueue_drop_count


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
    candidate_ids: tuple[str, ...]
    policy_id: str
    policy_digest: str
    source_tier: int
    created_at: str
    query_fingerprint: str | None = None
    fingerprint_scheme: str | None = None
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
    candidate_ids: list[str] | tuple[str, ...] | None = None,
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
) -> DecisionReceipt | None:
    """Buffer an ephemeral decision receipt (S07 hot-path seam).

    Pure in-memory: no writer lease, no durable I/O, no fingerprint-key disk
    access. Pass a precomputed ``query_fingerprint``. Missing fingerprints
    become ``None`` (counter incremented). Raw ``query`` is never stored.
    On internal failure the receipt is dropped/logged and ``None`` is
    returned — this function does not raise.
    """
    global _enqueue_missing_fingerprint, _enqueue_drop_count
    _ = (cfg, query)
    try:
        fp = query_fingerprint
        scheme: str | None = fingerprint_key_mod.FINGERPRINT_SCHEME if fp else None
        if fp is None:
            _enqueue_missing_fingerprint += 1
        ids = tuple(candidate_ids or ())
        receipt = DecisionReceipt(
            receipt_id=f"rcpt_{uuid.uuid4().hex[:12]}",
            decision_id=decision_id or new_decision_id(),
            query_fingerprint=fp,
            fingerprint_scheme=scheme,
            candidate_ids=ids,
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
            chosen_action=chosen_action or (ids[0] if ids else None),
        )
        with _buffer_lock:
            _receipt_buffer[receipt.receipt_id] = receipt
            while len(_receipt_buffer) > _receipt_buffer_limit:
                _receipt_buffer.popitem(last=False)
        return receipt
    except Exception:
        _enqueue_drop_count += 1
        logger.exception("enqueue_decision_receipt dropped receipt on internal failure")
        return None


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


def _fsync_dir(dir_path: Path) -> None:
    dir_fd = os.open(str(dir_path), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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
    _fsync_dir(path.parent)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _default_meta() -> dict[str, Any]:
    return {
        "kind": LEDGER_KIND,
        "ledger_version": LEDGER_KIND,
        "last_sequence": 0,
        "open_segment_id": "00000001",
        "redaction_version": REDACTION_VERSION,
        # Retention / backup expiry live on Config (C6); meta must not carry them.
        # Backups: restore MUST replay tombstones via apply_privacy_overlay (S12).
        "backup_restore_requires_privacy_overlay": True,
    }


def _load_meta(root: Path) -> dict[str, Any]:
    return _read_json(root / _META_FILENAME, _default_meta())


def _load_index(root: Path) -> dict[str, Any]:
    return _read_json(root / _INDEX_FILENAME, {"events": {}})


def _repair_torn_open_segment(segments: Path) -> None:
    """Truncate only a torn trailing partial line; never drop complete lines."""
    path = segments / _OPEN_SEGMENT_NAME
    if not path.is_file():
        return
    data = path.read_bytes()
    if not data:
        return
    changed = False
    if data.endswith(b"\n"):
        lines = data.split(b"\n")
        complete = [ln for ln in lines[:-1] if ln]
        if not complete:
            return
        try:
            json.loads(complete[-1])
            return
        except json.JSONDecodeError:
            complete = complete[:-1]
            rebuilt = b"\n".join(complete) + (b"\n" if complete else b"")
            _atomic_write_bytes(path, rebuilt)
            changed = True
    else:
        last_nl = data.rfind(b"\n")
        repaired = data[: last_nl + 1] if last_nl >= 0 else b""
        _atomic_write_bytes(path, repaired)
        changed = True
    if changed:
        _rewrite_segment_manifest(path, sealed=False)


def _append_line_fsync(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        raw = (line if line.endswith("\n") else line + "\n").encode("utf-8")
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)


def _iter_segment_records(root: Path) -> list[dict[str, Any]]:
    """Parse segment records. Sealed segments fail closed on any corrupt line.

    OPEN segment: only a torn trailing line may be skipped (after repair);
    any corrupt non-trailing line fails closed. Sealed segment bytes must
    also match their sibling manifest sha256 when a manifest exists.
    """
    segments = root / _SEGMENTS_DIRNAME
    _repair_torn_open_segment(segments)
    out: list[dict[str, Any]] = []
    for path in sorted(segments.glob("*.events.jsonl")):
        if not path.is_file():
            continue
        sealed = path.name != _OPEN_SEGMENT_NAME
        # Sealed: refuse to trust if manifest digest mismatches file bytes.
        if sealed:
            _assert_sealed_segment_manifest(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                is_trailing = idx == len(lines) - 1
                if sealed or not is_trailing:
                    raise InvalidInputError(
                        "corrupt evidence segment record; refusing to trust ledger",
                        details={
                            "segment": path.name,
                            "line_index": idx,
                            "sealed": sealed,
                            "error": str(exc),
                        },
                    ) from exc
                # Open segment: trailing torn line already handled by repair;
                # if still present, skip only this trailing line.
                continue
            if not isinstance(row, dict):
                raise InvalidInputError(
                    "corrupt evidence segment record; non-object JSON",
                    details={"segment": path.name, "line_index": idx},
                )
            out.append(row)
    return out


def _assert_sealed_segment_manifest(segment_path: Path) -> None:
    manifest_path = _manifest_path_for_segment(segment_path)
    if not manifest_path.is_file():
        raise InvalidInputError(
            "sealed segment is missing its manifest; refusing to trust ledger",
            details={"segment": segment_path.name},
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidInputError(
            "sealed segment manifest is corrupt",
            details={"segment": segment_path.name, "error": str(exc)},
        ) from exc
    data = segment_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = str(manifest.get("sha256") or "")
    if not expected or not hmac.compare_digest(expected, actual):
        raise InvalidInputError(
            "sealed segment manifest sha256 mismatch; refusing to trust ledger",
            details={"segment": segment_path.name},
        )


def _record_event_id(row: dict[str, Any]) -> str | None:
    if row.get("deleted"):
        return str(row.get("event_id")) if row.get("event_id") else None
    event = row.get("event")
    if isinstance(event, dict) and event.get("event_id"):
        return str(event["event_id"])
    return None


def _scan_segment_authority(root: Path) -> tuple[dict[str, dict[str, Any]], int]:
    """Segment is source of truth. Returns (event_id -> row, max_seq).

    A deleted stub for an event_id always wins over live rows regardless of
    sequence order (crash-safe mid-delete / mid-rotation).
    """
    by_id: dict[str, dict[str, Any]] = {}
    max_seq = 0
    for row in _iter_segment_records(root):
        seq = int(row.get("sequence", 0))
        max_seq = max(max_seq, seq)
        eid = _record_event_id(row)
        if eid is None:
            continue
        prev = by_id.get(eid)
        if prev is None:
            by_id[eid] = row
            continue
        # Deleted stub always wins over any live payload row.
        if row.get("deleted"):
            by_id[eid] = row
            continue
        if prev.get("deleted"):
            continue
        prev_digest = prev.get("payload_digest")
        cur_digest = row.get("payload_digest")
        if prev_digest and cur_digest and prev_digest != cur_digest:
            raise IdempotencyKeyConflictError(
                f"segment contains conflicting payloads for event_id {eid!r}",
                details={"event_id": eid, "digests": [prev_digest, cur_digest]},
            )
        # Same digest duplicates (e.g. mid-rotation re-copy): keep either.
        if seq >= int(prev.get("sequence", 0)):
            by_id[eid] = row
    return by_id, max_seq


def _derive_index_and_meta_from_segment(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id, max_seq = _scan_segment_authority(root)
    events: dict[str, Any] = {}
    for eid, row in by_id.items():
        if row.get("deleted"):
            continue
        event = row.get("event") or {}
        events[eid] = {
            "sequence": int(row["sequence"]),
            "segment_id": str(row.get("segment_id", "00000001")),
            "payload_digest": str(row.get("payload_digest") or ""),
            "decision_id": event.get("decision_id"),
            "event_type": event.get("event_type"),
        }
    meta = _load_meta(root)
    meta["last_sequence"] = max_seq
    meta["open_segment_id"] = str(meta.get("open_segment_id", "00000001"))
    meta["updated_at"] = _now()
    meta.setdefault("kind", LEDGER_KIND)
    meta.setdefault("ledger_version", LEDGER_KIND)
    meta.setdefault("backup_restore_requires_privacy_overlay", True)
    return {"events": events}, meta


def _rewrite_derived_caches(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    index, meta = _derive_index_and_meta_from_segment(root)
    _atomic_write_json(root / _INDEX_FILENAME, index)
    _atomic_write_json(root / _META_FILENAME, meta)
    return index, meta


def _reconcile_authority(root: Path) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Repair segment; rewrite derived caches when they lag the segment.

    Fresh empty ledgers do not create empty index/meta files here — the first
    successful append publishes them. That keeps segment-before-index crash
    recovery well-defined (C6).
    """
    _by_id, seg_max = _scan_segment_authority(root)
    index_exists = (root / _INDEX_FILENAME).is_file()
    meta_exists = (root / _META_FILENAME).is_file()
    index = _load_index(root) if index_exists else {"events": {}}
    meta = _load_meta(root) if meta_exists else _default_meta()
    index_max = 0
    for entry in (index.get("events") or {}).values():
        index_max = max(index_max, int(entry.get("sequence", 0)))
    meta_max = int(meta.get("last_sequence", 0))
    authority_max = max(seg_max, index_max, meta_max)

    if seg_max == 0 and not index_exists:
        # Nothing durable yet — leave caches absent until first append.
        return {"events": {}}, meta, authority_max

    derived_index, derived_meta = _derive_index_and_meta_from_segment(root)
    need_rewrite = (
        not index_exists
        or not meta_exists
        or derived_index.get("events") != (index.get("events") or {})
        or int(meta.get("last_sequence", -1)) != int(derived_meta.get("last_sequence", -2))
        or meta_max < seg_max
        or index_max < seg_max
    )
    if need_rewrite:
        for key in (
            "backup_restore_requires_privacy_overlay",
            "redaction_version",
            "open_segment_id",
        ):
            if key in meta:
                derived_meta[key] = meta[key]
        # Strip forgeable policy knobs — retention/backup expiry live on Config.
        derived_meta.pop("retention_operational_days", None)
        derived_meta.pop("retention_audit_days", None)
        derived_meta.pop("backup_expiry_days", None)
        derived_meta["last_sequence"] = max(
            int(derived_meta.get("last_sequence", 0)), authority_max
        )
        _atomic_write_json(root / _INDEX_FILENAME, derived_index)
        _atomic_write_json(root / _META_FILENAME, derived_meta)
        index, meta = derived_index, derived_meta
    return index, meta, max(authority_max, int(meta.get("last_sequence", 0)))



def _next_seal_segment_id(root: Path) -> str:
    """Allocate the next sealed segment id from existing files (not forgeable meta).

    ``meta.open_segment_id`` is advisory only; sealing must never overwrite an
    existing ``NNNNNNNN.events.jsonl``.
    """
    segments = root / _SEGMENTS_DIRNAME
    max_id = 0
    if segments.is_dir():
        for path in segments.glob("*.events.jsonl"):
            name = path.name
            if name == _OPEN_SEGMENT_NAME:
                continue
            stem = name.replace(".events.jsonl", "")
            if stem.isdigit():
                max_id = max(max_id, int(stem))
    return f"{max_id + 1:08d}"


def _maybe_rotate_open_segment(root: Path, *, max_bytes: int | None = None) -> str:
    """Seal open segment when oversized; return current open segment_id."""
    limit = DEFAULT_SEGMENT_MAX_BYTES if max_bytes is None else max_bytes
    segments = root / _SEGMENTS_DIRNAME
    open_path = segments / _OPEN_SEGMENT_NAME
    meta = _load_meta(root)
    # Prefer filesystem-derived id so forged meta.open_segment_id cannot
    # overwrite an existing sealed segment.
    segment_id = _next_seal_segment_id(root)
    if not open_path.is_file() or open_path.stat().st_size < limit:
        return segment_id
    sealed_name = f"{int(segment_id):08d}.events.jsonl"
    sealed_path = segments / sealed_name
    if sealed_path.exists():
        raise InvalidInputError(
            "refusing to overwrite existing sealed segment during rotation",
            details={"segment": sealed_name},
        )
    data = open_path.read_bytes()
    _atomic_write_bytes(sealed_path, data)
    file_digest = hashlib.sha256(data).hexdigest()
    _atomic_write_json(
        segments / f"{int(segment_id):08d}.manifest.json",
        {
            "segment_id": segment_id,
            "kind": LEDGER_KIND,
            "sha256": file_digest,
            "sealed": True,
            "record_count": sum(1 for ln in data.splitlines() if ln.strip()),
            "tombstone_mac_initialized": (root / _TOMBSTONE_MAC_FILENAME).is_file(),
            "tombstone_digest": _tombstone_set_digest(root),
            "tombstone_count": _tombstone_line_count(root),
            "updated_at": _now(),
        },
    )
    _maybe_fault("mid_rotation_after_seal")
    # Reset open segment.
    _atomic_write_bytes(open_path, b"")
    next_id = f"{int(segment_id) + 1:08d}"
    meta["open_segment_id"] = next_id
    meta["updated_at"] = _now()
    _atomic_write_json(root / _META_FILENAME, meta)
    _atomic_write_json(
        segments / _OPEN_MANIFEST_NAME,
        {
            "segment_id": next_id,
            "kind": LEDGER_KIND,
            "record_count": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "sealed": False,
            "tombstone_mac_initialized": (root / _TOMBSTONE_MAC_FILENAME).is_file(),
            "tombstone_digest": _tombstone_set_digest(root),
            "tombstone_count": _tombstone_line_count(root),
            "updated_at": _now(),
        },
    )
    return next_id

@contextmanager
def _evidence_write_guard(
    cfg: Config, conn: sqlite3.Connection, holder: str
) -> Iterator[None]:
    """Existing CrossProcessLease + in-process writer_lease (no second lock).

    On every lease-held mutation, resume physical purge for any tombstoned
    event that still has residual payload bytes (never on the route hot path).
    """
    def _enter() -> None:
        root = _ensure_ledger_dirs(cfg)
        _ensure_tombstone_mac(cfg, root)
        _repurge_tombstoned_payloads(cfg, root)

    if lease_mod._CROSS_PROCESS_LEASE.get() is not None:  # noqa: SLF001
        with writer_lease(holder):
            _enter()
            yield
        return
    cross = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path,
        conn=conn,
        holder=holder,
    )
    with cross.acquire():
        with writer_lease(holder):
            _enter()
            yield


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
          segment_id=excluded.segment_id,
          tombstoned=0
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
        SET last_sequence = MAX(last_sequence, ?), last_segment_id = ?,
            redaction_version = ?, updated_at = ?
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
    """Lease-guarded durable checkpoint; segment is the source of truth (C6)."""
    validate_evidence_event(event)
    digest = payload_digest(event.payload_for_digest())

    with _evidence_write_guard(cfg, conn, holder):
        root = _ensure_ledger_dirs(cfg)
        segments = root / _SEGMENTS_DIRNAME
        _repair_torn_open_segment(segments)
        index, meta, authority_max = _reconcile_authority(root)

        by_id, _ = _scan_segment_authority(root)
        existing_row = by_id.get(event.event_id)
        if existing_row is not None and not existing_row.get("deleted"):
            existing_digest = str(existing_row.get("payload_digest") or "")
            if existing_digest != digest:
                raise IdempotencyKeyConflictError(
                    f"event_id {event.event_id!r} already committed with a different payload",
                    hint="retries must reuse the original immutable payload",
                    details={
                        "event_id": event.event_id,
                        "existing_digest": existing_digest,
                        "incoming_digest": digest,
                    },
                )
            _rewrite_derived_caches(root)
            _project_upsert(
                conn,
                event=_event_from_dict(existing_row["event"]),
                sequence=int(existing_row["sequence"]),
                segment_id=str(existing_row.get("segment_id", "00000001")),
                digest=existing_digest,
            )
            return CheckpointAck(
                event_id=event.event_id,
                sequence=int(existing_row["sequence"]),
                segment_id=str(existing_row.get("segment_id", "00000001")),
                payload_digest=existing_digest,
                replayed=True,
            )

        indexed = (index.get("events") or {}).get(event.event_id)
        if indexed is not None and indexed.get("payload_digest") == digest:
            return CheckpointAck(
                event_id=event.event_id,
                sequence=int(indexed["sequence"]),
                segment_id=str(indexed["segment_id"]),
                payload_digest=str(indexed["payload_digest"]),
                replayed=True,
            )
        if indexed is not None and indexed.get("payload_digest") != digest:
            raise IdempotencyKeyConflictError(
                f"event_id {event.event_id!r} already committed with a different payload",
                details={
                    "event_id": event.event_id,
                    "existing_digest": indexed.get("payload_digest"),
                    "incoming_digest": digest,
                },
            )

        sequence = authority_max + 1
        segment_id = str(meta.get("open_segment_id", "00000001"))
        record = _event_record(event, sequence=sequence, segment_id=segment_id, digest=digest)
        _append_line_fsync(segments / _OPEN_SEGMENT_NAME, _canonical_json(record))
        _maybe_fault("after_segment")

        open_path = segments / _OPEN_SEGMENT_NAME
        file_digest = hashlib.sha256(open_path.read_bytes()).hexdigest()
        _atomic_write_json(
            segments / _OPEN_MANIFEST_NAME,
            {
                "segment_id": segment_id,
                "kind": LEDGER_KIND,
                "record_count": sequence,
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
        _maybe_fault("after_index")

        _maybe_fault("before_meta")
        meta["last_sequence"] = sequence
        meta["open_segment_id"] = segment_id
        meta["updated_at"] = _now()
        meta.setdefault("backup_restore_requires_privacy_overlay", True)
        _atomic_write_json(root / _META_FILENAME, meta)

        _project_upsert(
            conn, event=event, sequence=sequence, segment_id=segment_id, digest=digest
        )
        _maybe_rotate_open_segment(root)
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
    receipt = get_receipt(receipt_id)
    if receipt is None:
        raise InvalidInputError(f"unknown receipt_id {receipt_id!r}")
    event = receipt_to_event(receipt, event_id=event_id)
    ack = checkpoint(cfg, conn, event)
    with _buffer_lock:
        _receipt_buffer.pop(receipt_id, None)
    return ack


def load_event(cfg: Config, event_id: str) -> EvidenceEvent | None:
    """Load via derived index; rebuild restores index after loss (C6).

    Never returns live data when a deleted stub exists for ``event_id`` or a
    tombstone names it. Segment stubs are authority independent of journal
    truncation (which fails closed via tombstones.mac).
    """
    root = evidence_dir(cfg)
    if not root.exists():
        return None
    _assert_tombstone_mac_or_absent(cfg, root)
    if _is_tombstoned(root, event_id):
        return None
    by_id, _ = _scan_segment_authority(root)
    row = by_id.get(event_id)
    if row is None or row.get("deleted"):
        return None
    # Defense in depth: any deleted stub for this id anywhere wins.
    for rec in _iter_segment_records(root):
        if _record_event_id(rec) == event_id and rec.get("deleted"):
            return None
    event_data = row.get("event")
    if not isinstance(event_data, dict):
        return None
    return _event_from_dict(event_data)


def _is_tombstoned(root: Path, event_id: str) -> bool:
    path = root / _TOMBSTONES_FILENAME
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("target_event_id") == event_id:
            return True
    return False


def _assert_tombstone_mac_or_absent(cfg: Config, root: Path) -> None:
    """Read-path gate: fail closed on missing/wrong tombstone MAC when ledger exists.

    Never auto-heals. Missing MAC fails closed whenever segments exist, any
    manifest records ``tombstone_mac_initialized``, or the journal is non-empty.
    """
    path = root / _TOMBSTONES_FILENAME
    mac_path = root / _TOMBSTONE_MAC_FILENAME
    initialized = _any_manifest_tombstone_mac_initialized(root)
    has_segments = _ledger_has_any_segments(root)
    has_tombstones = path.is_file() and bool(path.read_bytes().strip())

    if not mac_path.is_file():
        if initialized or has_segments or has_tombstones:
            _refuse_missing_tombstone_mac(root)
        return

    raw = path.read_bytes() if path.is_file() else b""
    try:
        body = json.loads(mac_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInputError(
            "tombstones.mac is corrupt; refusing to trust tombstone journal",
            details={"error": str(exc)},
        ) from exc
    expected = _compute_tombstone_mac(cfg, raw)
    actual = str(body.get("mac") or "")
    if not actual or not hmac.compare_digest(expected, actual):
        raise InvalidInputError(
            "tombstones.mac HMAC verification failed",
            details={"hint": "tombstone journal may be truncated or forged"},
        )
    if initialized or has_segments:
        current = _tombstone_set_digest(root)
        for mpath in (root / _SEGMENTS_DIRNAME).glob("*.manifest.json"):
            try:
                mbody = json.loads(mpath.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            bound = mbody.get("tombstone_digest")
            if bound is not None and str(bound) != current:
                raise InvalidInputError(
                    "tombstone digest diverges from segment manifest binding",
                    details={"manifest": mpath.name},
                )


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
    """Rebuild SQLite projections AND derived index/meta from the file ledger."""
    with _evidence_write_guard(cfg, conn, "evidence-rebuild"):
        root = _ensure_ledger_dirs(cfg)
        _repair_torn_open_segment(root / _SEGMENTS_DIRNAME)
        # Detect conflicting digests before checksum verify so integrity
        # conflicts surface as IdempotencyKeyConflictError.
        _scan_segment_authority(root)
        # Fail closed on corrupt / MAC-invalid export registry.
        _load_export_registry(cfg, root, required=False)
        verify_segments(root)
        index, meta = _rewrite_derived_caches(root)
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
        by_id, last_seq = _scan_segment_authority(root)
        last_seg: str | None = None
        for eid, row in by_id.items():
            if row.get("deleted") or eid in tombstoned:
                continue
            event = _event_from_dict(row["event"])
            seq = int(row["sequence"])
            seg = str(row.get("segment_id", "00000001"))
            digest = str(row.get("payload_digest") or "")
            last_seg = seg
            _project_upsert(conn, event=event, sequence=seq, segment_id=seg, digest=digest)
            count += 1

        if tombstone_path.is_file():
            for line in tombstone_path.read_text(encoding="utf-8").splitlines():
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
            (max(last_seq, int(meta.get("last_sequence", 0))), last_seg, _now()),
        )
        _ = index
        return count


def _manifest_path_for_segment(segment_path: Path) -> Path:
    if segment_path.name == _OPEN_SEGMENT_NAME:
        return segment_path.with_name(_OPEN_MANIFEST_NAME)
    # 00000001.events.jsonl -> 00000001.manifest.json
    stem = segment_path.name.replace(".events.jsonl", "")
    return segment_path.with_name(f"{stem}.manifest.json")


def _rewrite_segment_manifest(segment_path: Path, *, sealed: bool | None = None) -> dict[str, Any]:
    """Recompute sha256 / record_count and atomically rewrite the sibling manifest.

    Binds ``tombstone_mac_initialized`` + current tombstone journal digest so a
    truncated journal cannot be re-authenticated as a fresh ledger.
    """
    data = segment_path.read_bytes() if segment_path.is_file() else b""
    digest = hashlib.sha256(data).hexdigest()
    count = sum(1 for ln in data.splitlines() if ln.strip())
    if sealed is None:
        sealed = segment_path.name != _OPEN_SEGMENT_NAME
    if segment_path.name == _OPEN_SEGMENT_NAME:
        segment_id = "open"
        try:
            meta = _load_meta(segment_path.parent.parent)
            segment_id = str(meta.get("open_segment_id", "open"))
        except Exception:  # noqa: BLE001 — best-effort id for open manifest
            pass
    else:
        segment_id = segment_path.name.replace(".events.jsonl", "")
    root = segment_path.parent.parent
    # Preserve prior initialized flag if present; once true it stays true.
    prior_initialized = False
    prior_path = _manifest_path_for_segment(segment_path)
    if prior_path.is_file():
        try:
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            prior_initialized = bool(prior.get("tombstone_mac_initialized"))
        except (OSError, json.JSONDecodeError):
            prior_initialized = False
    mac_exists = (root / _TOMBSTONE_MAC_FILENAME).is_file()
    payload = {
        "segment_id": segment_id,
        "kind": LEDGER_KIND,
        "record_count": count,
        "sha256": digest,
        "sealed": sealed,
        "tombstone_mac_initialized": prior_initialized or mac_exists,
        "tombstone_digest": _tombstone_set_digest(root),
        "tombstone_count": _tombstone_line_count(root),
        "updated_at": _now(),
    }
    _atomic_write_json(_manifest_path_for_segment(segment_path), payload)
    return payload


def _tombstone_line_count(root: Path) -> int:
    path = root / _TOMBSTONES_FILENAME
    if not path.is_file():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


def _any_manifest_tombstone_mac_initialized(root: Path) -> bool:
    segments = root / _SEGMENTS_DIRNAME
    if not segments.is_dir():
        return False
    for path in segments.glob("*.manifest.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if body.get("tombstone_mac_initialized"):
            return True
    return False


def _stamp_tombstone_digest_on_all_manifests(root: Path, *, initialized: bool) -> None:
    segments = root / _SEGMENTS_DIRNAME
    if not segments.is_dir():
        return
    digest = _tombstone_set_digest(root)
    count = _tombstone_line_count(root)
    for path in list(segments.glob("*.events.jsonl")):
        manifest_path = _manifest_path_for_segment(path)
        if manifest_path.is_file():
            try:
                body = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                body = {}
        else:
            body = {}
        data = path.read_bytes() if path.is_file() else b""
        body.update(
            {
                "segment_id": body.get("segment_id")
                or (
                    "open"
                    if path.name == _OPEN_SEGMENT_NAME
                    else path.name.replace(".events.jsonl", "")
                ),
                "kind": LEDGER_KIND,
                "sha256": hashlib.sha256(data).hexdigest(),
                "record_count": sum(1 for ln in data.splitlines() if ln.strip()),
                "sealed": path.name != _OPEN_SEGMENT_NAME,
                "tombstone_mac_initialized": bool(
                    body.get("tombstone_mac_initialized") or initialized
                ),
                "tombstone_digest": digest,
                "tombstone_count": count,
                "updated_at": _now(),
            }
        )
        _atomic_write_json(manifest_path, body)


def verify_segments(root: Path) -> None:
    """Fail closed if any segment file's sha256 mismatches its sibling manifest."""
    segments = root / _SEGMENTS_DIRNAME
    if not segments.is_dir():
        return
    for path in sorted(segments.glob("*.events.jsonl")):
        manifest_path = _manifest_path_for_segment(path)
        if not manifest_path.is_file():
            # Open segment may lack a manifest until first write; create one.
            if path.name == _OPEN_SEGMENT_NAME and (
                not path.is_file() or path.stat().st_size == 0
            ):
                continue
            raise InvalidInputError(
                f"missing segment manifest for {path.name}",
                details={"segment": str(path), "expected_manifest": str(manifest_path)},
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = hashlib.sha256(path.read_bytes() if path.is_file() else b"").hexdigest()
        expected = str(manifest.get("sha256") or "")
        if actual != expected:
            raise InvalidInputError(
                f"segment checksum mismatch for {path.name}",
                details={
                    "segment": str(path),
                    "manifest_sha256": expected,
                    "actual_sha256": actual,
                },
            )


def _physically_purge_event_from_segments(root: Path, event_id: str) -> None:
    """Rewrite segments atomically, replacing payloads with deleted stubs (C6).

    After each rewritten ``*.events.jsonl``, recomputes and rewrites its sibling
    manifest. Emits ``after_purge_sealed`` once the first sealed segment for
    this event has been rewritten (crash-injection seam).
    """
    segments = root / _SEGMENTS_DIRNAME
    sealed_purged = False
    for path in sorted(segments.glob("*.events.jsonl")):
        if not path.is_file():
            continue
        lines_out: list[str] = []
        changed = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                lines_out.append(line)
                continue
            eid = _record_event_id(row)
            if eid == event_id and not row.get("deleted"):
                stub = {
                    "sequence": int(row["sequence"]),
                    "segment_id": str(row.get("segment_id", "00000001")),
                    "payload_digest": None,
                    "deleted": True,
                    "event_id": event_id,
                }
                lines_out.append(_canonical_json(stub))
                changed = True
            else:
                lines_out.append(line)
        if changed:
            data = ("\n".join(lines_out) + ("\n" if lines_out else "")).encode("utf-8")
            _atomic_write_bytes(path, data)
            sealed = path.name != _OPEN_SEGMENT_NAME
            _rewrite_segment_manifest(path, sealed=sealed)
            if sealed and not sealed_purged:
                sealed_purged = True
                _maybe_fault("after_purge_sealed")


def _event_has_live_payload(root: Path, event_id: str) -> bool:
    for row in _iter_segment_records(root):
        if _record_event_id(row) == event_id and not row.get("deleted") and row.get("event"):
            return True
    return False


def _tombstone_generation(root: Path) -> str:
    path = root / _TOMBSTONES_FILENAME
    if not path.is_file():
        return "empty"
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}:{st.st_ino}"


def _tombstone_set_digest(root: Path) -> str:
    """Stable digest of tombstone file bytes (empty file / missing → known digest)."""
    path = root / _TOMBSTONES_FILENAME
    if not path.is_file():
        raw = b""
    else:
        raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest()


def _segment_manifest_digest(root: Path) -> str:
    """Digest over all segment manifest files; any rewrite/rotation changes this."""
    segments = root / _SEGMENTS_DIRNAME
    if not segments.is_dir():
        return hashlib.sha256(b"").hexdigest()
    h = hashlib.sha256()
    for path in sorted(segments.glob("*.manifest.json")):
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"missing")
        h.update(b"\0")
    return h.hexdigest()


def _purge_marker_mac_key(cfg: Config) -> bytes:
    master = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return hmac.new(master, _PURGE_MARKER_MAC_LABEL, hashlib.sha256).digest()


def _purge_marker_mac(cfg: Config, body: dict[str, Any]) -> str:
    key = _purge_marker_mac_key(cfg)
    payload = {k: v for k, v in body.items() if k != "mac"}
    message = b"v1|" + _canonical_json(payload).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _ledger_has_any_segments(root: Path) -> bool:
    segments = root / _SEGMENTS_DIRNAME
    if not segments.is_dir():
        return False
    return any(p.is_file() for p in segments.glob("*.events.jsonl"))


def _purge_is_complete(cfg: Config, root: Path) -> bool:
    """Return True only when an authenticated marker still matches live state.

    Unauthenticated / mismatched markers are ignored (never suppress a purge).
    Callers must still compute pending ids even when this returns True.
    """
    marker = root / _PURGE_COMPLETE_FILENAME
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or not data.get("purge_complete"):
        return False
    if int(data.get("pending_count", -1)) != 0:
        return False
    actual = str(data.get("mac") or "")
    if not actual:
        return False
    expected = _purge_marker_mac(cfg, data)
    if not hmac.compare_digest(expected, actual):
        return False
    if str(data.get("generation")) != _tombstone_generation(root):
        return False
    if str(data.get("tombstone_digest")) != _tombstone_set_digest(root):
        return False
    if str(data.get("manifest_digest")) != _segment_manifest_digest(root):
        return False
    if str(data.get("mac_scheme") or "") != _PURGE_MARKER_MAC_SCHEME:
        return False
    return True


def _mark_purge_complete(cfg: Config, root: Path) -> None:
    body = {
        "purge_complete": True,
        "pending_count": 0,
        "generation": _tombstone_generation(root),
        "tombstone_digest": _tombstone_set_digest(root),
        "manifest_digest": _segment_manifest_digest(root),
        "mac_scheme": _PURGE_MARKER_MAC_SCHEME,
        "updated_at": _now(),
    }
    body["mac"] = _purge_marker_mac(cfg, body)
    _atomic_write_json(root / _PURGE_COMPLETE_FILENAME, body)


def _invalidate_purge_complete(root: Path) -> None:
    try:
        (root / _PURGE_COMPLETE_FILENAME).unlink(missing_ok=True)
    except OSError:
        pass


def _tombstone_mac_key(cfg: Config) -> bytes:
    master = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return hmac.new(master, _TOMBSTONE_MAC_LABEL, hashlib.sha256).digest()


def _compute_tombstone_mac(cfg: Config, raw: bytes) -> str:
    key = _tombstone_mac_key(cfg)
    return hmac.new(key, b"v1|" + raw, hashlib.sha256).hexdigest()


def _refuse_missing_tombstone_mac(root: Path) -> None:
    raise InvalidInputError(
        "tombstones.mac is missing; refusing to trust tombstone journal",
        details={
            "hint": (
                "restore tombstones.mac from backup; migrate_tombstone_mac only "
                "initializes a completely empty ledger"
            ),
            "initialized": _any_manifest_tombstone_mac_initialized(root),
            "has_segments": _ledger_has_any_segments(root),
        },
    )


def _ensure_tombstone_mac(cfg: Config, root: Path) -> None:
    """Verify tombstone MAC. Never auto-heal a missing MAC on a non-empty ledger.

    Initialization is allowed only when the ledger has no segment files at all
    (brand-new ledger). Otherwise missing MAC fails closed. ``migrate_tombstone_mac``
    is likewise empty-ledger-only (S09 never shipped a pre-MAC segmented ledger).
    """
    path = root / _TOMBSTONES_FILENAME
    mac_path = root / _TOMBSTONE_MAC_FILENAME
    initialized = _any_manifest_tombstone_mac_initialized(root)
    has_segments = _ledger_has_any_segments(root)
    has_tombstones = path.is_file() and bool(path.read_bytes().strip())

    if not mac_path.is_file():
        if initialized or has_segments or has_tombstones:
            _refuse_missing_tombstone_mac(root)
        # Completely empty ledger — initialize empty MAC once.
        _write_tombstone_mac(cfg, root)
        _stamp_tombstone_digest_on_all_manifests(root, initialized=True)
        return

    raw = path.read_bytes() if path.is_file() else b""
    try:
        body = json.loads(mac_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInputError(
            "tombstones.mac is corrupt; refusing to trust tombstone journal",
            details={"error": str(exc)},
        ) from exc
    expected = _compute_tombstone_mac(cfg, raw)
    actual = str(body.get("mac") or "")
    if not actual or not hmac.compare_digest(expected, actual):
        raise InvalidInputError(
            "tombstones.mac HMAC verification failed",
            details={
                "hint": "tombstone journal may be truncated or forged; restore from backup",
                "mac_scheme": _TOMBSTONE_MAC_SCHEME,
            },
        )
    # Cross-check digest bound into segment manifests when initialized.
    if initialized or has_segments:
        current = _tombstone_set_digest(root)
        for mpath in (root / _SEGMENTS_DIRNAME).glob("*.manifest.json"):
            try:
                mbody = json.loads(mpath.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            bound = mbody.get("tombstone_digest")
            if bound is not None and str(bound) != current:
                raise InvalidInputError(
                    "tombstone digest diverges from segment manifest binding",
                    details={"manifest": mpath.name, "bound": bound, "current": current},
                )


def migrate_tombstone_mac(cfg: Config, *, confirm: bool = False) -> dict[str, Any]:
    """Operator-only MAC init for a completely empty ledger.

    S09 has never shipped a pre-MAC ledger with segments, so this refuses
    whenever any segment file, segment manifest, event index, or tombstone
    journal content exists — regardless of unsigned manifest flags. Clearing
    ``tombstone_mac_initialized`` cannot authorize re-MAC of a truncated
    journal after an incomplete delete.
    """
    if not confirm:
        raise InvalidInputError(
            "migrate_tombstone_mac requires confirm=True",
            details={"hint": "explicit operator acknowledgment required"},
        )
    root = _ensure_ledger_dirs(cfg)
    if (root / _TOMBSTONE_MAC_FILENAME).is_file():
        raise InvalidInputError(
            "tombstones.mac already exists; migrate refused",
        )
    if _ledger_has_any_segments(root):
        raise InvalidInputError(
            "migrate_tombstone_mac refused: segment files exist",
            details={
                "hint": "restore authentic tombstones.mac from backup; never re-MAC a non-empty ledger",
            },
        )
    if any((root / _SEGMENTS_DIRNAME).glob("*.manifest.json")):
        raise InvalidInputError(
            "migrate_tombstone_mac refused: segment manifests exist",
            details={
                "hint": "restore authentic tombstones.mac from backup; never re-MAC a non-empty ledger",
            },
        )
    index_path = root / _INDEX_FILENAME
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {"events": {"_corrupt": True}}
        if index.get("events"):
            raise InvalidInputError(
                "migrate_tombstone_mac refused: evidence index is non-empty",
                details={"hint": "restore authentic tombstones.mac from backup"},
            )
    tomb_path = root / _TOMBSTONES_FILENAME
    if tomb_path.is_file() and tomb_path.read_bytes().strip():
        raise InvalidInputError(
            "migrate_tombstone_mac refused: tombstone journal is non-empty",
            details={"hint": "restore authentic tombstones.mac from backup"},
        )
    _write_tombstone_mac(cfg, root)
    return {
        "status": "ok",
        "tombstone_digest": _tombstone_set_digest(root),
        "tombstone_count": _tombstone_line_count(root),
    }


def _write_tombstone_mac(cfg: Config, root: Path) -> None:
    path = root / _TOMBSTONES_FILENAME
    raw = path.read_bytes() if path.is_file() else b""
    _atomic_write_json(
        root / _TOMBSTONE_MAC_FILENAME,
        {
            "mac_scheme": _TOMBSTONE_MAC_SCHEME,
            "mac": _compute_tombstone_mac(cfg, raw),
            "updated_at": _now(),
        },
    )


def _append_tombstone_line(cfg: Config, root: Path, tombstone: dict[str, Any]) -> None:
    _append_line_fsync(root / _TOMBSTONES_FILENAME, _canonical_json(tombstone))
    _write_tombstone_mac(cfg, root)
    _stamp_tombstone_digest_on_all_manifests(root, initialized=True)
    _invalidate_purge_complete(root)


def _pending_tombstone_payload_ids(root: Path) -> set[str]:
    """Single segment scan: tombstoned event ids that still have live payloads."""
    tombstoned = {
        str(row.get("target_event_id") or "")
        for row in _load_tombstone_rows(root)
        if row.get("target_event_id")
    }
    if not tombstoned:
        return set()
    pending: set[str] = set()
    for row in _iter_segment_records(root):
        eid = _record_event_id(row)
        if eid in tombstoned and not row.get("deleted") and row.get("event"):
            pending.add(eid)
    return pending


def _repurge_tombstoned_payloads(cfg: Config, root: Path) -> None:
    """Resume physical purge for tombstoned events that still have payloads.

    Even a MAC-valid purge marker cannot suppress purge when pending payloads
    remain — always compute pending ids; marker only avoids re-writing when
    pending is already empty.
    """
    marker_ok = _purge_is_complete(cfg, root)
    pending = _pending_tombstone_payload_ids(root)
    if marker_ok and not pending:
        return
    for eid in pending:
        _physically_purge_event_from_segments(root, eid)
    _mark_purge_complete(cfg, root)


def _load_tombstone_rows(root: Path) -> list[dict[str, Any]]:
    path = root / _TOMBSTONES_FILENAME
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _managed_exports_root(root: Path) -> Path:
    return (root / "exports").resolve()


def _path_equals_or_under_casefold(path: Path, ancestor: Path) -> bool:
    """Casefold path-prefix check for case-insensitive filesystems."""
    try:
        pp = str(path.resolve()).replace("\\", "/").casefold()
        aa = str(ancestor.resolve()).replace("\\", "/").casefold()
    except OSError:
        pp = str(path).replace("\\", "/").casefold()
        aa = str(ancestor).replace("\\", "/").casefold()
    if pp == aa:
        return True
    prefix = aa if aa.endswith("/") else aa + "/"
    return pp.startswith(prefix)


def _path_equals_or_under(path: Path, ancestor: Path) -> bool:
    """True if path equals or lies under ancestor (casefold + samefile aware)."""
    try:
        resolved = path.resolve()
        anc = ancestor.resolve()
    except OSError:
        return _path_equals_or_under_casefold(path, ancestor)
    if resolved == anc:
        return True
    try:
        resolved.relative_to(anc)
        return True
    except ValueError:
        pass
    if _path_equals_or_under_casefold(resolved, anc):
        return True
    cursor = resolved
    for _ in range(64):
        try:
            if cursor.exists() and anc.exists() and cursor.samefile(anc):
                return True
        except OSError:
            break
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return False


def _paths_overlap(a: Path, b: Path) -> bool:
    """True if a equals b, a is inside b, or b is inside a (casefold-aware)."""
    if _path_equals_or_under(a, b) or _path_equals_or_under(b, a):
        return True
    try:
        if a.resolve().exists() and b.resolve().exists() and a.resolve().samefile(b.resolve()):
            return True
    except OSError:
        pass
    return False


def _protected_control_paths(cfg: Config, root: Path) -> list[Path]:
    """Magicite control / ledger locations that must never be export roots."""
    paths = [
        cfg.data_dir,
        root,  # evidence/
        cfg.registry_dir,
        cfg.approvals_dir,
        cfg.runtime_dir,
        cfg.archive_dir,
        cfg.data_dir / "trust",
        cfg.data_dir / "migrations",
    ]
    return [p.resolve() for p in paths]


def _validate_configured_export_roots(cfg: Config, root: Path) -> None:
    """Reject configured roots that equal/contain/are-inside protected dirs.

    The managed ``evidence/exports/`` subtree is the sole exception under the
    data dir. Fail closed with :class:`InvalidInputError`.
    """
    managed = _managed_exports_root(root)
    protected = _protected_control_paths(cfg, root)
    for raw in getattr(cfg, "evidence_export_roots", ()) or ():
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = (cfg.project_root / p).resolve()
        else:
            p = p.resolve()
        # Allowed: managed exports root or anything strictly under it.
        if p == managed or _path_equals_or_under(p, managed):
            continue
        for ctrl in protected:
            if _paths_overlap(p, ctrl):
                raise InvalidInputError(
                    "refusing evidence export root that overlaps protected control paths",
                    details={
                        "export_root": str(p),
                        "protected": str(ctrl),
                        "hint": (
                            "set MAGICITE_EVIDENCE_EXPORT_ROOTS to directories outside "
                            "the data dir; only evidence/exports/ is allowed inside it"
                        ),
                    },
                )


def _is_hard_denied_purge_path(cfg: Config, root: Path, path: Path) -> bool:
    """Hard-deny unlink of ledger authority / control files regardless of roots."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    managed = _managed_exports_root(root)
    if resolved == managed or _path_equals_or_under(resolved, managed):
        return False

    # Anything else under evidence/ is protected (segments, manifests, meta, …).
    if _path_equals_or_under(resolved, root.resolve()):
        return True

    explicit = [
        cfg.db_path,
        Path(str(cfg.db_path) + "-wal"),
        Path(str(cfg.db_path) + "-shm"),
        fingerprint_key_mod.fingerprint_key_path(cfg),
        root / _REGISTERED_EXPORTS_FILENAME,
        root / _META_FILENAME,
        root / _INDEX_FILENAME,
        root / _TOMBSTONES_FILENAME,
        root / _PURGE_COMPLETE_FILENAME,
        cfg.dream_lock_path,
    ]
    for f in explicit:
        try:
            if resolved == f.resolve():
                return True
        except OSError:
            continue

    for ctrl in (
        cfg.registry_dir,
        cfg.approvals_dir,
        cfg.runtime_dir,
        cfg.archive_dir,
        cfg.data_dir / "trust",
        cfg.data_dir / "migrations",
    ):
        if _path_equals_or_under(resolved, ctrl.resolve()):
            return True
    return False


def _safe_unlink_verified(path: Path, *, expected_dev: int, expected_ino: int) -> None:
    """Unlink via directory fd after O_NOFOLLOW verify (TOCTOU hardening).

    Residual: on platforms without ``O_NOFOLLOW`` / ``dir_fd`` support we fall
    back to ``os.unlink`` after a final ``lstat`` inode check.
    """
    parent = path.parent
    name = path.name
    use_dir_fd = hasattr(os, "O_DIRECTORY") and hasattr(os, "unlink")
    if not use_dir_fd:
        st = os.lstat(path)
        if st.st_dev != expected_dev or st.st_ino != expected_ino:
            raise OSError("inode changed before unlink")
        os.unlink(path)
        return

    dir_fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=dir_fd)
        except TypeError:
            # dir_fd unsupported
            st = os.lstat(path)
            if st.st_dev != expected_dev or st.st_ino != expected_ino:
                raise OSError("inode changed before unlink") from None
            os.unlink(path)
            return
        try:
            st = os.fstat(fd)
            if st.st_dev != expected_dev or st.st_ino != expected_ino:
                raise OSError("inode changed before unlink")
            if not stat.S_ISREG(st.st_mode):
                raise OSError("not a regular file at unlink time")
        finally:
            os.close(fd)
        try:
            os.unlink(name, dir_fd=dir_fd)
        except TypeError:
            st = os.lstat(path)
            if st.st_dev != expected_dev or st.st_ino != expected_ino:
                raise OSError("inode changed before unlink") from None
            os.unlink(path)
    finally:
        os.close(dir_fd)


def _purge_managed_and_registered_exports(
    cfg: Config, root: Path
) -> dict[str, Any]:
    """AC-S09-05 + Round-3/4: allowlisted, file-level, MAC-authenticated purge.

    Returns a report ``{purged: [...], skipped: [...]}``. Never follows
    symlinks; never rmtree's directories; never unlinks outside allowed roots
    or under hard-denied protected locations.
    """
    report: dict[str, Any] = {"purged": [], "skipped": []}
    _validate_configured_export_roots(cfg, root)
    registry = _load_export_registry(cfg, root, required=False)
    if registry is None:
        return report

    roots = _allowed_export_roots(cfg, root)
    remaining_files: list[dict[str, Any]] = []
    for entry in list(registry.get("exports") or []):
        if not isinstance(entry, dict):
            report["skipped"].append({"reason": "invalid_entry", "entry": entry})
            continue
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw:
            report["skipped"].append({"reason": "missing_path", "entry": entry})
            continue
        target = Path(raw)
        try:
            resolved_for_check = target.resolve()
        except OSError as exc:
            report["skipped"].append(
                {"path": raw, "reason": f"resolve_failed:{exc}"}
            )
            remaining_files.append(entry)
            continue
        if _is_hard_denied_purge_path(cfg, root, resolved_for_check):
            report["skipped"].append(
                {
                    "path": raw,
                    "reason": "protected_path_denied",
                    "detail": "path is under ledger/control locations",
                }
            )
            remaining_files.append(entry)
            continue
        if not _path_is_under_allowed_roots(resolved_for_check, roots):
            raise InvalidInputError(
                "registered export path is outside allowed roots",
                details={"path": raw, "allowed_roots": [str(r) for r in roots]},
            )
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            report["purged"].append({"path": raw, "status": "already_absent"})
            continue
        except OSError as exc:
            report["skipped"].append({"path": raw, "reason": f"lstat_failed:{exc}"})
            remaining_files.append(entry)
            continue
        if stat.S_ISLNK(st.st_mode):
            report["skipped"].append({"path": raw, "reason": "is_symlink"})
            remaining_files.append(entry)
            continue
        if not stat.S_ISREG(st.st_mode):
            report["skipped"].append({"path": raw, "reason": "not_regular_file"})
            remaining_files.append(entry)
            continue
        expected_size = int(entry.get("size", -1))
        expected_sha = str(entry.get("sha256") or "")
        expected_dev = int(entry.get("st_dev", -1))
        expected_ino = int(entry.get("st_ino", -1))
        if st.st_size != expected_size or st.st_dev != expected_dev or st.st_ino != expected_ino:
            report["skipped"].append(
                {
                    "path": raw,
                    "reason": "metadata_mismatch",
                    "expected": {
                        "size": expected_size,
                        "st_dev": expected_dev,
                        "st_ino": expected_ino,
                    },
                    "actual": {
                        "size": st.st_size,
                        "st_dev": st.st_dev,
                        "st_ino": st.st_ino,
                    },
                }
            )
            remaining_files.append(entry)
            continue
        try:
            actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            report["skipped"].append({"path": raw, "reason": f"read_failed:{exc}"})
            remaining_files.append(entry)
            continue
        if actual_sha != expected_sha:
            report["skipped"].append(
                {
                    "path": raw,
                    "reason": "sha256_mismatch",
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                }
            )
            remaining_files.append(entry)
            continue
        # Re-check hard-deny after verify (defense in depth).
        if _is_hard_denied_purge_path(cfg, root, target):
            report["skipped"].append(
                {"path": raw, "reason": "protected_path_denied"}
            )
            remaining_files.append(entry)
            continue
        try:
            _safe_unlink_verified(
                target, expected_dev=expected_dev, expected_ino=expected_ino
            )
        except OSError as exc:
            report["skipped"].append({"path": raw, "reason": f"unlink_failed:{exc}"})
            remaining_files.append(entry)
            continue
        report["purged"].append({"path": raw, "status": "unlinked"})

    remaining_dirs: list[dict[str, Any]] = []
    for entry in list(registry.get("dirs") or []):
        if not isinstance(entry, dict) or not entry.get("created_by_magicite"):
            continue
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        dpath = Path(raw)
        try:
            resolved = dpath.resolve()
        except OSError:
            remaining_dirs.append(entry)
            continue
        if _is_hard_denied_purge_path(cfg, root, resolved):
            remaining_dirs.append(entry)
            continue
        if not _path_is_under_allowed_roots(resolved, roots):
            raise InvalidInputError(
                "registered export dir is outside allowed roots",
                details={"path": raw},
            )
        try:
            st = os.lstat(dpath)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            remaining_dirs.append(entry)
            continue
        try:
            os.rmdir(dpath)  # fails if non-empty — never rmtree
        except OSError:
            remaining_dirs.append(entry)

    registry["exports"] = remaining_files
    registry["dirs"] = remaining_dirs
    registry["updated_at"] = _now()
    _save_export_registry(cfg, root, registry)
    return report


def _allowed_export_roots(cfg: Config, root: Path) -> list[Path]:
    _validate_configured_export_roots(cfg, root)
    roots = [_managed_exports_root(root)]
    for raw in getattr(cfg, "evidence_export_roots", ()) or ():
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = (cfg.project_root / p).resolve()
        else:
            p = p.resolve()
        roots.append(p)
    return roots


def _path_is_under_allowed_roots(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if ".." in resolved.parts:
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _assert_export_destination_allowed(cfg: Config, root: Path, export_dir: Path) -> Path:
    """Refuse symlinks and destinations outside allowed roots (fail closed)."""
    if export_dir.exists() and export_dir.is_symlink():
        raise InvalidInputError(
            "refusing symlinked export directory",
            details={"export_dir": str(export_dir)},
        )
    cursor = export_dir
    for _ in range(64):
        if cursor.exists() and cursor.is_symlink():
            raise InvalidInputError(
                "refusing export path with symlink ancestor",
                details={"export_dir": str(export_dir), "symlink": str(cursor)},
            )
        if cursor.parent == cursor:
            break
        cursor = cursor.parent

    resolved = export_dir.expanduser()
    if not resolved.is_absolute():
        resolved = (cfg.project_root / resolved).resolve()
    else:
        resolved = resolved.resolve()
    if _is_hard_denied_purge_path(cfg, root, resolved) and not _path_equals_or_under(
        resolved, _managed_exports_root(root)
    ):
        raise InvalidInputError(
            "export destination is under protected control paths",
            details={"export_dir": str(resolved)},
        )
    roots = _allowed_export_roots(cfg, root)
    if not _path_is_under_allowed_roots(resolved, roots):
        raise InvalidInputError(
            "export destination is outside allowed roots",
            details={
                "export_dir": str(resolved),
                "allowed_roots": [str(r) for r in roots],
                "hint": "set MAGICITE_EVIDENCE_EXPORT_ROOTS or use evidence/exports/",
            },
        )
    return resolved


def _file_fingerprint(path: Path) -> dict[str, Any]:
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise InvalidInputError(
            "refusing to register non-regular export file",
            details={"path": str(path)},
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path.resolve()),
        "sha256": digest,
        "size": int(st.st_size),
        "st_dev": int(st.st_dev),
        "st_ino": int(st.st_ino),
        "exported_at": _now(),
    }


def _registry_mac_key(cfg: Config) -> bytes:
    master = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return hmac.new(master, _REGISTRY_MAC_LABEL, hashlib.sha256).digest()


def _registry_mac(cfg: Config, body: dict[str, Any]) -> str:
    key = _registry_mac_key(cfg)
    payload = {k: v for k, v in body.items() if k != "mac"}
    message = _REGISTRY_MAC_VERSION_PREFIX + _canonical_json(payload).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _legacy_registry_mac(cfg: Config, body: dict[str, Any]) -> str:
    """Pre-Round-4 MAC (raw fingerprint key, no domain separation)."""
    key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    payload = {k: v for k, v in body.items() if k != "mac"}
    return hmac.new(
        key, _canonical_json(payload).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _load_export_registry(
    cfg: Config, root: Path, *, required: bool = False
) -> dict[str, Any] | None:
    path = root / _REGISTERED_EXPORTS_FILENAME
    if not path.is_file():
        if required:
            raise InvalidInputError("registered_exports.json is required but missing")
        return None
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidInputError(
            "registered_exports.json is corrupt (invalid JSON)",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(registry, dict):
        raise InvalidInputError("registered_exports.json must be a JSON object")
    expected = _registry_mac(cfg, registry)
    actual = str(registry.get("mac") or "")
    if actual and hmac.compare_digest(expected, actual):
        return registry
    # Fail closed on legacy scheme — require re-registration (no silent accept).
    if actual and hmac.compare_digest(_legacy_registry_mac(cfg, registry), actual):
        raise InvalidInputError(
            "registered_exports.json uses legacy MAC; registry needs re-registration",
            details={
                "mac_scheme_required": _REGISTRY_MAC_SCHEME,
                "hint": "delete registered_exports.json and re-export under the current scheme",
            },
        )
    raise InvalidInputError(
        "registered_exports.json HMAC verification failed",
        details={"hint": "registry may be tampered or from another key epoch"},
    )


def _save_export_registry(cfg: Config, root: Path, registry: dict[str, Any]) -> None:
    body = dict(registry)
    body.setdefault("kind", "EvidenceExportRegistry/1")
    body["mac_scheme"] = _REGISTRY_MAC_SCHEME
    body["mac"] = _registry_mac(cfg, body)
    _atomic_write_json(root / _REGISTERED_EXPORTS_FILENAME, body)


def _register_export_files(
    cfg: Config,
    root: Path,
    files: list[Path],
    *,
    created_dirs: list[Path] | None = None,
) -> None:
    """Register exact files magicite wrote (never directories), under the lease."""
    _validate_configured_export_roots(cfg, root)
    registry = _load_export_registry(cfg, root, required=False) or {
        "kind": "EvidenceExportRegistry/1",
        "exports": [],
        "dirs": [],
    }
    entries = list(registry.get("exports") or [])
    by_path = {e.get("path"): i for i, e in enumerate(entries) if isinstance(e, dict)}
    for fpath in files:
        fp = _file_fingerprint(fpath)
        resolved = Path(fp["path"])
        if _is_hard_denied_purge_path(cfg, root, resolved) and not _path_equals_or_under(
            resolved, _managed_exports_root(root)
        ):
            raise InvalidInputError(
                "refusing to register protected path as export file",
                details={"path": fp["path"]},
            )
        if not _path_is_under_allowed_roots(
            resolved, _allowed_export_roots(cfg, root)
        ):
            raise InvalidInputError(
                "refusing to register export file outside allowed roots",
                details={"path": fp["path"]},
            )
        idx = by_path.get(fp["path"])
        if idx is None:
            entries.append(fp)
            by_path[fp["path"]] = len(entries) - 1
        else:
            entries[idx] = fp
    registry["exports"] = entries
    dirs = list(registry.get("dirs") or [])
    dir_paths = {d.get("path") for d in dirs if isinstance(d, dict)}
    for d in created_dirs or ():
        dir_resolved = d.resolve()
        dir_key = str(dir_resolved)
        if dir_key not in dir_paths:
            if not _path_is_under_allowed_roots(dir_resolved, _allowed_export_roots(cfg, root)):
                raise InvalidInputError(
                    "refusing to register export dir outside allowed roots",
                    details={"path": dir_key},
                )
            dirs.append(
                {
                    "path": dir_key,
                    "created_by_magicite": True,
                    "exported_at": _now(),
                }
            )
            dir_paths.add(dir_key)
    registry["dirs"] = dirs
    registry["updated_at"] = _now()
    _save_export_registry(cfg, root, registry)


def _delete_event_locked(
    cfg: Config,
    conn: sqlite3.Connection,
    event_id: str,
    *,
    reason: str | None = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Tombstone-first then physical purge. Caller holds write guard.

    Ordering is crash-critical: once the tombstone is fsynced, load/export/
    rebuild hide the event even if segment purge is incomplete. Recovery
    (rebuild / verify / next lease-held mutation) re-purges residual payloads.
    """
    root = _ensure_ledger_dirs(cfg)
    _repair_torn_open_segment(root / _SEGMENTS_DIRNAME)
    by_id, authority_max = _scan_segment_authority(root)

    if _is_tombstoned(root, event_id):
        _physically_purge_event_from_segments(root, event_id)
        purge_report = _purge_managed_and_registered_exports(cfg, root)
        _mark_purge_complete(cfg, root)
        for row in _load_tombstone_rows(root):
            if row.get("target_event_id") == event_id:
                return {**row, "export_purge_skipped": purge_report.get("skipped", [])}
        return {
            "tombstone_id": f"tomb_idem_{event_id[:12]}",
            "target_event_id": event_id,
            "sequence": authority_max,
            "deleted_at": _now(),
            "reason": reason or "already_deleted",
            "actor": actor,
            "export_purge_skipped": purge_report.get("skipped", []),
        }

    if event_id not in by_id:
        index = _load_index(root)
        if event_id not in (index.get("events") or {}):
            raise InvalidInputError(f"unknown event_id {event_id!r}")

    sequence = authority_max + 1
    tombstone = {
        "tombstone_id": f"tomb_{uuid.uuid4().hex[:12]}",
        "target_event_id": event_id,
        "sequence": sequence,
        "deleted_at": _now(),
        "reason": reason,
        "actor": actor,
    }
    # (a) Durable hide first (tombstone journal + MAC).
    _append_tombstone_line(cfg, root, tombstone)
    _maybe_fault("after_tombstone")

    # (b) Physical purge (idempotent / resumable).
    _physically_purge_event_from_segments(root, event_id)

    index, meta = _rewrite_derived_caches(root)
    meta["last_sequence"] = max(int(meta.get("last_sequence", 0)), sequence)
    meta["updated_at"] = _now()
    _atomic_write_json(root / _META_FILENAME, meta)
    events = dict(index.get("events") or {})
    events.pop(event_id, None)
    _atomic_write_json(root / _INDEX_FILENAME, {"events": events})

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
    conn.execute("DELETE FROM evidence_event_projection WHERE event_id = ?", (event_id,))
    purge_report = _purge_managed_and_registered_exports(cfg, root)
    _mark_purge_complete(cfg, root)
    return {
        **tombstone,
        "export_purge_skipped": purge_report.get("skipped", []),
        "export_purge_purged": purge_report.get("purged", []),
    }


def delete_event(
    cfg: Config,
    conn: sqlite3.Connection,
    event_id: str,
    *,
    reason: str | None = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Physically purge payload bytes and append a minimal tombstone (C6)."""
    with _evidence_write_guard(cfg, conn, "evidence-delete"):
        return _delete_event_locked(cfg, conn, event_id, reason=reason, actor=actor)


def _export_rekey(value: str, *, scope_key: bytes) -> str:
    return hmac.new(scope_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def export_evidence(
    cfg: Config,
    conn: sqlite3.Connection | None = None,
    *,
    event_ids: list[str] | None = None,
    export_dir: Path | None = None,
) -> Path:
    """Export with fresh scoped pseudonyms; correlators re-keyed per export (C6).

    Writes under the existing CrossProcessLease when a connection is available
    (or a short-lived DB connection is opened for fencing). Reconciles derived
    caches from segments before reading so a wiped index cannot empty the
    export. Caller-chosen destinations outside ``evidence/exports/`` are
    registered for privacy deletion (AC-S09-05).
    """

    def _write_export(owned_conn: sqlite3.Connection) -> Path:
        with _evidence_write_guard(cfg, owned_conn, "evidence-export"):
            root = _ensure_ledger_dirs(cfg)
            # Segment authority + derived cache heal.
            _rewrite_derived_caches(root)
            by_id, _ = _scan_segment_authority(root)
            selected = event_ids or [
                eid
                for eid, row in by_id.items()
                if not row.get("deleted") and not _is_tombstoned(root, eid)
            ]
            scope_key = os.urandom(32)
            pseudonym_scope = scope_key.hex()
            default_dir = root / "exports" / f"export_{pseudonym_scope[:12]}"
            out_dir = export_dir or default_dir
            out_dir = _assert_export_destination_allowed(cfg, root, out_dir)
            created_dirs: list[Path] = []
            if not out_dir.exists():
                out_dir.mkdir(parents=True, exist_ok=True)
                created_dirs.append(out_dir)
            # Ensure managed exports/ parent is trackable for rmdir.
            managed_exports = (root / "exports").resolve()
            if managed_exports not in {d.resolve() for d in created_dirs}:
                if out_dir.resolve() != managed_exports and managed_exports in out_dir.resolve().parents:
                    # Parent export_* was created above; also note exports/ if we created it.
                    pass

            id_map: dict[str, str] = {}
            events_out: list[dict[str, Any]] = []
            correlator_fields = (
                "query_fingerprint",
                "context_fingerprint",
                "registry_fingerprint",
                "model_fingerprint",
                "config_fingerprint",
            )
            for event_id in selected:
                if _is_tombstoned(root, event_id):
                    continue
                row = by_id.get(event_id)
                if row is None or row.get("deleted"):
                    continue
                event_data = row.get("event")
                if not isinstance(event_data, dict):
                    continue
                event = _event_from_dict(event_data)
                payload = event.to_dict()
                _assert_no_raw_leak(payload)
                for field_name in ("event_id", "decision_id"):
                    original = str(payload[field_name])
                    if original not in id_map:
                        id_map[original] = (
                            "pseudo_" + _export_rekey(original, scope_key=scope_key)[:16]
                        )
                    payload[field_name] = id_map[original]
                for field_name in correlator_fields:
                    val = payload.get(field_name)
                    if isinstance(val, str) and val:
                        payload[field_name] = _export_rekey(val, scope_key=scope_key)
                payload["fingerprint_scheme"] = "hmac-sha256/export-scoped-v1"
                events_out.append(payload)

            manifest = {
                "kind": "EvidenceExport/1",
                "pseudonym_scope": hashlib.sha256(scope_key).hexdigest()[:24],
                "fingerprint_scheme": "hmac-sha256/export-scoped-v1",
                "redaction_version": REDACTION_VERSION,
                "exported_at": _now(),
                "event_count": len(events_out),
                "fingerprint_key_exported": False,
                "correlators_rekeyed": True,
                "privacy_deletion_notice": EXPORT_COPY_DELETION_NOTICE,
            }
            _assert_no_raw_leak(manifest)
            manifest_path = out_dir / "manifest.json"
            export_path = out_dir / "events.jsonl"
            _atomic_write_json(manifest_path, manifest)
            raw = "".join(_canonical_json(row) + "\n" for row in events_out).encode("utf-8")
            _atomic_write_bytes(export_path, raw)
            # Register exact files written (never directories as deletable targets).
            _register_export_files(
                cfg,
                root,
                [manifest_path, export_path],
                created_dirs=created_dirs,
            )
            return out_dir

    if conn is not None:
        return _write_export(conn)
    from magicite.storage import db as db_mod

    cfg.ensure_dirs()
    owned = db_mod.connect(cfg.db_path)
    try:
        return _write_export(owned)
    finally:
        owned.close()


def apply_retention(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Tombstone+purge expired operational/audit records per Config policy (C6).

    Retention days come from operator Config defaults (30d operational / 90d
    audit). ``meta.json`` retention fields are ignored.
    """
    with _evidence_write_guard(cfg, conn, "evidence-retention"):
        root = evidence_dir(cfg)
        now_dt = now or datetime.now(UTC)
        op_days = int(
            getattr(cfg, "evidence_retention_operational_days", DEFAULT_OPERATIONAL_RETENTION_DAYS)
        )
        audit_days = int(
            getattr(cfg, "evidence_retention_audit_days", DEFAULT_AUDIT_RETENTION_DAYS)
        )
        deleted: list[str] = []
        by_id, _ = _scan_segment_authority(root) if root.exists() else ({}, 0)
        for event_id, row in list(by_id.items()):
            if row.get("deleted") or _is_tombstoned(root, event_id):
                continue
            event = _event_from_dict(row["event"])
            recorded = datetime.fromisoformat(event.recorded_at)
            limit_days = audit_days if event.retention_class == "audit" else op_days
            if recorded + timedelta(days=limit_days) <= now_dt:
                _delete_event_locked(
                    cfg, conn, event_id, reason="retention_expiry", actor="retention"
                )
                deleted.append(event_id)
        return deleted


def backup_expiry_days(cfg: Config) -> int:
    """Backup overlay expiry from operator Config (never from forgeable meta.json)."""
    return int(
        getattr(cfg, "evidence_backup_expiry_days", DEFAULT_BACKUP_EXPIRY_DAYS)
    )


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
        sorted(hashlib.sha256(_canonical_json(r).encode()).hexdigest() for r in records)
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
    """Replay deletion tombstones after restore (S12 hook)."""
    if overlay.kind != RECOVERY_OVERLAY_KIND:
        raise InvalidInputError(f"unsupported overlay kind {overlay.kind!r}")
    if expected_registry_id is not None and overlay.registry_id != expected_registry_id:
        raise InvalidInputError(
            "privacy overlay registry_id mismatch",
            details={"expected": expected_registry_id, "got": overlay.registry_id},
        )
    if minimum_sequence is not None and overlay.control_sequence < minimum_sequence:
        raise InvalidInputError(
            "privacy overlay sequence is stale",
            details={"minimum_sequence": minimum_sequence, "got": overlay.control_sequence},
        )
    applied = 0
    with _evidence_write_guard(cfg, conn, "evidence-overlay"):
        for record in overlay.deletion_records:
            event_id = str(record["target_event_id"])
            root = evidence_dir(cfg)
            if _is_tombstoned(root, event_id):
                _physically_purge_event_from_segments(root, event_id)
                continue
            by_id, _ = _scan_segment_authority(root)
            if event_id in by_id and not by_id[event_id].get("deleted"):
                _delete_event_locked(
                    cfg,
                    conn,
                    event_id,
                    reason=str(record.get("reason") or "overlay_replay"),
                    actor=str(record.get("actor") or "privacy_overlay"),
                )
            else:
                root = _ensure_ledger_dirs(cfg)
                _, authority_max = _scan_segment_authority(root)
                sequence = authority_max + 1
                tombstone = {
                    "tombstone_id": record.get("tombstone_id")
                    or f"tomb_{uuid.uuid4().hex[:12]}",
                    "target_event_id": event_id,
                    "sequence": sequence,
                    "deleted_at": record.get("deleted_at") or _now(),
                    "reason": record.get("reason") or "overlay_replay",
                    "actor": record.get("actor") or "privacy_overlay",
                }
                _append_tombstone_line(cfg, root, tombstone)
                _physically_purge_event_from_segments(root, event_id)
                _rewrite_derived_caches(root)
                _mark_purge_complete(cfg, root)
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
        "retention_operational_days": int(
            getattr(cfg, "evidence_retention_operational_days", DEFAULT_OPERATIONAL_RETENTION_DAYS)
        ),
        "retention_audit_days": int(
            getattr(cfg, "evidence_retention_audit_days", DEFAULT_AUDIT_RETENTION_DAYS)
        ),
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
