"""AC-S03 migration authority: preview / crash-resume / idempotent apply."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import migration as migration_mod
from magicite.core import registry as registry_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.engram.parser import parse_artifact_file
from magicite.errors import InvalidInputError
from magicite.storage import db as db_mod
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION


class MigrationFault(RuntimeError):
    def __init__(self, boundary: str) -> None:
        super().__init__(boundary)
        self.boundary = boundary


def _engram_bytes(root: Path) -> dict[str, bytes]:
    registry = root / ".magicite" / "engrams"
    return {path.name: path.read_bytes() for path in sorted(registry.glob("*.egr.md"))}


def _prepare_registered(cfg: Config) -> None:
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        outcome = registry_mod.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
        assert outcome.ingested >= 1
        assert db_mod.schema_version(conn) == MAX_KNOWN_SCHEMA_VERSION
    finally:
        conn.close()


def test_preview_zero_write(cfg: Config) -> None:
    """AC-S03-01: preview must not mutate any durable input bytes."""
    _prepare_registered(cfg)
    before = migration_mod._input_digests(cfg)
    before_files = _engram_bytes(cfg.project_root)

    preview = migration_mod.preview(cfg)
    assert preview.kind == "upgrade_engram_0_2_to_1_0"
    assert preview.artifact_plans
    assert preview.input_digests == before
    assert migration_mod._input_digests(cfg) == before
    assert _engram_bytes(cfg.project_root) == before_files
    # DB file digest included in input_digests must also be stable.
    assert cfg.db_path.is_file()
    assert (
        before[migration_mod._rel_to_data(cfg, cfg.db_path)]
        == hashlib.sha256(cfg.db_path.read_bytes()).hexdigest()
    )


def test_idempotent_apply(cfg: Config) -> None:
    """AC-S03-03: re-applying a completed operation_id is a zero-effect noop."""
    _prepare_registered(cfg)
    first = migration_mod.apply(cfg, operation_id="mig_idempotent_1")
    assert first.state == "completed"
    assert first.duplicate_noop is False
    after_first = _engram_bytes(cfg.project_root)
    for raw in after_first.values():
        assert b"engram/1.0" in raw

    second = migration_mod.apply(cfg, operation_id="mig_idempotent_1")
    assert second.state == "completed"
    assert second.duplicate_noop is True
    assert second.operation_id == first.operation_id
    assert _engram_bytes(cfg.project_root) == after_first

    st = migration_mod.status(cfg, "mig_idempotent_1")
    assert st.state == "completed"
    assert "operation_completed" in st.steps_done


def test_crash_matrix(cfg: Config, tmp_path: Path) -> None:
    """AC-S03-02: resume after fault at each boundary equals uninterrupted apply."""
    boundaries = [
        "boundary:backup_committed",
        "boundary:db_mirror_committed",
        "boundary:operation_completed",
    ]
    # Also fault after the first artifact rewrite when present.
    _prepare_registered(cfg)
    preview = migration_mod.preview(cfg)
    first_artifact = next(p for p in preview.artifact_plans if p.source_format == "engram/0.2")
    boundaries.insert(1, f"boundary:file:{first_artifact.relpath}")

    baseline_root = tmp_path / "baseline"
    baseline_root.mkdir()
    # Clone project for uninterrupted baseline.
    import shutil

    shutil.copytree(cfg.project_root / ".magicite", baseline_root / ".magicite")
    baseline_cfg = Config.load(baseline_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    baseline = migration_mod.apply(baseline_cfg, operation_id="mig_crash_baseline")
    assert baseline.state == "completed"
    baseline_files = _engram_bytes(baseline_root)

    for boundary in boundaries:
        case_root = tmp_path / f"case-{hashlib.sha256(boundary.encode()).hexdigest()[:8]}"
        if case_root.exists():
            shutil.rmtree(case_root)
        shutil.copytree(cfg.project_root / ".magicite", case_root / ".magicite")
        # Restore pre-migration engrams from the original cfg snapshot taken
        # before any apply — cfg itself was not migrated yet in this loop's
        # source: re-copy from the fixture project that test started with.
        # `cfg` may still be unmigrated because we only migrate clones.
        case_cfg = Config.load(case_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})

        op_id = f"mig_crash_{hashlib.sha256(boundary.encode()).hexdigest()[:10]}"

        def hook(hit: str, *, expect: str = boundary) -> None:
            if hit == expect:
                raise MigrationFault(hit)

        with pytest.raises(MigrationFault) as raised:
            migration_mod.apply(case_cfg, operation_id=op_id, fault_hook=hook)
        assert raised.value.boundary == boundary

        resumed = migration_mod.resume(case_cfg, op_id)
        assert resumed.state == "completed"
        assert _engram_bytes(case_root) == baseline_files

        # Files parse as 1.0 after successful resume.
        sample = next((case_root / ".magicite" / "engrams").glob("*.egr.md"))
        artifact, _doc = parse_artifact_file(sample, registry_root=case_root)
        assert artifact.frontmatter.spec == "engram/1.0"


def test_unsupported_future_schema_fails_closed(tmp_path: Path) -> None:
    """C8: newer unreadable schema versions must fail before mutation."""
    db_path = tmp_path / "future.db"
    conn = db_mod.connect(db_path, migrate=True)
    try:
        future = MAX_KNOWN_SCHEMA_VERSION + 1
        conn.execute(f"PRAGMA user_version = {future}")
    finally:
        conn.close()

    with pytest.raises(InvalidInputError, match="newer than this build"):
        db_mod.connect(db_path, migrate=True)
