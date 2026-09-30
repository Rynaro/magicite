"""Local journal snapshots verified against fresh independent custody.

The remote head and immutable records are authenticated by CustodianClient.
Local files are evidence to compare, never an alternate authority. Missing or
changed history closes reads until explicit reconciliation under a writer fence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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


@contextmanager
def _directory_fd(path: Path, *, create: bool = False) -> Iterator[int]:
    """Walk from root without following any attacker-controlled symlink."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.absolute().parts[1:]:
            if component in {".", ".."}:
                raise CustodianError("invalid journal directory")
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    except OSError as exc:
        raise CustodianError("unsafe or unavailable journal directory") from exc
    finally:
        os.close(descriptor)


def _read_file(directory: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CustodianError("unsafe journal file")
        return stream.read()


def _replace_file(
    directory: int, name: str, content: bytes, assert_owned: Callable[[], None] = lambda: None
) -> None:
    # Replacing a destination symlink is safe: neither it nor a hard-linked
    # prior inode is followed or written. The source is exclusively created.
    temporary = ".trust-" + secrets.token_hex(24)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        assert_owned()
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


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
        deadline = time.monotonic() + 30.0
        raw = bytearray()
        identity = None
        while True:
            if time.monotonic() >= deadline:
                raise CustodianError("history read deadline exceeded")
            page = self.client.call("history_page", expected_head=head, offset=len(raw))
            try:
                chunk = base64.b64decode(page["data"], validate=True)
                integers = ("offset", "next_offset", "total_bytes", "record_count")
                current_identity = (page["total_bytes"], page["record_count"], page["stream_digest"])
                if (
                    not _match(page, head, HEAD_FIELDS)
                    or any(type(page[key]) is not int for key in integers)
                    or page["offset"] != len(raw)
                    or not 0 < len(chunk) <= 1024 * 1024
                    or page["next_offset"] != len(raw) + len(chunk)
                    or page["next_offset"] > page["total_bytes"]
                    or page["record_count"] != head["head_sequence"]
                    or (identity is not None and current_identity != identity)
                ):
                    raise CustodianError("invalid authenticated history page")
                identity = current_identity
                raw.extend(chunk)
                if len(raw) == page["total_bytes"]:
                    if hashlib.sha256(raw).hexdigest() != page["stream_digest"]:
                        raise CustodianError("history stream digest mismatch")
                    records = json.loads(raw)
                    if not isinstance(records, list):
                        raise CustodianError("invalid history stream")
                    break
            except (ValueError, TypeError, KeyError, RecursionError) as exc:
                raise CustodianError("invalid authenticated history page") from exc
        if time.monotonic() >= deadline:
            raise CustodianError("history read deadline exceeded")
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

    def _write_head(self, head: dict[str, Any], assert_owned: Callable[[], None] = lambda: None) -> None:
        with _directory_fd(self.directory) as directory:
            _replace_file(
                directory,
                "head.json",
                _bytes({key: head[key] for key in (*HEAD_FIELDS, "policy_digest")}),
                assert_owned,
            )

    def initialize_reviewed_genesis(self, *, assert_owned: Callable[[], None] = lambda: None) -> None:
        """Explicit operator step AFTER protected enrollment; never used by reads."""
        head, records = self._remote()
        if head["head_sequence"] != 1 or records[0]["kind"] != "genesis":
            raise CustodianError("existing history requires explicit recovery")
        assert_owned()
        with _directory_fd(self.directory, create=True) as directory:
            for name in ("journal.jsonl", "head.json"):
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise CustodianError("local trust history already initialized")
            _replace_file(directory, "journal.jsonl", _bytes(records[0]) + b"\n", assert_owned)
        self._write_head(head, assert_owned)
        assert_owned()

    def snapshot(self) -> TrustSnapshot:
        try:
            head, authenticated = self._remote()
            with _directory_fd(self.directory) as directory:
                records = [json.loads(line) for line in _read_file(directory, "journal.jsonl").splitlines()]
                local_head = json.loads(_read_file(directory, "head.json"))
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
                elif record["kind"] == "artifact_transform":
                    pass  # Lineage grants nothing and cannot clear a prior restriction.
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
            assert_owned()
            return before
        assert_owned()
        with _directory_fd(self.directory) as directory:
            _replace_file(
                directory,
                "journal.jsonl",
                b"".join(_bytes(row) + b"\n" for row in (*before.records, record)),
                assert_owned,
            )
        assert_owned()
        head = self.client.call("commit_record", fence=fence, expected_head=before.head, record=record)
        assert_owned()
        self._write_head(head, assert_owned)
        result = self.snapshot()
        assert_owned()
        return result

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
        with _directory_fd(self.directory, create=True) as directory:
            _replace_file(
                directory,
                "journal.jsonl",
                b"".join(_bytes(record) + b"\n" for record in records),
                assert_owned,
            )
        if pending is not None:
            assert_owned()
            head = self.client.call("commit_record", fence=fence, expected_head=head, record=pending)
        assert_owned()
        self._write_head(head, assert_owned)
        result = self.snapshot()
        assert_owned()
        return result
