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
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
    source_signers: dict[tuple[str, str], frozenset[str]]


_AUTHORED_TRANSFORMS = frozenset(
    {"magicite-authored-edit/1", "magicite-dream-checkpoint/1", "magicite-archive/1"}
)
# Everything that pins history content; fence_generation advances per writer
# registration without changing any record.
_HISTORY_FIELDS = (*HEAD_FIELDS, "policy_digest", "pending_record_id", "legacy_reconciliation")
_Key = tuple[str, str]
_FileIdentity = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class _Replay:
    """Authenticated journal fold; ``extended`` never mutates its receiver."""

    registry_id: str
    policy: dict[str, Any]
    decisions: list[dict[str, Any]]
    latest: dict[str, dict[str, Any]]
    source_signers: dict[_Key, frozenset[str]]
    decision_ids: dict[str, dict[str, Any]]
    transformed_targets: frozenset[_Key]
    descendants: dict[_Key, frozenset[_Key]]
    records: tuple[dict[str, Any], ...]

    @classmethod
    def start(cls, registry_id: str, genesis: dict[str, Any]) -> _Replay:
        return cls(registry_id, genesis["payload"]["policy"], [], {}, {}, {}, frozenset(), {}, (genesis,))

    def extended(self, records: list[dict[str, Any]]) -> _Replay:
        policy = self.policy
        decisions = list(self.decisions)
        latest = dict(self.latest)
        source_signers = dict(self.source_signers)
        decision_ids = dict(self.decision_ids)
        transformed_targets = set(self.transformed_targets)
        descendants = dict(self.descendants)
        touched: set[_Key] = set()
        for record in records:
            if record["kind"] == "policy_snapshot":
                policy = record["payload"]
            elif record["kind"] == "trust_decision":
                decision = record["payload"]
                decisions.append(decision)
                latest[decision["engram_id"]] = decision
                decision_ids[decision["decision_id"]] = decision
                if decision.get("signature_valid") is True and decision.get("signer_fingerprint"):
                    key = (decision["engram_id"], decision["content_digest"])
                    source_signers[key] = source_signers.get(key, frozenset()) | {
                        decision["signer_fingerprint"]
                    }
                    touched.add(key)
            elif record["kind"] == "legacy_reconciliation":
                # Custody enforces ordered non-admitting plan completion;
                # the authenticated current head gates ordinary readers.
                continue
            elif record["kind"] == "epoch_transition":
                intent = record["payload"]
                if (
                    intent["schema"] != "EpochTransitionIntent/1"
                    or intent["registry_id"] != self.registry_id
                    or intent["new_epoch"] != record["epoch"]
                    or intent["old_epoch"] + 1 != intent["new_epoch"]
                    or intent["old_head"]["head_mac"] != record["prev_mac"]
                    or intent["old_head"]["head_sequence"] + 1 != record["sequence"]
                    or intent["policy_digest"] != hashlib.sha256(_bytes(policy)).hexdigest()
                ):
                    raise CustodianError("invalid authenticated epoch continuity")
                # Rotation preserves policy and every ordered decision.
            elif record["kind"] == "artifact_transform":
                lineage = record["payload"]
                source_key = (lineage["engram_id"], lineage["source_digest"])
                target_key = (lineage["engram_id"], lineage["target_digest"])
                descendants[source_key] = descendants.get(source_key, frozenset()) | {target_key}
                if lineage["transform_id"] in _AUTHORED_TRANSFORMS and source_key not in transformed_targets:
                    raise CustodianError("authored lineage source is missing")
                signers = set(source_signers.get(source_key, frozenset()))
                for identity in lineage["source_decision_ids"]:
                    original = decision_ids.get(identity)
                    if (
                        original is None
                        or original["engram_id"] != source_key[0]
                        or original["content_digest"] != source_key[1]
                    ):
                        raise CustodianError("invalid source decision reference")
                    if original.get("signature_valid") is True and original.get("signer_fingerprint"):
                        signers.add(original["signer_fingerprint"])
                legacy = lineage.get("legacy_provenance")
                if legacy is not None:
                    # Unsigned historical claims can restrict, never verify
                    # a publisher or grant transformed-target admission.
                    signers.update(
                        original["signer_fingerprint"]
                        for original in legacy["original_decisions"]
                        if original.get("signer_fingerprint")
                    )
                provenance = lineage["signature_provenance"]["source"]
                if provenance is not None and provenance["signature_valid"] is True:
                    signers.add(provenance["signer_fingerprint"])
                source_signers[target_key] = source_signers.get(target_key, frozenset()) | frozenset(signers)
                transformed_targets.add(target_key)
                touched.update((source_key, target_key))
                # Lineage grants nothing and cannot clear prior restrictions.
            else:
                raise CustodianError("unsupported authenticated journal kind")
        # A later verified import can establish source provenance for an
        # already-authored descendant. Propagate restrictions to a fixed
        # point; never propagate admission or publisher-validity flags. The
        # receiver is already a fixed point, so every edge that can now be
        # violated starts at a touched key.
        pending = deque(key for key in touched if source_signers.get(key))
        while pending:
            source_key = pending.popleft()
            for target_key in descendants.get(source_key, frozenset()):
                old = source_signers.get(target_key, frozenset())
                merged = old | source_signers[source_key]
                if merged != old:
                    source_signers[target_key] = merged
                    pending.append(target_key)
        return _Replay(
            self.registry_id,
            policy,
            decisions,
            latest,
            source_signers,
            decision_ids,
            frozenset(transformed_targets),
            descendants,
            self.records + tuple(records),
        )

    def snapshot(self, head: dict[str, Any]) -> TrustSnapshot:
        if hashlib.sha256(_bytes(self.policy)).hexdigest() != head["policy_digest"]:
            raise CustodianError("snapshot policy commitment mismatch")
        return TrustSnapshot(
            head, self.policy, tuple(self.decisions), self.latest, self.records, self.source_signers
        )


