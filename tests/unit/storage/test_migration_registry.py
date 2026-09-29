"""Guards for contiguous shipped migration numbering (merge-order policy)."""

from __future__ import annotations

from pathlib import Path

from magicite.storage import db as db_mod
from magicite.storage.migrations import registry


def test_shipped_migrations_are_contiguous_and_match_sql_files() -> None:
    """Shipped files must be exactly 1..N with no gaps; MAX == N; stems match."""
    shipped = [a for a in registry.ALLOCATIONS if a.status == "shipped"]
    numbers = sorted(a.number for a in shipped)
    assert numbers, "expected at least one shipped migration"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"shipped migration numbers must be contiguous 1..N, got {numbers}"
    )
    assert registry.MAX_KNOWN_SCHEMA_VERSION == numbers[-1]
    assert registry.shipped_numbers() == tuple(numbers)

    discovered = db_mod._discover_migrations()
    file_numbers = [n for n, _ in discovered]
    assert file_numbers == numbers, (
        f"SQL migration files {file_numbers} must match shipped registry {numbers}"
    )

    migrations_dir = Path(db_mod._migrations_dir())
    by_number = {a.number: a for a in shipped}
    for number, path in discovered:
        alloc = by_number[number]
        expected_name = f"{number:03d}_{alloc.stem}.sql"
        assert path.name == expected_name, f"expected {expected_name}, found {path.name}"
        assert (migrations_dir / expected_name).is_file()
