"""Explicit registry/custody binding for existing local writer leases.

No authority is inferred from mutable registry files or lock filenames. The
operator profile is in a fixed, protected OS configuration directory. Tests
inject the resolver explicitly; no environment-selected alternate provider.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core.trust_custodian import CustodianError
from magicite.core.trust_custodian_transport import CustodianClient, CustodyProfile
from magicite.core.trust_journal import Custody, TrustJournal
from magicite.storage import lease


def protected_profile_path(cfg: Config) -> Path:
    identity = hashlib.sha256(str(cfg.project_root.resolve()).encode()).hexdigest()
    # Resolve only the fixed OS-owned configuration prefix (macOS /etc alias).
    return Path("/etc/magicite/registries").resolve() / f"{identity}.json"


def resolve_custody(cfg: Config) -> tuple[str, Custody]:
    try:
        profile = CustodyProfile.from_enrollment(protected_profile_path(cfg), project_root=cfg.project_root)
        return profile.registry_id, CustodianClient(profile)
    except (OSError, ValueError) as exc:
        raise CustodianError("protected custody enrollment required") from exc


def preflight_custody(cfg: Config) -> None:
    """Authenticate availability before an ordinary entrypoint creates local state."""
    registry_id, client = resolve_custody(cfg)
    head = client.call("read_current")
    if head.get("registry_id") != registry_id or head.get("legacy_reconciliation") is not None:
        raise CustodianError("custody reconciliation required before ordinary startup")


class RegistryCustodyCoordinator:
    def __init__(self, registry_id: str, client: Custody):
        self.registry_id, self.client = registry_id, client
        self.predecessor: dict[str, Any] | None = None
        self.attempt_id: str | None = None
        self.fence: dict[str, Any] | None = None

    def capture(self) -> None:
        self.predecessor = self.client.call("read_current")
        if self.predecessor["registry_id"] != self.registry_id:
            raise CustodianError("custody enrollment mismatch")
        self.attempt_id = secrets.token_hex(32)
        self.fence = None

    def register(self, holder: str, local_token: int) -> None:
        if self.predecessor is None or self.attempt_id is None:
            raise CustodianError("custody predecessor was not captured before acquisition")
        self.fence = self.client.call(
            "register_fence",
            predecessor=self.predecessor,
            attempt_id=self.attempt_id,
            holder=holder,
            local_token=local_token,
        )


def registry_writer_lease(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    holder: str | None = None,
    ttl_s: float = lease.DEFAULT_LEASE_TTL_S,
    heartbeat_interval_s: float = lease.DEFAULT_HEARTBEAT_INTERVAL_S,
) -> lease.CrossProcessLease:
    current = lease.current_cross_process_lease()
    if current is not None:
        lease.require_cross_process_scope(cfg.dream_lock_path)
        if not isinstance(current.custody, RegistryCustodyCoordinator):
            raise CustodianError("existing writer lease lacks registry custody")
        coordinator = current.custody
    else:
        registry_id, client = resolve_custody(cfg)
        coordinator = RegistryCustodyCoordinator(registry_id, client)
    return _bound_lease(
        cfg, conn, coordinator, holder=holder, ttl_s=ttl_s, heartbeat_interval_s=heartbeat_interval_s
    )


def _bound_lease(
    cfg: Config,
    conn: sqlite3.Connection,
    coordinator: RegistryCustodyCoordinator,
    *,
    holder: str | None = None,
    ttl_s: float = lease.DEFAULT_LEASE_TTL_S,
    heartbeat_interval_s: float = lease.DEFAULT_HEARTBEAT_INTERVAL_S,
) -> lease.CrossProcessLease:
    return lease.CrossProcessLease(
        lock_path=cfg.dream_lock_path,
        conn=conn,
        holder=holder,
        ttl_s=ttl_s,
        heartbeat_interval_s=heartbeat_interval_s,
        custody=coordinator,
    )


def journal_for(cfg: Config) -> TrustJournal:
    registry_id, client = resolve_custody(cfg)
    return TrustJournal(cfg.data_dir / "trust" / "authority", registry_id, client)


def bound_journal(cfg: Config) -> tuple[TrustJournal, lease.CrossProcessLease, dict[str, Any]]:
    lease.require_cross_process_scope(cfg.dream_lock_path)
    current = lease.current_cross_process_lease()
    if current is None or not isinstance(current.custody, RegistryCustodyCoordinator):
        raise CustodianError("writer lease lacks enrolled custody")
    coordinator = current.custody
    if coordinator.fence is None:
        raise CustodianError("writer custody registration is incomplete")
    journal = TrustJournal(cfg.data_dir / "trust" / "authority", coordinator.registry_id, coordinator.client)
    return journal, current, coordinator.fence


class RotationCoordinator(RegistryCustodyCoordinator):
    def __init__(self, registry_id: str, client: Custody, transition_id: str):
        super().__init__(registry_id, client)
        self.transition_id = transition_id
        self.pending = False

    def capture(self) -> None:
        try:
            status = self.client.call("rotation_status", transition_id=self.transition_id)
            self.predecessor = status["head"]
            self.pending = status["phase"] != "FINISHED"
        except CustodianError:
            # Only an authenticated ordinary head can establish absence of
            # maintenance; unavailable custody still closes before acquisition.
            self.predecessor = self.client.call("read_current")
            self.pending = False
        if self.predecessor["registry_id"] != self.registry_id:
            raise CustodianError("rotation enrollment mismatch")
        self.attempt_id = secrets.token_hex(32)
        self.fence = None

    def register(self, holder: str, local_token: int) -> None:
        if self.predecessor is None or self.attempt_id is None:
            raise CustodianError("rotation predecessor not captured")
        args: dict[str, Any] = dict(
            predecessor=self.predecessor,
            attempt_id=self.attempt_id,
            holder=holder,
            local_token=local_token,
        )
        operation = "register_fence"
        if self.pending:
            operation = "rotate_register"
            args["transition_id"] = self.transition_id
        self.fence = self.client.call(operation, **args)


def rotation_writer_lease(
    cfg: Config, conn: sqlite3.Connection, *, client: Custody, registry_id: str, transition_id: str
) -> lease.CrossProcessLease:
    if lease.current_cross_process_lease() is not None:
        raise CustodianError("rotation requires its own existing-lease acquisition")
    return _bound_lease(cfg, conn, RotationCoordinator(registry_id, client, transition_id))
