"""Durable independent trust authority (the service owns this store).

This module never opens a registry directory. Production callers use the peer-
authenticated transport; direct construction is for the custodian and unit tests.
SQLite FULL transactions are the authority's linearization/durability boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Any, get_args

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

DOMAIN = b"magicite-trust-journal/1\x00"
HEAD_FIELDS = ("registry_id", "epoch", "head_sequence", "head_mac")
PREDECESSOR_FIELDS = (*HEAD_FIELDS, "fence_generation")
MAX_PAYLOAD = 4 * 1024 * 1024


class CustodianError(ValueError):
    """Redacted authority failure; callers must close admission."""


def _bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    except (ValueError, TypeError) as exc:
        raise CustodianError("invalid authority value") from exc
    if len(encoded) > MAX_PAYLOAD:
        raise CustodianError("authority value exceeds limit")
    return encoded


def _equal(left: Any, right: Any) -> bool:
    return _bytes(left) == _bytes(right)


def _match(left: dict[str, Any], right: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(key in left and key in right and _equal(left[key], right[key]) for key in fields)


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise CustodianError("invalid authority identifier")


class CustodianStore:
    """Explicitly created, independently held transactional authority.

    Existing stores are only opened, never recreated when keys/state are lost.
    No client-controlled filename is accepted by any registry operation.
    """

    def __init__(
        self, directory: Path, connection: sqlite3.Connection, key: bytes, signing_key: Ed25519PrivateKey
    ):
        self.directory = directory
        self._conn = connection
        self._key = key
        self.signing_key = signing_key
        self._closed = False

    @classmethod
    def create(cls, directory: Path) -> CustodianStore:
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        key = secrets.token_bytes(32)
        signing = Ed25519PrivateKey.generate()
        for name, data in (
            ("journal.key", key),
            ("signing.key", signing.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())),
        ):
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        conn = sqlite3.connect(directory / "authority.sqlite")
        os.chmod(directory / "authority.sqlite", 0o600)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE registry (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        conn.commit()
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        parent_fd = os.open(directory.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return cls(directory, conn, key, signing)

    @classmethod
    def open(cls, directory: Path) -> CustodianStore:
        try:
            for name in ("journal.key", "signing.key", "authority.sqlite"):
                info = (directory / name).lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise CustodianError("custody state must remain private")
            key = (directory / "journal.key").read_bytes()
            signing = Ed25519PrivateKey.from_private_bytes((directory / "signing.key").read_bytes())
            if len(key) != 32:
                raise ValueError("key")
            conn = sqlite3.connect(f"file:{directory / 'authority.sqlite'}?mode=rw", uri=True)
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("SELECT id FROM registry LIMIT 1")
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise CustodianError("custody unavailable; reconciliation required") from exc
        return cls(directory, conn, key, signing)

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            self._closed = True

    def _load(self, registry: str) -> dict[str, Any]:
        _identifier(registry)
        row = self._conn.execute("SELECT state FROM registry WHERE id=?", (registry,)).fetchone()
        if row is None:
            raise CustodianError("registry is not enrolled")
        result: dict[str, Any] = json.loads(row[0])
        return result

    def _save(self, registry: str, state: dict[str, Any]) -> None:
        # Do not apply the individual wire payload bound to the aggregate history.
        self._conn.execute(
            "UPDATE registry SET state=? WHERE id=?",
            (json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False), registry),
        )

    @staticmethod
    def _head(state: dict[str, Any]) -> dict[str, Any]:
        last = state["records"][-1]
        return {
            "registry_id": last["registry_id"],
            "epoch": last["epoch"],
            "head_sequence": last["sequence"],
            "head_mac": last["mac"],
            "policy_digest": state["policy_digest"],
            "fence_generation": state["generation"],
            "pending_record_id": state["pending"]["record_id"] if state["pending"] else None,
        }

    def _record(
        self,
        registry: str,
        epoch: int,
        sequence: int,
        previous: str,
        record_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "registry_id": registry,
            "epoch": epoch,
            "sequence": sequence,
            "record_id": record_id,
            "kind": kind,
            "payload": payload,
            "payload_digest": hashlib.sha256(_bytes(payload)).hexdigest(),
            "prev_mac": previous,
        }
        record["mac"] = hmac.new(self._key, DOMAIN + _bytes(record), hashlib.sha256).hexdigest()
        return record

    def enroll(self, registry: str, policy: dict[str, Any], *, actor: str, reviewed: bool) -> None:
        _identifier(registry)
        _identifier(actor)
        if reviewed is not True:
            raise CustodianError("explicit reviewed enrollment required")
        self._validate_payload("policy_snapshot", policy)
        genesis = self._record(
            registry,
            1,
            1,
            "0" * 64,
            "genesis",
            "genesis",
            {"policy": policy, "actor": actor, "historical_provenance": "operator-reviewed-current-state"},
        )
        state: dict[str, Any] = {
            "records": [genesis],
            "pending": None,
            "generation": 0,
            "attempts": {},
            "active": None,
            "policy_digest": hashlib.sha256(_bytes(policy)).hexdigest(),
        }
        try:
            with self._conn:
                self._conn.execute("INSERT INTO registry VALUES (?,?)", (registry, json.dumps(state)))
        except sqlite3.IntegrityError as exc:
            raise CustodianError("registry already enrolled") from exc

    def read_current(self, registry: str) -> dict[str, Any]:
        return self._head(self._load(registry))

    def register_fence(
        self, registry: str, *, predecessor: dict[str, Any], attempt_id: str, holder: str, local_token: int
    ) -> dict[str, Any]:
        _identifier(attempt_id)
        _identifier(holder)
        if type(local_token) is not int or local_token < 1:
            raise CustodianError("invalid lease binding")
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            state = self._load(registry)
            binding = {
                "attempt_id": attempt_id,
                "holder": holder,
                "local_token": local_token,
                "predecessor": {k: predecessor.get(k) for k in PREDECESSOR_FIELDS},
            }
            previous = state["attempts"].get(attempt_id)
            if previous is not None:
                if previous != state["active"] or not _equal(previous["binding"], binding):
                    raise CustodianError("superseded or conflicting fence attempt")
                return previous
            if not _match(predecessor, self._head(state), PREDECESSOR_FIELDS):
                raise CustodianError("stale fence predecessor")
            state["generation"] += 1
            fence = {"generation": state["generation"], "binding": binding}
            state["attempts"][attempt_id] = fence
            state["active"] = fence
            self._save(registry, state)
            return fence

    @staticmethod
    def _assert_fence(state: dict[str, Any], fence: dict[str, Any]) -> None:
        if state["active"] is None or not _equal(state["active"], fence):
            raise CustodianError("stale writer fence")

    @staticmethod
    def _validate_payload(kind: str, payload: dict[str, Any]) -> None:
        # Reuse the existing frozen domain parsers, with no file reads/defaults.
        from magicite.core.trust import DecisionKind, SourceChannel, TrustDecision, TrustPolicy

        if not isinstance(payload, dict):
            raise CustodianError("invalid record payload")
        _bytes(payload)
        try:
            if kind == "policy_snapshot":
                if payload.get("schema") != "TrustPolicy/1":
                    raise CustodianError("invalid policy schema")
                _identifier(payload.get("policy_id"))
                if type(payload.get("revision")) is not int or payload["revision"] < 1:
                    raise CustodianError("invalid policy revision")
                if not isinstance(payload.get("roots"), list):
                    raise CustodianError("invalid policy roots")
                for root in payload["roots"]:
                    if (
                        type(root.get("revoked")) is not bool
                        or len(bytes.fromhex(root["public_key_hex"])) != 32
                    ):
                        raise CustodianError("invalid policy root")
                parsed = TrustPolicy.from_dict(payload).to_dict()
            elif kind == "trust_decision":
                if payload.get("schema") != "TrustDecision/1":
                    raise CustodianError("invalid decision schema")
                if payload.get("decision") not in get_args(DecisionKind) or payload.get(
                    "source_channel"
                ) not in get_args(SourceChannel):
                    raise CustodianError("invalid decision domain")
                for name in (
                    "decision_id",
                    "engram_id",
                    "policy_id",
                    "actor",
                    "timestamp",
                    "scanner_revision",
                ):
                    _identifier(payload.get(name))
                if type(payload.get("policy_revision")) is not int or payload["policy_revision"] < 1:
                    raise CustodianError("invalid decision policy revision")
                for name in ("content_digest", "policy_digest", "resource_digest", "signer_fingerprint"):
                    value = payload.get(name)
                    if value is None and name in {"resource_digest", "signer_fingerprint"}:
                        continue
                    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                        raise CustodianError("invalid decision digest")
                if (
                    payload.get("signature_valid") is not None
                    and type(payload["signature_valid"]) is not bool
                ):
                    raise CustodianError("invalid signature status")
                parsed = TrustDecision.from_dict(payload).to_dict()
            else:
                raise CustodianError("unsupported record kind")
            if not _equal(parsed, payload):
                raise CustodianError("noncanonical record payload")
        except (KeyError, ValueError, TypeError) as exc:
            raise CustodianError("invalid record payload") from exc

    def prepare_record(
        self,
        registry: str,
        *,
        fence: dict[str, Any],
        expected_head: dict[str, Any],
        record_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _identifier(record_id)
        self._validate_payload(kind, payload)
        if kind == "trust_decision" and record_id != payload["decision_id"]:
            raise CustodianError("decision identity must equal immutable journal identity")
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            state = self._load(registry)
            self._assert_fence(state, fence)
            for existing in [*state["records"], state["pending"]]:
                if existing and existing["record_id"] == record_id:
                    if existing["kind"] == kind and _equal(existing["payload"], payload):
                        return existing
                    raise CustodianError("conflicting immutable record identity")
            if state["pending"] is not None:
                raise CustodianError("pending record requires reconciliation")
            head = self._head(state)
            if not _match(expected_head, head, HEAD_FIELDS):
                raise CustodianError("stale preparation head")
            record = self._record(
                registry, head["epoch"], head["head_sequence"] + 1, head["head_mac"], record_id, kind, payload
            )
            state["pending"] = record
            self._save(registry, state)
            return record

    def commit_record(
        self, registry: str, *, fence: dict[str, Any], expected_head: dict[str, Any], record: dict[str, Any]
    ) -> dict[str, Any]:
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            state = self._load(registry)
            self._assert_fence(state, fence)
            if _equal(record, state["records"][-1]):
                return self._head(state)
            if not _match(expected_head, self._head(state), HEAD_FIELDS):
                raise CustodianError("stale commit head")
            if state["pending"] is None or not _equal(record, state["pending"]):
                raise CustodianError("record does not match durable preparation")
            state["records"].append(state["pending"])
            if record["kind"] == "policy_snapshot":
                state["policy_digest"] = record["payload_digest"]
            state["pending"] = None
            self._save(registry, state)
            return self._head(state)

    def committed_records(self, registry: str) -> list[dict[str, Any]]:
        return list(self._load(registry)["records"])

    def prepared_record(self, registry: str) -> dict[str, Any] | None:
        pending: dict[str, Any] | None = self._load(registry)["pending"]
        return pending
