"""AC-S03 migration authority: preview / crash-resume / idempotent apply / security."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
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


def _tree_relpaths(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def _tree_meta(root: Path) -> dict[str, tuple[int, int, int]]:
    """relpath → (size, mtime_ns, mode) for zero-write assertions."""
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int, int]] = {}
    for path in root.rglob("*"):
        st = path.lstat()
        out[path.relative_to(root).as_posix()] = (
            st.st_size,
            st.st_mtime_ns,
            stat.S_IMODE(st.st_mode),
        )
    return out


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


def test_idempotent_apply(legacy_migration_case, tmp_path: Path) -> None:
    """AC-S03-03: the same reviewed operation neither appends nor rewrites."""
    cfg, plan, backup, provider = legacy_migration_case(tmp_path)
    from magicite.core import trust_legacy

    kwargs = {"reviewed_sha256": trust_legacy.digest(plan), "backup_path": backup}
    first = migration_mod.apply(cfg, **kwargs)
    assert first.state == "completed" and not first.duplicate_noop
    after_first = _engram_bytes(cfg.project_root)
    assert all(b"engram/1.0" in raw for raw in after_first.values())
    head = provider.call("read_current")
    second = migration_mod.apply(cfg, operation_id=first.operation_id, **kwargs)
    assert second.state == "completed" and second.duplicate_noop
    assert second.operation_id == first.operation_id
    assert _engram_bytes(cfg.project_root) == after_first
    assert provider.call("read_current")["head_sequence"] == head["head_sequence"]
    st = migration_mod.status(cfg, first.operation_id)
    assert st.state == "completed"
    assert st.steps_done[-1].endswith("-complete")


def test_crash_matrix(legacy_migration_case, tmp_path: Path) -> None:
    """AC-S03-02: actual reviewed commit boundaries resume to identical targets."""
    from magicite.core import trust_legacy

    baseline_cfg, baseline_plan, backup, _ = legacy_migration_case(tmp_path / "baseline")
    baseline = migration_mod.apply(
        baseline_cfg, reviewed_sha256=trust_legacy.digest(baseline_plan), backup_path=backup
    )
    assert baseline.state == "completed"
    baseline_files = _engram_bytes(baseline_cfg.project_root)
    baseline_mirrors = _mirror_projection(baseline_cfg)
    boundaries = [
        "begin_committed",
        "record_committed:artifact_transform",
        "target_published",
        "record_committed:trust_decision",
        "before_complete",
        "complete_committed",
    ]
    for i, boundary in enumerate(boundaries):
        cfg, plan, backup, provider = legacy_migration_case(tmp_path / f"case-{i}")
        digest = trust_legacy.digest(plan)

        def hook(hit: str, expect=boundary):
            if hit == expect:
                raise MigrationFault(hit)

        with pytest.raises(MigrationFault) as raised:
            migration_mod.apply(cfg, reviewed_sha256=digest, backup_path=backup, fault_hook=hook)
        assert raised.value.boundary == boundary
        resumed = migration_mod.resume(
            cfg, "legacy-" + digest[:32], reviewed_sha256=digest, backup_path=backup
        )
        assert resumed.state == "completed"
        assert _engram_bytes(cfg.project_root) == baseline_files
        assert _mirror_projection(cfg) == baseline_mirrors
        records = provider.call("committed_records")
        assert len({record["record_id"] for record in records}) == len(records)
        assert records[-1]["payload"]["phase"] == "COMPLETE"
        sample = next(cfg.registry_dir.glob("*.egr.md"))
        artifact, _ = parse_artifact_file(sample, registry_root=cfg.project_root)
        assert artifact.frontmatter.spec == "engram/1.0"


def test_partial_backup_resume_rebuilds(legacy_migration_case, tmp_path: Path) -> None:
    """Incomplete backup resumes exact bytes; corruption is never overwritten."""
    from magicite.core import trust_legacy
    from magicite.core.trust_custodian import CustodianError

    cfg, plan, backup, _ = legacy_migration_case(tmp_path, create_backup=False)
    digest = trust_legacy.digest(plan)

    def hook(hit):
        if hit == "backup_file":
            raise MigrationFault(hit)

    with pytest.raises(MigrationFault):
        trust_legacy.backup_reviewed(
            cfg, plan=plan, reviewed_sha256=digest, destination=backup, fault_hook=hook
        )
    assert not (backup / "complete.json").exists()
    with pytest.raises(CustodianError):
        migration_mod.apply(cfg, reviewed_sha256=digest, backup_path=backup)
    assert not (cfg.data_dir / "trust/authority").exists()
    copied = next(path for path in (backup / "files").rglob("*") if path.is_file())
    original_copy = copied.read_bytes()
    copied.write_bytes(b"partial corruption")
    with pytest.raises(CustodianError):
        trust_legacy.backup_reviewed(cfg, plan=plan, reviewed_sha256=digest, destination=backup)
    assert copied.read_bytes() == b"partial corruption"
    assert not (cfg.data_dir / "trust/authority").exists()
    # Restore the fixture's original exact bytes before testing legitimate resume.
    copied.write_bytes(original_copy)
    trust_legacy.backup_reviewed(cfg, plan=plan, reviewed_sha256=digest, destination=backup)
    result = migration_mod.apply(cfg, reviewed_sha256=digest, backup_path=backup)
    assert result.state == "completed"
    _, manifest = trust_legacy.read_verified_backup(backup, reviewed_sha256=digest)
    for rel, entry in manifest["files"].items():
        path = backup / "files" / rel
        assert migration_mod._sha256_file(path) == entry["sha256"]
        assert path.stat().st_size == entry["size"]
        assert stat_mode(path) == 0o600


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_backup_source_dest_digest_match(legacy_migration_case, tmp_path: Path) -> None:
    from magicite.core import trust_legacy

    cfg, plan, backup, _ = legacy_migration_case(tmp_path)
    before = {
        p.name: (migration_mod._sha256_file(p), p.stat().st_size) for p in cfg.registry_dir.glob("*.egr.md")
    }
    result = migration_mod.apply(cfg, reviewed_sha256=trust_legacy.digest(plan), backup_path=backup)
    assert result.state == "completed"
    _, manifest = trust_legacy.read_verified_backup(backup, reviewed_sha256=trust_legacy.digest(plan))
    for rel, entry in manifest["files"].items():
        if rel.endswith(".egr.md"):
            src_sha, src_size = before[Path(rel).name]
            assert entry["sha256"] == src_sha
            assert entry["size"] == src_size


def test_restore_rejects_path_traversal(cfg: Config, tmp_path: Path) -> None:
    """Finding 3: ../, absolute paths, and symlinks are rejected before I/O."""
    _prepare_registered(cfg)
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
        migration_mod.restore(
            cfg, backup_path=op_dir, staging_path=tmp_path.parent / (tmp_path.name + "-staging")
        )

    # Absolute path
    _manifest([{"path": "/tmp/evil.egr.md", "sha256": "0" * 64, "size": 1}])
    with pytest.raises(InvalidInputError, match="absolute|relative"):
        migration_mod.restore(
            cfg, backup_path=op_dir, staging_path=tmp_path.parent / (tmp_path.name + "-staging")
        )

    # Symlink inside backup tree
    link = backup / "linked.egr.md"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("/etc/passwd", link)
    digest = migration_mod._sha256_file(Path("/etc/passwd")) if Path("/etc/passwd").is_file() else "0" * 64
    size = Path("/etc/passwd").stat().st_size if Path("/etc/passwd").is_file() else 1
    _manifest([{"path": "engrams/linked.egr.md", "sha256": digest, "size": size}])
    with pytest.raises(InvalidInputError, match="symlink"):
        migration_mod.restore(
            cfg, backup_path=op_dir, staging_path=tmp_path.parent / (tmp_path.name + "-staging")
        )


def test_concurrent_apply_busy(legacy_migration_case, tmp_path: Path, monkeypatch) -> None:
    from magicite.core import trust_legacy
    from magicite.core.trust_custodian import CustodianStore

    cfg, plan, backup, provider = legacy_migration_case(tmp_path)
    digest = trust_legacy.digest(plan)

    def call(operation, **arguments):
        store = CustodianStore.open(provider.store.directory)
        try:
            return getattr(store, operation)(provider.registry_id, **arguments)
        finally:
            store.close()

    monkeypatch.setattr(provider, "call", call)
    held, release = threading.Event(), threading.Event()
    errors = []

    def hook(hit):
        if hit == "begin_committed":
            held.set()
            assert release.wait(timeout=5)

    def first():
        try:
            migration_mod.apply(cfg, reviewed_sha256=digest, backup_path=backup, fault_hook=hook)
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=first)
    t.start()
    try:
        assert held.wait(timeout=5), errors
        with pytest.raises(BusyError):
            migration_mod.apply(cfg, reviewed_sha256=digest, backup_path=backup)
    finally:
        release.set()
        t.join(timeout=10)
    assert not errors


def test_lease_loss_mid_artifact_fencing(legacy_migration_case, tmp_path: Path) -> None:
    from magicite.core import trust_legacy

    cfg, plan, backup, provider = legacy_migration_case(tmp_path)
    digest = trust_legacy.digest(plan)

    def hook(hit):
        if hit == "begin_committed":
            conn = db_mod.connect(cfg.db_path, migrate=False)
            try:
                conn.execute(
                    "UPDATE writer_lease SET fencing_token=fencing_token+1, holder='stolen' WHERE id=1"
                )
            finally:
                conn.close()

    original = _engram_bytes(cfg.project_root)
    with pytest.raises(BusyError):
        migration_mod.apply(cfg, reviewed_sha256=digest, backup_path=backup, fault_hook=hook)
    assert _engram_bytes(cfg.project_root) == original
    assert provider.call("read_current")["legacy_reconciliation"] is not None


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


def test_preview_sees_uncheckpointed_wal_without_data_dir_writes(cfg: Config) -> None:
    """Preview must read post-change WAL state without writing into the data dir.

    ``immutable=1`` ignores the WAL and would report a stale snapshot when a
    non-empty ``-wal`` exists; the copy-based open must see the marker row and
    leave the data directory's path set unchanged.
    """
    _prepare_registered(cfg)
    writer = sqlite3.connect(str(cfg.db_path), isolation_level=None, check_same_thread=False)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE preview_wal_marker(x TEXT NOT NULL)")
        writer.execute("INSERT INTO preview_wal_marker(x) VALUES ('post-change')")
        wal = Path(str(cfg.db_path) + "-wal")
        assert wal.is_file() and wal.stat().st_size > 0

        before_meta = _tree_meta(cfg.data_dir)
        preview = migration_mod.preview(cfg)
        assert preview.kind == "upgrade_engram_0_2_to_1_0"
        assert _tree_meta(cfg.data_dir) == before_meta

        # Directly verify the readonly open path sees WAL frames (copy approach).
        conn, _ver, owns, temp_dir = migration_mod._connect_preview_readonly(cfg)
        assert owns is True
        assert conn is not None
        try:
            row = conn.execute("SELECT x FROM preview_wal_marker").fetchone()
            assert row is not None
            assert row[0] == "post-change"
        finally:
            conn.close()
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
        assert _tree_meta(cfg.data_dir) == before_meta
    finally:
        writer.close()


def test_preview_wal_copy_retries_or_fails_on_checkpoint_tear(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Torn db/-wal copy must never surface as a wrong snapshot or OperationalError.

    Simulate TRUNCATE checkpoint between copying the main DB and the WAL; the
    stable-copy path must retry to a consistent open or fail closed with BusyError.
    """
    _prepare_registered(cfg)
    # Keep the writer open so the WAL is not auto-checkpointed away on close;
    # the tear hook closes it so TRUNCATE can finish mid-copy.
    writer = sqlite3.connect(str(cfg.db_path), isolation_level=None, check_same_thread=False)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE preview_wal_marker(x TEXT NOT NULL)")
    writer.execute("INSERT INTO preview_wal_marker(x) VALUES ('post-change')")

    wal = Path(str(cfg.db_path) + "-wal")
    assert wal.is_file() and wal.stat().st_size > 0

    tear_once = {"done": False}
    real_copy2 = shutil.copy2

    def tearing_copy2(
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        *,
        follow_symlinks: bool = True,
    ) -> str | os.PathLike[str]:
        result = real_copy2(src, dst, follow_symlinks=follow_symlinks)
        if not tear_once["done"] and Path(src).resolve() == cfg.db_path.resolve():
            tear_once["done"] = True
            writer.close()
            chk = sqlite3.connect(str(cfg.db_path), isolation_level=None, check_same_thread=False)
            try:
                chk.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                chk.close()
        return result

    monkeypatch.setattr(shutil, "copy2", tearing_copy2)

    try:
        # Assert on the *first* open under the tear — a second open after
        # checkpoint would falsely pass against a live DB that already absorbed
        # the WAL frames.
        try:
            conn, _ver, owns, temp_dir = migration_mod._connect_preview_readonly(cfg)
        except BusyError as exc:
            assert "being written" in exc.message
            return

        assert owns is True
        assert conn is not None
        try:
            row = conn.execute("SELECT x FROM preview_wal_marker").fetchone()
            assert row is not None
            assert row[0] == "post-change"
        finally:
            conn.close()
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001 — may already be closed by tear hook
            pass


