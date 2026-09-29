"""AC-S03 migration authority: preview / crash-resume / idempotent apply / security."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import migration as migration_mod
from magicite.core import registry as registry_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.engram.parser import parse_artifact_file
from magicite.errors import BusyError, InvalidInputError
from magicite.storage import db as db_mod
from magicite.storage import migration_ops as ops
from magicite.storage.migrations.registry import MAX_KNOWN_SCHEMA_VERSION


class MigrationFault(RuntimeError):
    def __init__(self, boundary: str) -> None:
        super().__init__(boundary)
        self.boundary = boundary


def _engram_bytes(root: Path) -> dict[str, bytes]:
    registry = root / ".magicite" / "engrams"
    return {path.name: path.read_bytes() for path in sorted(registry.glob("*.egr.md"))}


def _journal_keys(cfg: Config, operation_id: str) -> tuple[str, ...]:
    conn = db_mod.connect(cfg.db_path, migrate=True)
    try:
        return tuple(sorted(ops.list_journal_steps(conn, operation_id).keys()))
    finally:
        conn.close()


def _mirror_projection(cfg: Config) -> dict[str, tuple[str, str, str]]:
    """engram_id → (spec_version, content_sha256, body_sha256)."""
    conn = db_mod.connect(cfg.db_path, migrate=True)
    try:
        rows = conn.execute(
            "SELECT id, spec_version, content_sha256, body_sha256 FROM engram ORDER BY id"
        ).fetchall()
        return {
            str(r["id"]): (str(r["spec_version"]), str(r["content_sha256"]), str(r["body_sha256"]))
            for r in rows
        }
    finally:
        conn.close()


def _prepare_registered(cfg: Config) -> None:
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    try:
        outcome = registry_mod.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
        assert outcome.ingested >= 1
        assert db_mod.schema_version(conn) == MAX_KNOWN_SCHEMA_VERSION
    finally:
        conn.close()


def _clone_magicite(src: Path, dest: Path) -> Config:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(src / ".magicite", dest / ".magicite")
    return Config.load(dest, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})


def test_preview_zero_write_no_wal_shm(cfg: Config) -> None:
    """AC-S03-01: preview must not mutate bytes or create -wal/-shm sidecars."""
    _prepare_registered(cfg)
    # Ensure a clean checkpoint so sidecars are absent before preview.
    conn = db_mod.connect(cfg.db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    for sidecar in (Path(str(cfg.db_path) + "-wal"), Path(str(cfg.db_path) + "-shm")):
        if sidecar.exists():
            sidecar.unlink()

    before = migration_mod._input_digests(cfg)
    before_files = _engram_bytes(cfg.project_root)
    db_mtime = cfg.db_path.stat().st_mtime_ns

    preview = migration_mod.preview(cfg)
    assert preview.kind == "upgrade_engram_0_2_to_1_0"
    assert preview.eligibility_diff == "unevaluated"
    assert all(d.before_ok_for_composition is None for d in preview.eligibility_deltas)
    assert preview.input_digests == before
    assert migration_mod._input_digests(cfg) == before
    assert _engram_bytes(cfg.project_root) == before_files
    assert cfg.db_path.stat().st_mtime_ns == db_mtime
    assert not Path(str(cfg.db_path) + "-wal").exists()
    assert not Path(str(cfg.db_path) + "-shm").exists()
    as_dict = migration_mod.preview_as_dict(preview)
    assert as_dict["eligibility_diff"] == "unevaluated"


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
    """AC-S03-02: resume after fault at true commit boundaries ≡ uninterrupted."""
    _prepare_registered(cfg)
    preview = migration_mod.preview(cfg)
    first_artifact = next(p for p in preview.artifact_plans if p.source_format == "engram/0.2")
    first_engram_id = first_artifact.engram_id
    first_backup_rel = f"engrams/{Path(first_artifact.relpath).name}"

    boundaries = [
        f"boundary:backup_file:{first_backup_rel}",
        "boundary:backup_files_copied",
        "boundary:backup_committed",
        f"precommit:file:{first_artifact.relpath}",
        f"boundary:file:{first_artifact.relpath}",
        f"boundary:db_mirror:{first_engram_id}",
        "boundary:db_mirror_committed",
        "boundary:operation_completed",
    ]

    baseline_root = tmp_path / "baseline"
    baseline_cfg = _clone_magicite(cfg.project_root, baseline_root)
    baseline = migration_mod.apply(baseline_cfg, operation_id="mig_crash_baseline")
    assert baseline.state == "completed"
    baseline_files = _engram_bytes(baseline_root)
    baseline_mirrors = _mirror_projection(baseline_cfg)
    baseline_journal = _journal_keys(baseline_cfg, "mig_crash_baseline")

    for boundary in boundaries:
        case_root = tmp_path / f"case-{hashlib.sha256(boundary.encode()).hexdigest()[:8]}"
        case_cfg = _clone_magicite(cfg.project_root, case_root)
        op_id = f"mig_crash_{hashlib.sha256(boundary.encode()).hexdigest()[:10]}"

        def hook(hit: str, *, expect: str = boundary) -> None:
            if hit == expect:
                raise MigrationFault(hit)

        with pytest.raises(MigrationFault) as raised:
            migration_mod.apply(case_cfg, operation_id=op_id, fault_hook=hook)
        assert raised.value.boundary == boundary

        # Partial backup must not leave STEP_BACKUP committed.
        if boundary.startswith("boundary:backup_file:") or boundary == "boundary:backup_files_copied":
            conn = db_mod.connect(case_cfg.db_path)
            try:
                assert migration_mod.STEP_BACKUP not in ops.list_journal_steps(conn, op_id)
            finally:
                conn.close()

        resumed = migration_mod.resume(case_cfg, op_id)
        assert resumed.state == "completed"
        assert _engram_bytes(case_root) == baseline_files
        assert _mirror_projection(case_cfg) == baseline_mirrors
        assert _journal_keys(case_cfg, op_id) == baseline_journal

        sample = next((case_root / ".magicite" / "engrams").glob("*.egr.md"))
        artifact, _doc = parse_artifact_file(sample, registry_root=case_root)
        assert artifact.frontmatter.spec == "engram/1.0"


def test_partial_backup_resume_rebuilds(cfg: Config, tmp_path: Path) -> None:
    """Finding 4: resume after partial backup rebuilds until STEP_BACKUP commits."""
    _prepare_registered(cfg)
    preview = migration_mod.preview(cfg)
    first = next(p for p in preview.artifact_plans if p.source_format == "engram/0.2")
    boundary = f"boundary:backup_file:engrams/{Path(first.relpath).name}"

    baseline = _clone_magicite(cfg.project_root, tmp_path / "pb-baseline")
    migration_mod.apply(baseline, operation_id="mig_pb_base")
    base_files = _engram_bytes(tmp_path / "pb-baseline")

    case = _clone_magicite(cfg.project_root, tmp_path / "pb-case")
    op_id = "mig_partial_backup"

    def hook(hit: str) -> None:
        if hit == boundary:
            raise MigrationFault(hit)

    with pytest.raises(MigrationFault):
        migration_mod.apply(case, operation_id=op_id, fault_hook=hook)

    backup_dir = case.data_dir / "migrations" / op_id / "backup"
    assert backup_dir.exists()
    # Corrupt/partial: wipe some bytes to prove rebuild replaces them.
    for leftover in backup_dir.rglob("*"):
        if leftover.is_file():
            leftover.write_bytes(b"partial")

    resumed = migration_mod.resume(case, op_id)
    assert resumed.state == "completed"
    assert _engram_bytes(tmp_path / "pb-case") == base_files
    manifest = json.loads(
        (case.data_dir / "migrations" / op_id / "manifest.json").read_text(encoding="utf-8")
    )
    for entry in manifest["files"]:
        path = backup_dir / entry["path"]
        assert migration_mod._sha256_file(path) == entry["sha256"]
        assert path.stat().st_size == entry["size"]
        # Modes hardened (nit 7).
        assert stat_mode(path) & 0o777 == 0o600


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_backup_source_dest_digest_match(cfg: Config) -> None:
    """Finding 4: manifest digests match source files, not just dest after copy."""
    _prepare_registered(cfg)
    before = {
        path.name: (migration_mod._sha256_file(path), path.stat().st_size)
        for path in cfg.registry_dir.glob("*.egr.md")
    }
    result = migration_mod.apply(cfg, operation_id="mig_digest_check")
    assert result.state == "completed"
    manifest = json.loads(
        (cfg.data_dir / "migrations" / "mig_digest_check" / "manifest.json").read_text(encoding="utf-8")
    )
    for entry in manifest["files"]:
        if not entry["path"].startswith("engrams/"):
            continue
        name = Path(entry["path"]).name
        src_sha, src_size = before[name]
        assert entry["sha256"] == src_sha
        assert entry["size"] == src_size
        assert entry.get("source_sha256", entry["sha256"]) == src_sha


def test_restore_rejects_path_traversal(cfg: Config, tmp_path: Path) -> None:
    """Finding 3: ../, absolute paths, and symlinks are rejected before I/O."""
    _prepare_registered(cfg)
    migration_mod.apply(cfg, operation_id="mig_trav_base")
    op_dir = cfg.data_dir / "migrations" / "mig_sec"
    backup = op_dir / "backup" / "engrams"
    backup.mkdir(parents=True)
    (backup / "ok.egr.md").write_text("x", encoding="utf-8")

    def _manifest(files: list[dict[str, object]]) -> None:
        payload = {
            "manifest_kind": "migration_backup/1",
            "schema_version": 1,
            "operation_id": "mig_sec",
            "source_engram_format": "engram/0.2",
            "target_engram_format": "engram/1.0",
            "storage_schema_version": MAX_KNOWN_SCHEMA_VERSION,
            "files": files,
        }
        (op_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    # Traversal via ..
    _manifest(
        [
            {
                "path": "../escape.egr.md",
                "sha256": "0" * 64,
                "size": 1,
            }
        ]
    )
    with pytest.raises(InvalidInputError, match=r"\.\.|escapes"):
        migration_mod.restore(cfg, backup_path=op_dir)

    # Absolute path
    _manifest([{"path": "/tmp/evil.egr.md", "sha256": "0" * 64, "size": 1}])
    with pytest.raises(InvalidInputError, match="absolute|relative"):
        migration_mod.restore(cfg, backup_path=op_dir)

    # Symlink inside backup tree
    link = backup / "linked.egr.md"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("/etc/passwd", link)
    digest = migration_mod._sha256_file(Path("/etc/passwd")) if Path("/etc/passwd").is_file() else "0" * 64
    size = Path("/etc/passwd").stat().st_size if Path("/etc/passwd").is_file() else 1
    _manifest([{"path": "engrams/linked.egr.md", "sha256": digest, "size": size}])
    with pytest.raises(InvalidInputError, match="symlink"):
        migration_mod.restore(cfg, backup_path=op_dir)


def test_concurrent_apply_busy(cfg: Config) -> None:
    """Finding 6: a second applier receives BusyError while the first holds the lease."""
    _prepare_registered(cfg)
    held = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def hook(hit: str) -> None:
        if hit == "boundary:backup_committed":
            held.set()
            assert release.wait(timeout=5)

    def first() -> None:
        try:
            migration_mod.apply(cfg, operation_id="mig_concurrent_a", fault_hook=hook)
        except BaseException as exc:  # noqa: BLE001 — collect for main thread
            errors.append(exc)

    t = threading.Thread(target=first)
    t.start()
    assert held.wait(timeout=5)

    with pytest.raises(BusyError):
        migration_mod.apply(cfg, operation_id="mig_concurrent_b")

    release.set()
    t.join(timeout=10)
    assert not errors


def test_lease_loss_mid_artifact_fencing(cfg: Config) -> None:
    """Finding 6: losing the fencing token mid-artifact aborts before journal commit."""
    _prepare_registered(cfg)
    preview = migration_mod.preview(cfg)
    first = next(p for p in preview.artifact_plans if p.source_format == "engram/0.2")
    boundary = f"precommit:file:{first.relpath}"

    def hook(hit: str) -> None:
        if hit == boundary:
            # Steal the lease row so assert_owned fails on the subsequent journal write.
            conn = db_mod.connect(cfg.db_path, migrate=False)
            try:
                conn.execute(
                    "UPDATE writer_lease SET fencing_token = fencing_token + 1, "
                    "holder = 'stolen', expires_at = '2099-01-01T00:00:00+00:00' WHERE id = 1"
                )
            finally:
                conn.close()

    with pytest.raises(BusyError):
        migration_mod.apply(cfg, operation_id="mig_fence_loss", fault_hook=hook)

    conn = db_mod.connect(cfg.db_path)
    try:
        steps = ops.list_journal_steps(conn, "mig_fence_loss")
        assert f"artifact:{first.relpath}" not in steps
    finally:
        conn.close()


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