@dataclass(frozen=True)
class _VerifiedSnapshot:
    head: dict[str, Any]
    journal_identity: _FileIdentity
    head_bytes: bytes
    replay: _Replay
    snapshot: TrustSnapshot


_VERIFIED_SNAPSHOTS: dict[tuple[str, str, str | None], _VerifiedSnapshot] = {}


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


def _identity(info: os.stat_result) -> _FileIdentity:
    # ctime is included because utime() can restore mtime but never ctime.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _safe_identity(info: os.stat_result) -> _FileIdentity:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CustodianError("unsafe journal file")
    return _identity(info)


def _read_file_identity(directory: int, name: str) -> tuple[bytes, _FileIdentity]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        identity = _safe_identity(os.fstat(stream.fileno()))
        content = stream.read()
        if _identity(os.fstat(stream.fileno())) != identity:
            raise CustodianError("journal file changed during read")
        return content, identity


def _read_file(directory: int, name: str) -> bytes:
    return _read_file_identity(directory, name)[0]


def _append_file(
    directory: int,
    name: str,
    content: bytes,
    expected: _FileIdentity,
    assert_owned: Callable[[], None],
) -> _FileIdentity:
    """Append only to the exact inode last verified against custody."""
    descriptor = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=directory)
    try:
        if _safe_identity(os.fstat(descriptor)) != expected:
            raise CustodianError("local trust history changed since verification")
        assert_owned()
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
        return _identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)


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
    def __init__(
        self, directory: Path, registry_id: str, client: Custody, *, reconciliation_id: str | None = None
    ):
        self.directory, self.registry_id, self.client = directory, registry_id, client
        self.reconciliation_id = reconciliation_id
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

    def _cache_key(self) -> tuple[str, str, str | None]:
        return (str(self.directory.absolute()), self.registry_id, self.reconciliation_id)

    def snapshot(self) -> TrustSnapshot:
        # Every read takes a fresh custody head and re-checks local state; only
        # a byte-identical head (sequence, MAC, epoch, policy, gate) over an
        # unchanged journal inode and identical head bytes reuses the replay
        # verified at that head. Anything else re-verifies all history.
        key = self._cache_key()
        try:
            cached = _VERIFIED_SNAPSHOTS.get(key)
            if cached is not None:
                head = self.client.call("read_current")
                if set(head) == set(cached.head) and _match(head, cached.head, _HISTORY_FIELDS):
                    with _directory_fd(self.directory) as directory:
                        identity = _safe_identity(
                            os.stat("journal.jsonl", dir_fd=directory, follow_symlinks=False)
                        )
                        head_bytes = _read_file(directory, "head.json")
                    if identity == cached.journal_identity and head_bytes == cached.head_bytes:
                        if not _equal(head, cached.head):
                            cached = replace(cached, head=head, snapshot=replace(cached.snapshot, head=head))
                            _VERIFIED_SNAPSHOTS[key] = cached
                        return cached.snapshot
            _VERIFIED_SNAPSHOTS.pop(key, None)
            entry = self._verified_entry()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _VERIFIED_SNAPSHOTS.pop(key, None)
            if isinstance(exc, CustodianError):
                raise
            raise CustodianError("trust history requires reconciliation") from exc
        _VERIFIED_SNAPSHOTS[key] = entry
        return entry.snapshot

    def _verified_entry(self) -> _VerifiedSnapshot:
        try:
            head, authenticated = self._remote()
            gate = head.get("legacy_reconciliation")
            if gate is not None and gate["migration_id"] != self.reconciliation_id:
                raise CustodianError("legacy reconciliation remains incomplete")
            with _directory_fd(self.directory) as directory:
                journal_bytes, identity = _read_file_identity(directory, "journal.jsonl")
                head_bytes = _read_file(directory, "head.json")
            records = [json.loads(line) for line in journal_bytes.splitlines()]
            if not _match(json.loads(head_bytes), head, (*HEAD_FIELDS, "policy_digest")):
                raise CustodianError("local trust head does not match custody")
            if len(records) != len(authenticated) or any(
                not _equal(local, remote) for local, remote in zip(records, authenticated, strict=True)
            ):
                raise CustodianError("local trust history does not match custody")
            replay = _Replay.start(self.registry_id, records[0]).extended(records[1:])
            return _VerifiedSnapshot(head, identity, head_bytes, replay, replay.snapshot(head))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, CustodianError):
                raise
            raise CustodianError("trust history requires reconciliation") from exc

    def _advance(
        self,
        prior: _VerifiedSnapshot,
        record: dict[str, Any],
        head: dict[str, Any],
        identity: _FileIdentity,
    ) -> None:
        """Extend the verified replay by the one record custody just committed.

        Any inconsistency only discards the cache, so the next read re-verifies
        all history and fails closed there.
        """
        key = self._cache_key()
        _VERIFIED_SNAPSHOTS.pop(key, None)
        try:
            gate = head.get("legacy_reconciliation")
            if (
                record["registry_id"] != self.registry_id
                or record["sequence"] != prior.head["head_sequence"] + 1
                or record["prev_mac"] != prior.head["head_mac"]
                or head["registry_id"] != self.registry_id
                or head["epoch"] != record["epoch"]
                or head["head_sequence"] != record["sequence"]
                or head["head_mac"] != record["mac"]
                or head["pending_record_id"] is not None
                or (gate is not None and gate["migration_id"] != self.reconciliation_id)
            ):
                return
            replay = prior.replay.extended([record])
            with _directory_fd(self.directory) as directory:
                head_bytes = _read_file(directory, "head.json")
            if not _match(json.loads(head_bytes), head, (*HEAD_FIELDS, "policy_digest")):
                return
            entry = _VerifiedSnapshot(head, identity, head_bytes, replay, replay.snapshot(head))
        except (OSError, ValueError, KeyError, TypeError):
            return
        _VERIFIED_SNAPSHOTS[key] = entry

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
        prior = _VERIFIED_SNAPSHOTS.get(self._cache_key())
        if prior is not None and prior.snapshot is not before:
            prior = None
        identity: _FileIdentity | None = None
        with _directory_fd(self.directory) as directory:
            if prior is not None:
                identity = _append_file(
                    directory, "journal.jsonl", _bytes(record) + b"\n", prior.journal_identity, assert_owned
                )
            else:
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
        if prior is not None and identity is not None:
            self._advance(prior, record, head, identity)
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
