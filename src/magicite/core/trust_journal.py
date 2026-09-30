"""Local journal snapshots verified against fresh independent custody.

The remote head and immutable records are authenticated by CustodianClient.
Local files are evidence to compare, never an alternate authority. Missing or
changed history closes reads until explicit reconciliation under a writer fence.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from magicite.core.trust_custodian import HEAD_FIELDS, CustodianError, _bytes, _equal, _match


class Custody(Protocol):
    def call(self, operation: str, **arguments: Any) -> Any: ...


@dataclass(frozen=True)
class TrustSnapshot:
    head: dict[str, Any]
    policy: dict[str, Any]
    decisions: tuple[dict[str, Any], ...]
    latest_by_engram: dict[str, dict[str, Any]]
    records: tuple[dict[str, Any], ...]


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TrustJournal:
    def __init__(self, directory: Path, registry_id: str, client: Custody):
        self.directory, self.registry_id, self.client = directory, registry_id, client
        self.journal_path = directory / "journal.jsonl"
        self.head_path = directory / "head.json"

    def _remote(self, *, allow_pending: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        head = self.client.call("read_current")
        if head["registry_id"] != self.registry_id:
            raise CustodianError("custody enrollment mismatch")
        if head["pending_record_id"] is not None and not allow_pending:
            raise CustodianError("pending trust record requires reconciliation")
        records = self.client.call("committed_records")
        after = self.client.call("read_current")
        if not _equal(head, after):
            raise CustodianError("trust head changed during snapshot")
        if not records or len(records) != head["head_sequence"]:
            raise CustodianError("incomplete authenticated history")
        previous = "0" * 64
        ids: set[str] = set()
        for sequence, record in enumerate(records, 1):
            if (
                record["registry_id"] != self.registry_id
                or record["sequence"] != sequence
                or record["prev_mac"] != previous
                or record["record_id"] in ids
            ):
                raise CustodianError("invalid authenticated history")
            previous = record["mac"]
            ids.add(record["record_id"])
        if previous != head["head_mac"]:
            raise CustodianError("authenticated history/head mismatch")
        return head, records

    def _write_head(self, head: dict[str, Any]) -> None:
        temporary = self.head_path.with_suffix(".pending")
        with temporary.open("wb") as stream:
            stream.write(_bytes({key: head[key] for key in (*HEAD_FIELDS, "policy_digest")}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.head_path)
        _sync_directory(self.directory)

    def initialize_reviewed_genesis(self) -> None:
        """Explicit operator step AFTER protected enrollment; never used by reads."""
        head, records = self._remote()
        if head["head_sequence"] != 1 or records[0]["kind"] != "genesis":
            raise CustodianError("existing history requires explicit recovery")
        if self.journal_path.exists() or self.head_path.exists():
            raise CustodianError("local trust history already initialized")
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("xb") as stream:
            stream.write(_bytes(records[0]) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(self.directory.parent)
        self._write_head(head)

    def snapshot(self) -> TrustSnapshot:
        try:
            head, authenticated = self._remote()
            with self.journal_path.open("rb") as stream:
                records = [json.loads(line) for line in stream]
            local_head = json.loads(self.head_path.read_bytes())
            if not _match(local_head, head, (*HEAD_FIELDS, "policy_digest")):
                raise CustodianError("local trust head does not match custody")
            if len(records) != len(authenticated) or any(
                not _equal(local, remote) for local, remote in zip(records, authenticated, strict=True)
            ):
                raise CustodianError("local trust history does not match custody")
            policy = records[0]["payload"]["policy"]
            decisions: list[dict[str, Any]] = []
            latest: dict[str, dict[str, Any]] = {}
            for record in records[1:]:
                if record["kind"] == "policy_snapshot":
                    policy = record["payload"]
                elif record["kind"] == "trust_decision":
                    decision = record["payload"]
                    decisions.append(decision)
                    latest[decision["engram_id"]] = decision
                else:
                    raise CustodianError("unsupported authenticated journal kind")
            import hashlib

            if hashlib.sha256(_bytes(policy)).hexdigest() != head["policy_digest"]:
                raise CustodianError("snapshot policy commitment mismatch")
            return TrustSnapshot(head, policy, tuple(decisions), latest, tuple(records))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CustodianError("trust history requires reconciliation") from exc

    def append(
        self,
        *,
        record_id: str,
        kind: str,
        payload: dict[str, Any],
        fence: dict[str, Any],
        assert_owned: Callable[[], None],
    ) -> TrustSnapshot:
        before = self.snapshot()
        assert_owned()
        record = self.client.call(
            "prepare_record",
            fence=fence,
            expected_head=before.head,
            record_id=record_id,
            kind=kind,
            payload=payload,
        )
        # An exact committed retry retains its original sequence and cannot
        # append after a later revoke. The caller receives the current snapshot.
        if record["sequence"] <= before.head["head_sequence"]:
            if not any(_equal(record, existing) for existing in before.records):
                raise CustodianError("conflicting retry history")
            return before
        assert_owned()
        with self.journal_path.open("ab") as stream:
            stream.write(_bytes(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        assert_owned()
        head = self.client.call("commit_record", fence=fence, expected_head=before.head, record=record)
        assert_owned()
        self._write_head(head)
        assert_owned()
        return self.snapshot()

    def reconcile(self, *, fence: dict[str, Any], assert_owned: Callable[[], None]) -> TrustSnapshot:
        """Explicit recovery using retained authenticated custody history only.

        Missing custody history remains closed. No caller-supplied overlay or
        unsigned mirror can become a journal record through recovery.
        """
        assert_owned()
        head, records = self._remote(allow_pending=True)
        pending = self.client.call("prepared_record")
        if (pending is None) != (head["pending_record_id"] is None):
            raise CustodianError("preparation changed during recovery")
        if pending is not None:
            if (
                pending["record_id"] != head["pending_record_id"]
                or pending["prev_mac"] != head["head_mac"]
                or pending["sequence"] != head["head_sequence"] + 1
                or pending["registry_id"] != self.registry_id
            ):
                raise CustodianError("invalid retained preparation")
            records.append(pending)
        temporary = self.journal_path.with_suffix(".recovery")
        self.directory.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            for record in records:
                stream.write(_bytes(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        assert_owned()
        os.replace(temporary, self.journal_path)
        _sync_directory(self.directory)
        if pending is not None:
            assert_owned()
            head = self.client.call("commit_record", fence=fence, expected_head=head, record=pending)
        assert_owned()
        self._write_head(head)
        assert_owned()
        return self.snapshot()