def test_preview_wal_copy_fails_closed_when_never_stable(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If db/wal keep changing under copy, preview fails closed with BusyError."""
    _prepare_registered(cfg)
    writer = sqlite3.connect(str(cfg.db_path), isolation_level=None, check_same_thread=False)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE preview_wal_marker(x TEXT NOT NULL)")
        writer.execute("INSERT INTO preview_wal_marker(x) VALUES ('post-change')")
        assert Path(str(cfg.db_path) + "-wal").stat().st_size > 0

        real_copy2 = shutil.copy2

        def unstable_copy2(
            src: str | os.PathLike[str],
            dst: str | os.PathLike[str],
            *,
            follow_symlinks: bool = True,
        ) -> str | os.PathLike[str]:
            result = real_copy2(src, dst, follow_symlinks=follow_symlinks)
            # Nudge mtime so the post-copy identity check always fails.
            os.utime(src, None)
            return result

        monkeypatch.setattr(shutil, "copy2", unstable_copy2)

        with pytest.raises(BusyError, match="being written"):
            migration_mod.preview(cfg)
    finally:
        writer.close()


def test_preview_nonexistent_data_dir_creates_nothing(tmp_path: Path) -> None:
    """Preview must not call ensure_dirs: missing data dir stays missing."""
    root = tmp_path / "uninitialized"
    root.mkdir()
    cfg = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    assert not cfg.data_dir.exists()
    before = _tree_meta(root)

    preview = migration_mod.preview(cfg)

    assert _tree_meta(root) == before
    assert not cfg.data_dir.exists()
    assert preview.artifact_plans == ()
    assert preview.input_digests == {}
    assert preview.kind == "upgrade_engram_0_2_to_1_0"


def test_mkdir_secure_fsyncs_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After creating a directory, the parent dirent must be fsynced."""
    synced_paths: list[Path] = []
    real_open = os.open
    real_fsync = os.fsync
    fd_to_path: dict[int, Path] = {}

    def tracking_open(path: str | bytes | os.PathLike[str], flags: int, *args: object) -> int:
        fd = real_open(path, flags, *args) if args else real_open(path, flags)
        fd_to_path[fd] = Path(os.fsdecode(path)).resolve()
        return fd

    def tracking_fsync(fd: int) -> None:
        synced_paths.append(fd_to_path.get(fd, Path(f"<fd:{fd}>")))
        real_fsync(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fsync", tracking_fsync)

    child = tmp_path / "secure-child"
    migration_mod._mkdir_secure(child)
    assert child.is_dir()
    assert tmp_path.resolve() in synced_paths


def test_mkdir_secure_nested_parents_mode_and_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested mkdir chmods every new intermediate and fsyncs each new parent."""
    synced_paths: list[Path] = []
    real_open = os.open
    real_fsync = os.fsync
    fd_to_path: dict[int, Path] = {}

    def tracking_open(path: str | bytes | os.PathLike[str], flags: int, *args: object) -> int:
        fd = real_open(path, flags, *args) if args else real_open(path, flags)
        fd_to_path[fd] = Path(os.fsdecode(path)).resolve()
        return fd

    def tracking_fsync(fd: int) -> None:
        synced_paths.append(fd_to_path.get(fd, Path(f"<fd:{fd}>")))
        real_fsync(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fsync", tracking_fsync)

    target = tmp_path / "a" / "b" / "c"
    migration_mod._mkdir_secure(target, mode=0o700)

    for part in (tmp_path / "a", tmp_path / "a" / "b", target):
        assert part.is_dir()
        assert stat.S_IMODE(part.stat().st_mode) == 0o700

    assert tmp_path.resolve() in synced_paths
    assert (tmp_path / "a").resolve() in synced_paths
    assert (tmp_path / "a" / "b").resolve() in synced_paths
