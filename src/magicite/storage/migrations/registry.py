"""Central migration-number authority (C8 / S03).

Parallel slices MUST NOT invent ``003_*.sql`` (or later) numbers on their
own. They publish a provisional name here; S03 serializes the final
integer at integration time.

``MAX_KNOWN_SCHEMA_VERSION`` is the highest ``PRAGMA user_version`` this
build understands. Opening a DB with a higher version fails closed before
any mutation (C8: newer unreadable schema → actionable error).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MigrationAllocation:
    number: int
    stem: str
    owner_slice: str
    status: str  # "shipped" | "provisional" | "reserved"
    purpose: str


#: Shipped and provisional allocations. Numbers in ``provisional`` /
#: ``reserved`` status are claimed but must not land as ``NNN_*.sql`` until
#: S03 promotes them to ``shipped`` at integration.
ALLOCATIONS: tuple[MigrationAllocation, ...] = (
    MigrationAllocation(
        number=1,
        stem="init",
        owner_slice="baseline",
        status="shipped",
        purpose="Initial durable + ephemeral + operational schema",
    ),
    MigrationAllocation(
        number=2,
        stem="integrity_recovery",
        owner_slice="v0.3",
        status="shipped",
        purpose="Writer fencing token + recoverable idempotency state",
    ),
    MigrationAllocation(
        number=3,
        stem="migration_authority",
        owner_slice="S03",
        status="shipped",
        purpose="Migration ops/journal + index-generation atomic pointers",
    ),
    MigrationAllocation(
        number=4,
        stem="bundle_trust",
        owner_slice="S04",
        status="provisional",
        purpose="Local trust/admission tables (provisional; finalize at S03 integration)",
    ),
    MigrationAllocation(
        number=5,
        stem="fulltext_index",
        owner_slice="S05",
        status="provisional",
        purpose="FTS5 / projection tables beyond index_generation (provisional)",
    ),
    MigrationAllocation(
        number=6,
        stem="evidence_ledger",
        owner_slice="S09",
        status="provisional",
        purpose="Evidence ledger metadata mirrored into SQLite (provisional)",
    ),
    MigrationAllocation(
        number=7,
        stem="backup_recovery",
        owner_slice="S12",
        status="provisional",
        purpose="Doctor/backup recovery overlay bookkeeping (provisional)",
    ),
)

MAX_KNOWN_SCHEMA_VERSION: int = max(a.number for a in ALLOCATIONS if a.status == "shipped")

#: Backup manifest kinds this build can restore. Newer kinds fail closed.
SUPPORTED_BACKUP_MANIFEST_KINDS: frozenset[str] = frozenset({"migration_backup/1"})

#: Engram formats this build can migrate *from* and restore *to*.
SUPPORTED_SOURCE_ENGRAM_FORMATS: frozenset[str] = frozenset({"engram/0.2"})
SUPPORTED_TARGET_ENGRAM_FORMATS: frozenset[str] = frozenset({"engram/1.0"})


def allocation_for(number: int) -> MigrationAllocation | None:
    for item in ALLOCATIONS:
        if item.number == number:
            return item
    return None


def shipped_numbers() -> tuple[int, ...]:
    return tuple(a.number for a in ALLOCATIONS if a.status == "shipped")


def provisional_for_slice(slice_id: str) -> tuple[MigrationAllocation, ...]:
    return tuple(a for a in ALLOCATIONS if a.owner_slice == slice_id and a.status == "provisional")
