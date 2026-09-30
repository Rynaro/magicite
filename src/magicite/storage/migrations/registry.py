"""Central migration-number authority (C8 / S03).

Parallel slices MUST NOT invent migration numbers on their own. They publish
a provisional name here; numbers are assigned in **merge order** and must
stay contiguous among ``shipped`` entries (MAX_KNOWN_SCHEMA_VERSION == N
with files ``001_…`` … ``00N_…`` and no gaps).

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
#: promoted to ``shipped`` at merge time (contiguous with prior shipped).
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
        stem="fulltext_index",
        owner_slice="S05",
        status="shipped",
        purpose="Generation-scoped FTS5 / projection entry tables (S05)",
    ),
    MigrationAllocation(
        number=5,
        stem="bundle_trust",
        owner_slice="S04",
        status="shipped",
        purpose="Local trust/admission cache tables (authoritative ledger is .magicite/trust/)",
    ),
    MigrationAllocation(
        number=6,
        stem="evidence_ledger",
        owner_slice="S09",
        status="shipped",
        purpose="Evidence ledger file-domain projection tables (authoritative store is <data_dir>/evidence/)",
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
#: ``backup/1`` is the S12 multi-domain operator backup (distinct from S03's
#: ``migration_backup/1`` pre-upgrade snapshot). Slot 7 ``backup_recovery``
#: remains provisional — S12 uses file-domain authority (like S04/S09).
SUPPORTED_BACKUP_MANIFEST_KINDS: frozenset[str] = frozenset(
    {"migration_backup/1", "backup/1"}
)

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
