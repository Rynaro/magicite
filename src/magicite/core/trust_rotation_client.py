"""Explicit operator rotation using the existing local lease and protected custody."""

from __future__ import annotations

import json
from typing import Any

from magicite.core.trust_custodian import CustodianError, _bytes, _equal
from magicite.core.trust_journal import TrustJournal, _directory_fd, _read_file, _replace_file
from magicite.core.trust_rotation import verify_transition_record


class RotationHistory:
    """Head-pinned maintenance reads, never an admission provider."""

    def __init__(self, client: Any, transition_id: str):
        self.client, self.transition_id = client, transition_id

    def call(self, operation: str, **arguments: Any) -> Any:
        if operation == "read_current":
            return self.client.call("rotation_status", transition_id=self.transition_id)["head"]
        if operation == "history_page":
            return self.client.call("rotate_history_page", transition_id=self.transition_id, **arguments)
        raise CustodianError("unsupported maintenance history operation")


def rotate_registry(
    cfg: Any, conn: Any, client: Any, *, registry_id: str, transition_id: str, actor: str
) -> dict[str, Any]:
    from magicite.core.writer_guard import RotationCoordinator, rotation_writer_lease

    guard = rotation_writer_lease(
        cfg, conn, client=client, registry_id=registry_id, transition_id=transition_id
    )
    with guard.acquire():
        held = guard
        coordinator = held.custody
        if not isinstance(coordinator, RotationCoordinator) or coordinator.fence is None:
            raise CustodianError("rotation custody binding unavailable")
        try:
            status = client.call("rotation_status", transition_id=transition_id)
        except CustodianError:
            head = client.call("read_current")
            held.assert_owned()
            status = client.call(
                "rotate_prepare",
                fence=coordinator.fence,
                expected_head=head,
                transition_id=transition_id,
                actor=actor,
            )
        held.assert_owned()
        certificate, record = status["transition"], status["record"]
        verify_transition_record(certificate, record)
        if certificate["body"]["transition_id"] != transition_id:
            raise CustodianError("rotation identity mismatch")
        journal = TrustJournal(cfg.data_dir / "trust" / "authority", registry_id, client)
        if status["phase"] != "FINISHED":
            maintenance = TrustJournal(journal.directory, registry_id, RotationHistory(client, transition_id))
            head, records = maintenance._remote()
            if status["phase"] == "PREPARED":
                if record["prev_mac"] != head["head_mac"] or record["sequence"] != len(records) + 1:
                    raise CustodianError("rotation preparation head changed")
                records.append(record)
            elif not records or not _equal(records[-1], record):
                raise CustodianError("committed rotation history changed")
            held.assert_owned()
            with _directory_fd(journal.directory) as directory:
                local = [json.loads(line) for line in _read_file(directory, "journal.jsonl").splitlines()]
                if len(local) > len(records) or any(
                    not _equal(a, b) for a, b in zip(local, records, strict=False)
                ):
                    raise CustodianError("conflicting local rotation suffix")
                _replace_file(
                    directory,
                    "journal.jsonl",
                    b"".join(_bytes(row) + b"\n" for row in records),
                    held.assert_owned,
                )
            held.assert_owned()
            if status["phase"] == "PREPARED":
                client.call(
                    "rotate_commit",
                    transition_id=transition_id,
                    fence=coordinator.fence,
                    expected_head=certificate["body"]["old_head"],
                )
            held.assert_owned()
            # This fresh-nonce COMMITTED response is verified with the new pin
            # by the maintenance client before asking custody to reopen.
            proof = client.call("rotation_status", transition_id=transition_id)
            if proof["phase"] != "COMMITTED" or not _equal(proof["transition"], certificate):
                raise CustodianError("rotation new-key proof mismatch")
            held.assert_owned()
            client.call("rotate_finish", transition_id=transition_id, expected_head=proof["head"])
        held.assert_owned()
        # Exact custody history now includes the transition. This updates the
        # local head only after active-profile/new-key verification succeeded.
        result = journal.reconcile(fence=coordinator.fence, assert_owned=held.assert_owned)
        held.assert_owned()
        return dict(result.head)
