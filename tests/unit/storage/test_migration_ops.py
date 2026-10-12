"""Unit coverage for C11 index-generation pointer swaps (S03 storage side)."""

from __future__ import annotations

from pathlib import Path

import pytest

from magicite.errors import InvalidInputError
from magicite.storage import db as db_mod
from magicite.storage import lease as lease_mod
from magicite.storage import migration_ops as ops
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION


def test_index_generation_atomic_swap_and_rollback(tmp_path: Path) -> None:
    conn = db_mod.connect(tmp_path / "idx.db")
    try:
        with lease_mod.writer_lease("index-test"):
            ops.begin_index_generation(
                conn,
                generation_id="gen_a",
                fingerprint={"provider": "hashing", "dim": 8},
                fingerprint_digest="a" * 64,
                schema_version=MAX_KNOWN_SCHEMA_VERSION,
            )
            ops.complete_index_generation(conn, "gen_a")
            published = ops.publish_index_generation(conn, "gen_a")
            assert published["generation_id"] == "gen_a"
            assert published["previous_generation_id"] is None

            ops.begin_index_generation(
                conn,
                generation_id="gen_b",
                fingerprint={"provider": "hashing", "dim": 8, "rev": 2},
                fingerprint_digest="b" * 64,
                schema_version=MAX_KNOWN_SCHEMA_VERSION,
            )
            with pytest.raises(InvalidInputError, match="building"):
                ops.publish_index_generation(conn, "gen_b")
            ops.complete_index_generation(conn, "gen_b")
            ops.publish_index_generation(conn, "gen_b")

            active = ops.active_index_generation(conn)
            assert active is not None
            assert active["generation_id"] == "gen_b"
            assert active["previous_generation_id"] == "gen_a"

            rolled = ops.rollback_index_generation(conn)
            assert rolled["generation_id"] == "gen_a"
            active = ops.active_index_generation(conn)
            assert active is not None
            assert active["generation_id"] == "gen_a"
    finally:
        conn.close()
