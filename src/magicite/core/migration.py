"""Migration authority: preview / apply / status / resume / restore (C8 / S03).

Domain APIs only — S11 owns CLI/MCP bindings. Artifact format upgrades use
S02's pure ``transform_0_2_to_1_0`` + ``write_engram_as``. Downgrade is
**restore from a matching pre-upgrade backup**, never a 1.0→0.2 transform
and never ``DELETE`` of SQL migration rows.

Crash safety: every file/SQL commit boundary records a journal step; resume
replays only incomplete steps so the final state equals one uninterrupted
apply. Retries of a completed ``operation_id`` are zero-effect.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from magicite.config import Config
from magicite.core import writer_guard
from magicite.engram.model import Engram
from magicite.engram.parser import EngramParseError, load_artifact_file, parse_file
from magicite.engram.transform import TransformDiagnostic, transform_0_2_to_1_0
from magicite.engram.writer import write_engram_as
from magicite.errors import BusyError, InvalidInputError, NotFoundError
from magicite.storage import db as db_mod
from magicite.storage import migration_ops as ops
from magicite.storage.migrations.registry import (
    MAX_KNOWN_SCHEMA_VERSION,
    SUPPORTED_BACKUP_MANIFEST_KINDS,
    SUPPORTED_SOURCE_ENGRAM_FORMATS,
    SUPPORTED_TARGET_ENGRAM_FORMATS,
)

MigrationKind = Literal["upgrade_engram_0_2_to_1_0", "restore_backup"]
OperationState = Literal["pending", "running", "completed", "failed", "restored", "reconciliation_required"]
FaultHook = Callable[[str], None]

MANIFEST_KIND = "migration_backup/1"
AUTHORITATIVE_TREE_NAMES = ("engrams", "approvals", "archive")
STEP_BACKUP = "backup_created"
STEP_DB_MIRROR = "db_mirror_updated"
STEP_COMPLETED = "operation_completed"


@dataclass(frozen=True, slots=True)
class ArtifactPlan:
    relpath: str
    engram_id: str
    name: str
    source_format: str
    target_format: str
    source_sha256: str
    diagnostics: tuple[TransformDiagnostic, ...]
    ok_for_composition: bool
    verification_status: str | None
    origin: str | None


@dataclass(frozen=True, slots=True)
class EligibilityDelta:
    engram_id: str
    #: Always ``None`` until S06 wires live eligibility; do not treat as OK.
    before_ok_for_composition: bool | None
    after_ok_for_composition: bool | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationPreview:
    kind: MigrationKind
    source_format: str
    target_format: str
    input_digests: dict[str, str]
    artifact_plans: tuple[ArtifactPlan, ...]
    eligibility_deltas: tuple[EligibilityDelta, ...]
    blocking_diagnostics: tuple[dict[str, Any], ...]
    preview_digest: str
    storage_schema_version: int
    #: S06 owns live eligibility diffs; S03 only reports transform diagnostics.
    eligibility_diff: Literal["unevaluated"] = "unevaluated"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    operation_id: str
    state: OperationState
    kind: MigrationKind
    preview_digest: str | None
    backup_relpath: str | None
    steps_done: tuple[str, ...]
    resumed: bool = False
    duplicate_noop: bool = False


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    operation_id: str
    kind: str
    state: OperationState
    source_format: str
    target_format: str
    backup_relpath: str | None
    preview_digest: str | None
    manifest_digest: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    error_message: str | None
    steps_done: tuple[str, ...]


@dataclass
class _ApplyContext:
    cfg: Config
    conn: sqlite3.Connection
    operation_id: str
    preview: MigrationPreview
    fault_hook: FaultHook | None = None
    resumed: bool = False
    steps_done: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_operation_id() -> str:
    return f"mig_{uuid.uuid4().hex}"


def _migrations_root(cfg: Config) -> Path:
    return cfg.data_dir / "migrations"


def _operation_dir(cfg: Config, operation_id: str) -> Path:
    return _migrations_root(cfg) / operation_id


def _backup_dir(cfg: Config, operation_id: str) -> Path:
    return _operation_dir(cfg, operation_id) / "backup"


def _rel_to_data(cfg: Config, path: Path) -> str:
    return path.resolve().relative_to(cfg.data_dir.resolve()).as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_secure(path: Path, *, mode: int = 0o700) -> None:
    """Create ``path`` and any missing parents at ``mode``; fsync each new parent."""
    to_create: list[Path] = []
    cursor = path
    while not cursor.exists():
        to_create.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    path.mkdir(parents=True, exist_ok=True)
    # chmod every newly created intermediate (not just the leaf) so umask
    # cannot leave world-readable 0755 parents in the tree.
    for created in to_create:
        os.chmod(created, mode)
    # Durability: fsync the parent of each newly created directory so the
    # directory entry itself survives a crash (macOS/Linux support dir fsync).
    for created in to_create:
        _fsync_dir(created.parent)


def _write_bytes_durable(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    _mkdir_secure(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, mode)
    _fsync_file(path)
    _fsync_dir(path.parent)


def _copy_file_durable(src: Path, dest: Path, *, mode: int = 0o600) -> dict[str, Any]:
    """Copy ``src`` → ``dest``, fsync, and verify dest digest/size match source."""
    _mkdir_secure(dest.parent)
    source_sha = _sha256_file(src)
    source_size = src.stat().st_size
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open(src, "rb") as rf, open(tmp, "wb") as wf:
        shutil.copyfileobj(rf, wf, length=1024 * 1024)
        wf.flush()
        os.fsync(wf.fileno())
    os.replace(tmp, dest)
    os.chmod(dest, mode)
    _fsync_file(dest)
    _fsync_dir(dest.parent)
    dest_sha = _sha256_file(dest)
    dest_size = dest.stat().st_size
    if dest_sha != source_sha or dest_size != source_size:
        raise InvalidInputError(
            f"backup copy integrity failure for {dest.name}",
            details={
                "source_sha256": source_sha,
                "dest_sha256": dest_sha,
                "source_size": source_size,
                "dest_size": dest_size,
            },
        )
    return {"sha256": dest_sha, "size": dest_size, "source_sha256": source_sha}


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _constrained_under(root: Path, rel: str, *, label: str) -> Path:
    """Resolve ``rel`` under ``root``; reject absolute paths, ``..``, and escapes."""
    if not isinstance(rel, str) or not rel:
        raise InvalidInputError(f"{label} path must be a non-empty relative string")
    candidate = Path(rel)
    if candidate.is_absolute():
        raise InvalidInputError(
            f"{label} path must be relative, got absolute {rel!r}",
            details={"path": rel},
        )
    if any(part == ".." for part in candidate.parts):
        raise InvalidInputError(
            f"{label} path escapes root via '..': {rel!r}",
            details={"path": rel},
        )
    root_resolved = root.resolve()
    joined = root_resolved.joinpath(*candidate.parts)
    cursor = root_resolved
    for part in candidate.parts:
        cursor = cursor / part
        if _is_symlink(cursor):
            raise InvalidInputError(
                f"{label} path contains a symlink: {rel!r}",
                details={"path": rel, "symlink": str(cursor)},
            )
    resolved = joined.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise InvalidInputError(
            f"{label} path escapes backup/data root: {rel!r}",
            details={"path": rel, "resolved": str(resolved), "root": str(root_resolved)},
        ) from exc
    return resolved


def _file_identity(path: Path) -> tuple[int, int, int] | None:
    """Return ``(st_ino, st_size, st_mtime_ns)`` or ``None`` if missing."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _clear_preview_copy_bundle(dest: Path) -> None:
    for companion in (dest, Path(f"{dest}-wal"), Path(f"{dest}-shm")):
        try:
            companion.unlink()
        except FileNotFoundError:
            pass


def _open_preview_uri(uri: str) -> sqlite3.Connection:
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("PRAGMA quick_check").fetchone()
        if row is None or str(row[0]) != "ok":
            raise sqlite3.DatabaseError(f"preview open quick_check failed: {row[0] if row else None!r}")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        db_mod.assert_schema_supported(conn)
    except Exception:
        conn.close()
        raise
    return conn


_PREVIEW_WAL_COPY_ATTEMPTS = 5


def _connect_preview_readonly(
    cfg: Config,
) -> tuple[sqlite3.Connection | None, int, bool, Path | None]:
    """Open an existing DB read-only for preview (zero writes to the data dir).

    Returns ``(conn_or_None, schema_version, owns_conn, temp_dir_or_None)``.
    When ``temp_dir_or_None`` is set, the caller must ``shutil.rmtree`` it after
    closing the connection (private copy of db+wal).

    Behaviour:
    * Missing/empty DB → ``(None, MAX_KNOWN_SCHEMA_VERSION, False, None)``.
    * No non-empty ``-wal`` → open with ``mode=ro&immutable=1`` so SQLite does
      not create ``-wal``/``-shm`` companions in the data dir.
    * Non-empty ``-wal`` (un-checkpointed frames) → ``immutable=1`` would ignore
      the WAL and return a stale snapshot. Instead, copy the database file plus
      ``-wal`` (never ``-shm``; SQLite rebuilds it in the temp dir) via a
      retry-until-stable identity check (inode/size/mtime_ns before and after),
      validate with ``PRAGMA quick_check``, and open that copy with ``mode=ro``.
      Concurrent checkpoint/write that never stabilizes fails closed with
      :class:`BusyError`. The data directory is never written.

    Preview is best-effort without the writer lease.
    """
    if not cfg.db_path.is_file() or cfg.db_path.stat().st_size == 0:
        return None, MAX_KNOWN_SCHEMA_VERSION, False, None

    temp_dir: Path | None = None
    last_problem = "unstable"
    try:
        for attempt in range(_PREVIEW_WAL_COPY_ATTEMPTS):
            wal_path = Path(f"{cfg.db_path}-wal")
            use_wal_copy = wal_path.is_file() and wal_path.stat().st_size > 0

            if not use_wal_copy:
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    temp_dir = None
                uri = f"file:{cfg.db_path.resolve().as_posix()}?mode=ro&immutable=1"
                conn = _open_preview_uri(uri)
                return conn, db_mod.schema_version(conn), True, None

            if temp_dir is None:
                temp_dir = Path(tempfile.mkdtemp(prefix="magicite-mig-preview-"))
            dest = temp_dir / cfg.db_path.name
            _clear_preview_copy_bundle(dest)

            before_db = _file_identity(cfg.db_path)
            before_wal = _file_identity(wal_path)
            if before_db is None:
                last_problem = "db-missing"
                time.sleep(0.01 * (attempt + 1))
                continue

            shutil.copy2(cfg.db_path, dest)
            # Never copy -shm; SQLite recreates it beside the temp copy.
            if wal_path.is_file() and wal_path.stat().st_size > 0:
                shutil.copy2(wal_path, Path(f"{dest}-wal"))

            after_db = _file_identity(cfg.db_path)
            after_wal = _file_identity(wal_path)
            if before_db != after_db or before_wal != after_wal:
                last_problem = "unstable"
                time.sleep(0.01 * (attempt + 1))
                continue

            try:
                uri = f"file:{dest.resolve().as_posix()}?mode=ro"
                conn = _open_preview_uri(uri)
            except (sqlite3.Error, OSError) as exc:
                last_problem = f"validate:{exc}"
                time.sleep(0.01 * (attempt + 1))
                continue
            return conn, db_mod.schema_version(conn), True, temp_dir

        raise BusyError(
            "database is being written; stop the server or retry preview",
            hint="checkpoint or stop writers, then retry migration preview",
            details={
                "attempts": _PREVIEW_WAL_COPY_ATTEMPTS,
                "last_problem": last_problem,
                "db_path": str(cfg.db_path),
            },
        )
    except Exception:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _durable_journal_append(path: Path, line: dict[str, Any]) -> None:
    _mkdir_secure(path.parent)
    payload = json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n"
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    _fsync_dir(path.parent)


def _with_full_sync(conn: sqlite3.Connection) -> None:
    """Bump durability for journal step commits (C8 crash safety)."""
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA wal_checkpoint(FULL)")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _invoke_fault(hook: FaultHook | None, boundary: str) -> None:
    if hook is not None:
        hook(boundary)


def _cross_process_lease(cfg: Config, conn: sqlite3.Connection, holder: str) -> Any:
    return writer_guard.registry_writer_lease(
        cfg,
        conn,
        holder=holder,
    )


def _scan_registry_files(cfg: Config) -> list[Path]:
    if not cfg.registry_dir.is_dir():
        return []
    return sorted(cfg.registry_dir.glob("*.egr.md"))


def _input_digests(cfg: Config) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in _scan_registry_files(cfg):
        rel = path.relative_to(cfg.project_root).as_posix()
        digests[rel] = _sha256_file(path)
    if cfg.db_path.is_file():
        digests[_rel_to_data(cfg, cfg.db_path)] = _sha256_file(cfg.db_path)
    if cfg.approvals_dir.is_dir():
        for path in sorted(cfg.approvals_dir.glob("*.json")):
            digests[_rel_to_data(cfg, path)] = _sha256_file(path)
    return digests


def _revision_map(cfg: Config, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    mapping: dict[str, int] = {}
    if conn is not None:
        for row in conn.execute("SELECT id, name, version FROM engram").fetchall():
            mapping[str(row["id"])] = int(row["version"])
            mapping[str(row["name"])] = int(row["version"])
    for path in _scan_registry_files(cfg):
        try:
            artifact, _doc = load_artifact_file(
                path, registry_root=cfg.project_root, require_asset_files=False
            )
        except EngramParseError:
            continue
        if isinstance(artifact, Engram):
            fm = artifact.frontmatter
            mapping[fm.id] = int(fm.version)
            mapping[fm.name] = int(fm.version)
        else:
            fm_v1 = artifact.frontmatter
            mapping[fm_v1.id] = int(fm_v1.version)
            mapping[fm_v1.name] = int(fm_v1.version)
    return mapping


def _build_artifact_plan(
    cfg: Config,
    path: Path,
    *,
    revision_map: dict[str, int],
    target_format: str,
) -> ArtifactPlan:
    artifact, _doc = load_artifact_file(path, registry_root=cfg.project_root, require_asset_files=False)
    relpath = path.relative_to(cfg.project_root).as_posix()
    source_sha = _sha256_file(path)

    if isinstance(artifact, Engram):
        source_format = artifact.frontmatter.spec
        if source_format not in SUPPORTED_SOURCE_ENGRAM_FORMATS:
            raise InvalidInputError(
                f"unsupported source engram format {source_format!r} at {relpath}",
                details={"path": relpath, "spec": source_format},
            )
        result = transform_0_2_to_1_0(artifact, revision_map=revision_map)
        trust = artifact.frontmatter.trust
        return ArtifactPlan(
            relpath=relpath,
            engram_id=artifact.frontmatter.id,
            name=artifact.frontmatter.name,
            source_format=source_format,
            target_format=target_format,
            source_sha256=source_sha,
            diagnostics=result.diagnostics,
            ok_for_composition=result.ok_for_composition,
            verification_status=trust.verification_status if trust else None,
            origin=artifact.frontmatter.provenance,
        )

    # Already 1.0 — report as no-op plan (apply skips rewrite).
    fm = artifact.frontmatter
    return ArtifactPlan(
        relpath=relpath,
        engram_id=fm.id,
        name=fm.name,
        source_format=fm.spec,
        target_format=target_format,
        source_sha256=source_sha,
        diagnostics=(),
        ok_for_composition=True,
        verification_status=fm.origin.verification_status if fm.origin else None,
        origin=fm.origin.channel if fm.origin else None,
    )


def preview(
    cfg: Config,
    conn: sqlite3.Connection | None = None,
    *,
    target_format: str = "engram/1.0",
) -> MigrationPreview:
    """Dry-run migration plan. Guaranteed zero durable writes (AC-S03-01).

    Preview never creates directories or files under the project/data dir
    (no ``ensure_dirs``). If the data dir / DB is missing, returns an empty
    plan (nothing to migrate / not initialized).

    When ``conn`` is omitted and a DB already exists, it is opened read-only
    without writing into the data dir: ``mode=ro&immutable=1`` when no
    non-empty ``-wal`` is present; otherwise a private temp copy of
    db+wal+shm is opened (see :func:`_connect_preview_readonly`).
    """
    if target_format not in SUPPORTED_TARGET_ENGRAM_FORMATS:
        raise InvalidInputError(
            f"unsupported target format {target_format!r}",
            details={"supported": sorted(SUPPORTED_TARGET_ENGRAM_FORMATS)},
        )

    own_conn = False
    temp_dir: Path | None = None
    if conn is None:
        # Zero-write: do not ensure_dirs — missing data dir → empty plan.
        conn, schema_ver, own_conn, temp_dir = _connect_preview_readonly(cfg)
    else:
        db_mod.assert_schema_supported(conn)
        schema_ver = db_mod.schema_version(conn)
    try:
        # Capture digests *before* any incidental filesystem touches.
        digests_before = _input_digests(cfg)
        revision_map = _revision_map(cfg, conn)
        plans: list[ArtifactPlan] = []
        for path in _scan_registry_files(cfg):
            plans.append(
                _build_artifact_plan(cfg, path, revision_map=revision_map, target_format=target_format)
            )

        eligibility: list[EligibilityDelta] = []
        blocking: list[dict[str, Any]] = []
        for plan in plans:
            if plan.source_format == target_format:
                continue
            codes = tuple(sorted({d.code for d in plan.diagnostics}))
            # Live eligibility is S06; S03 only surfaces transform diagnostics.
            eligibility.append(
                EligibilityDelta(
                    engram_id=plan.engram_id,
                    before_ok_for_composition=None,
                    after_ok_for_composition=None,
                    reason_codes=codes,
                )
            )
            for diag in plan.diagnostics:
                if diag.code == "depends_on_unpinned":
                    blocking.append(
                        {
                            "engram_id": plan.engram_id,
                            "code": diag.code,
                            "message": diag.message,
                            "detail": diag.detail,
                        }
                    )

        preview_body = {
            "kind": "upgrade_engram_0_2_to_1_0",
            "source_format": "engram/0.2",
            "target_format": target_format,
            "input_digests": digests_before,
            "eligibility_diff": "unevaluated",
            "artifacts": [
                {
                    "relpath": p.relpath,
                    "engram_id": p.engram_id,
                    "source_sha256": p.source_sha256,
                    "ok_for_composition": p.ok_for_composition,
                    "diagnostic_codes": [d.code for d in p.diagnostics],
                    "verification_status": p.verification_status,
                    "origin": p.origin,
                }
                for p in plans
            ],
            "storage_schema_version": schema_ver,
        }
        result = MigrationPreview(
            kind="upgrade_engram_0_2_to_1_0",
            source_format="engram/0.2",
            target_format=target_format,
            input_digests=digests_before,
            artifact_plans=tuple(plans),
            eligibility_deltas=tuple(eligibility),
            blocking_diagnostics=tuple(blocking),
            preview_digest=_digest_payload(preview_body),
            storage_schema_version=schema_ver,
            eligibility_diff="unevaluated",
        )
        digests_after = _input_digests(cfg)
        if digests_after != digests_before:
            raise RuntimeError("migration preview mutated durable bytes (invariant violation)")
        return result
    finally:
        if own_conn and conn is not None:
            conn.close()
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def status(cfg: Config, operation_id: str, conn: sqlite3.Connection | None = None) -> MigrationStatus:
    if operation_id.startswith("legacy-"):
        records = _reviewed_history(cfg, operation_id)
        if not records:
            raise NotFoundError("unknown authenticated migration operation")
        begin = records[0]["payload"]
        completed = any(row["payload"]["phase"] == "COMPLETE" for row in records)
        return MigrationStatus(
            operation_id=operation_id,
            kind="upgrade_engram_0_2_to_1_0",
            state="completed" if completed else "running",
            source_format="legacy-reviewed",
            target_format="engram/1.0",
            backup_relpath=None,
            preview_digest=begin["manifest_digest"],
            manifest_digest=begin["backup_digest"],
            created_at="",
            updated_at="",
            completed_at=None,
            error_message=None,
            steps_done=tuple(row["record_id"] for row in records),
        )
    own_conn = conn is None
    temp_dir = None
    if own_conn:
        conn, _schema, own_conn, temp_dir = _connect_preview_readonly(cfg)
    if conn is None:
        raise NotFoundError(f"unknown migration operation {operation_id!r}")
    try:
        row = ops.get_migration_operation(conn, operation_id)
        if row is None:
            raise NotFoundError(
                f"unknown migration operation {operation_id!r}",
                hint="pass the operation_id returned by apply/preview orchestration",
            )
        steps = tuple(ops.list_journal_steps(conn, operation_id).keys())
        return MigrationStatus(
            operation_id=str(row["operation_id"]),
            kind=str(row["kind"]),
            state="reconciliation_required",
            source_format=str(row["source_format"]),
            target_format=str(row["target_format"]),
            backup_relpath=row["backup_relpath"],
            preview_digest=row["preview_digest"],
            manifest_digest=row["manifest_digest"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=row["completed_at"],
            error_message="historical SQL migration status is not authenticated completion",
            steps_done=steps,
        )
    finally:
        if own_conn:
            conn.close()
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _write_backup_manifest(
    cfg: Config,
    operation_id: str,
    *,
    preview: MigrationPreview,
    file_entries: list[dict[str, Any]],
) -> tuple[Path, str]:
    manifest = {
        "manifest_kind": MANIFEST_KIND,
        "schema_version": 1,
        "operation_id": operation_id,
        "created_at": _now(),
        "source_engram_format": preview.source_format,
        "target_engram_format": preview.target_format,
        "storage_schema_version": preview.storage_schema_version,
        "max_known_schema_version_at_backup": MAX_KNOWN_SCHEMA_VERSION,
        "preview_digest": preview.preview_digest,
        "files": sorted(file_entries, key=lambda e: e["path"]),
        "authoritative_trees": list(AUTHORITATIVE_TREE_NAMES),
    }
    manifest_path = _operation_dir(cfg, operation_id) / "manifest.json"
    payload = _canonical_json(manifest)
    _write_bytes_durable(manifest_path, payload)
    return manifest_path, hashlib.sha256(payload).hexdigest()


def _copy_authoritative_backup(
    cfg: Config,
    operation_id: str,
    *,
    fault_hook: FaultHook | None = None,
) -> list[dict[str, Any]]:
    """Build a durable backup tree. Safe to rebuild until STEP_BACKUP commits."""
    backup_root = _backup_dir(cfg, operation_id)
    if backup_root.exists():
        shutil.rmtree(backup_root)
    _mkdir_secure(backup_root)
    _mkdir_secure(_operation_dir(cfg, operation_id))

    entries: list[dict[str, Any]] = []

    def _copy_file(src: Path, dest_rel: str) -> None:
        dest = backup_root / dest_rel
        meta = _copy_file_durable(src, dest)
        entries.append(
            {
                "path": dest_rel,
                "sha256": meta["sha256"],
                "size": meta["size"],
                "source_sha256": meta["source_sha256"],
            }
        )
        _invoke_fault(fault_hook, f"boundary:backup_file:{dest_rel}")

    for path in _scan_registry_files(cfg):
        _copy_file(path, f"engrams/{path.name}")

    if cfg.approvals_dir.is_dir():
        for path in sorted(cfg.approvals_dir.glob("*.json")):
            _copy_file(path, f"approvals/{path.name}")

    if cfg.archive_dir.is_dir():
        for path in sorted(cfg.archive_dir.rglob("*")):
            if path.is_file() and not _is_symlink(path):
                rel = path.relative_to(cfg.archive_dir).as_posix()
                _copy_file(path, f"archive/{rel}")

    # Checkpoint the DB after a WAL checkpoint so the backup is self-contained.
    if cfg.db_path.is_file():
        conn = db_mod.connect(cfg.db_path, migrate=False)
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            conn.close()
        _copy_file(cfg.db_path, "skill-graph.db")

    _invoke_fault(fault_hook, "boundary:backup_files_copied")
    return entries


def _step_done(ctx: _ApplyContext, step_key: str) -> bool:
    return step_key in ops.list_journal_steps(ctx.conn, ctx.operation_id)


def _commit_step(
    ctx: _ApplyContext,
    step_key: str,
    *,
    detail: dict[str, Any] | None = None,
    boundary: str | None = None,
) -> None:
    _with_full_sync(ctx.conn)
    ops.record_journal_step(
        ctx.conn,
        operation_id=ctx.operation_id,
        step_key=step_key,
        detail=detail,
    )
    _with_full_sync(ctx.conn)
    # Append-only file journal for forensics (retained even on restore).
    journal_path = _operation_dir(ctx.cfg, ctx.operation_id) / "journal.jsonl"
    _durable_journal_append(
        journal_path,
        {
            "ts": _now(),
            "operation_id": ctx.operation_id,
            "step_key": step_key,
            "detail": detail or {},
        },
    )
    ctx.steps_done.append(step_key)
    _invoke_fault(ctx.fault_hook, boundary or f"step:{step_key}")


def _ensure_backup(ctx: _ApplyContext) -> str:
    if _step_done(ctx, STEP_BACKUP):
        row = ops.get_migration_operation(ctx.conn, ctx.operation_id)
        assert row is not None and row["backup_relpath"]
        return str(row["backup_relpath"])

    # Rebuild from scratch until STEP_BACKUP is committed (partial-backup resume).
    entries = _copy_authoritative_backup(ctx.cfg, ctx.operation_id, fault_hook=ctx.fault_hook)
    _manifest_path, manifest_digest = _write_backup_manifest(
        ctx.cfg, ctx.operation_id, preview=ctx.preview, file_entries=entries
    )
    backup_relpath = _rel_to_data(ctx.cfg, _backup_dir(ctx.cfg, ctx.operation_id))
    ops.upsert_migration_operation(
        ctx.conn,
        operation_id=ctx.operation_id,
        kind=ctx.preview.kind,
        state="running",
        source_format=ctx.preview.source_format,
        target_format=ctx.preview.target_format,
        backup_relpath=backup_relpath,
        preview_digest=ctx.preview.preview_digest,
        manifest_digest=manifest_digest,
    )
    _commit_step(
        ctx,
        STEP_BACKUP,
        detail={"backup_relpath": backup_relpath, "manifest_digest": manifest_digest},
        boundary="boundary:backup_committed",
    )
    return backup_relpath


def _artifact_step_key(relpath: str) -> str:
    return f"artifact:{relpath}"


def _migrate_artifacts(ctx: _ApplyContext) -> None:
    revision_map = _revision_map(ctx.cfg, ctx.conn)
    for plan in ctx.preview.artifact_plans:
        step_key = _artifact_step_key(plan.relpath)
        if _step_done(ctx, step_key):
            continue
        path = ctx.cfg.project_root / plan.relpath
        if plan.source_format == ctx.preview.target_format:
            # Already target format (or resume after precommit write before journal).
            _commit_step(
                ctx,
                step_key,
                detail={
                    "engram_id": plan.engram_id,
                    "source_sha256": plan.source_sha256,
                    "target_sha256": _sha256_file(path),
                    "already_target_format": True,
                },
                boundary=f"boundary:file:{plan.relpath}",
            )
            continue

        parsed = parse_file(path, registry_root=ctx.cfg.project_root)
        write_engram_as(
            path,
            parsed.engram,
            target_format="engram/1.0",
            revision_map=revision_map,
        )
        # True commit-boundary fault: bytes on disk, journal step not yet durable.
        _invoke_fault(ctx.fault_hook, f"precommit:file:{plan.relpath}")
        _commit_step(
            ctx,
            step_key,
            detail={
                "engram_id": plan.engram_id,
                "source_sha256": plan.source_sha256,
                "target_sha256": _sha256_file(path),
            },
            boundary=f"boundary:file:{plan.relpath}",
        )


def _update_db_mirrors(ctx: _ApplyContext) -> None:
    if _step_done(ctx, STEP_DB_MIRROR):
        return
    # Always refresh from on-disk artifacts so resume after a mid-loop fault
    # still converges (preview may already report target_format for rewritten files).
    for path in _scan_registry_files(ctx.cfg):
        artifact, _doc = load_artifact_file(
            path, registry_root=ctx.cfg.project_root, require_asset_files=False
        )
        if isinstance(artifact, Engram):
            engram_id = artifact.frontmatter.id
            spec: str = artifact.frontmatter.spec
            body_sha = artifact.body_sha256
        else:
            engram_id = artifact.frontmatter.id
            spec = str(artifact.frontmatter.spec)
            body_sha = artifact.body_sha256
        content_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        ops.update_engram_format_mirror(
            ctx.conn,
            engram_id=engram_id,
            spec_version=spec,
            content_sha256=content_sha,
            body_sha256=body_sha or content_sha,
            file_mtime_ns=path.stat().st_mtime_ns,
            path=path.relative_to(ctx.cfg.project_root).as_posix(),
        )
        _invoke_fault(ctx.fault_hook, f"boundary:db_mirror:{engram_id}")
    _commit_step(ctx, STEP_DB_MIRROR, boundary="boundary:db_mirror_committed")


def _finalize(ctx: _ApplyContext) -> None:
    if _step_done(ctx, STEP_COMPLETED):
        return
    ops.upsert_migration_operation(
        ctx.conn,
        operation_id=ctx.operation_id,
        kind=ctx.preview.kind,
        state="completed",
        source_format=ctx.preview.source_format,
        target_format=ctx.preview.target_format,
        preview_digest=ctx.preview.preview_digest,
        completed_at=_now(),
        error_message=None,
    )
    _commit_step(ctx, STEP_COMPLETED, boundary="boundary:operation_completed")


def _run_apply(ctx: _ApplyContext) -> MigrationResult:
    ops.upsert_migration_operation(
        ctx.conn,
        operation_id=ctx.operation_id,
        kind=ctx.preview.kind,
        state="running",
        source_format=ctx.preview.source_format,
        target_format=ctx.preview.target_format,
        preview_digest=ctx.preview.preview_digest,
    )
    try:
        backup_relpath = _ensure_backup(ctx)
        _migrate_artifacts(ctx)
        _update_db_mirrors(ctx)
        _finalize(ctx)
    except BaseException as exc:
        if not isinstance(exc, SystemExit):
            ops.upsert_migration_operation(
                ctx.conn,
                operation_id=ctx.operation_id,
                kind=ctx.preview.kind,
                state="failed",
                source_format=ctx.preview.source_format,
                target_format=ctx.preview.target_format,
                preview_digest=ctx.preview.preview_digest,
                error_message=f"{type(exc).__name__}: {exc}",
            )
        raise

    steps = tuple(ops.list_journal_steps(ctx.conn, ctx.operation_id).keys())
    return MigrationResult(
        operation_id=ctx.operation_id,
        state="completed",
        kind=ctx.preview.kind,
        preview_digest=ctx.preview.preview_digest,
        backup_relpath=backup_relpath,
        steps_done=steps,
        resumed=ctx.resumed,
        duplicate_noop=False,
    )


def _reviewed_history(cfg: Config, operation_id: str) -> tuple[dict[str, Any], ...]:
    from magicite.core.trust_journal import TrustJournal

    registry_id, client = writer_guard.resolve_custody(cfg)
    journal = TrustJournal(cfg.data_dir / "trust/authority", registry_id, client)
    _, records = journal._remote(allow_pending=True)
    migration_id = operation_id.removeprefix("legacy-")
    return tuple(
        row
        for row in records
        if row["kind"] == "legacy_reconciliation" and row["payload"]["migration_id"] == migration_id
    )


def apply(
    cfg: Config,
    conn: sqlite3.Connection | None = None,
    *,
    operation_id: str | None = None,
    target_format: str = "engram/1.0",
    fault_hook: FaultHook | None = None,
    reviewed_sha256: str | None = None,
    backup_path: str | Path | None = None,
) -> MigrationResult:
    """Apply only the explicitly reviewed, backed-up authenticated migration."""
    from magicite.core import trust_legacy

    if not reviewed_sha256 or backup_path is None:
        raise InvalidInputError(
            "reviewed migration manifest and complete backup are required",
            hint="run custody legacy-preview, custody legacy-backup, then migration apply "
            "with --reviewed-sha256 and --backup-path",
        )
    if target_format != "engram/1.0":
        raise InvalidInputError("unsupported reviewed migration target")
    # Verify the complete backup before resolving identity or touching local state.
    trust_legacy.read_verified_backup(Path(backup_path), reviewed_sha256=reviewed_sha256)
    canonical_id = "legacy-" + reviewed_sha256[:32]
    if operation_id is not None and operation_id != canonical_id:
        raise InvalidInputError("operation_id does not match the reviewed manifest")
    before = _reviewed_history(cfg, canonical_id)
    complete = any(row["payload"]["phase"] == "COMPLETE" for row in before)
    result = trust_legacy.apply_reviewed(
        cfg, backup_path=Path(backup_path), reviewed_sha256=reviewed_sha256, fault_hook=fault_hook
    )
    return MigrationResult(
        operation_id=canonical_id,
        state="completed",
        kind="upgrade_engram_0_2_to_1_0",
        preview_digest=result["manifest_digest"],
        backup_relpath=str(backup_path),
        steps_done=("reviewed_backup_verified", "authenticated_reconciliation_completed"),
        resumed=bool(before),
        duplicate_noop=complete,
    )


def resume(
    cfg: Config,
    operation_id: str,
    conn: sqlite3.Connection | None = None,
    *,
    fault_hook: FaultHook | None = None,
    reviewed_sha256: str | None = None,
    backup_path: str | Path | None = None,
) -> MigrationResult:
    """Resume the same reviewed plan; SQL migration rows confer no authority."""
    return apply(
        cfg,
        conn,
        operation_id=operation_id,
        fault_hook=fault_hook,
        reviewed_sha256=reviewed_sha256,
        backup_path=backup_path,
    )


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInputError(
            f"unreadable backup manifest at {manifest_path}: {exc}",
        ) from exc
    kind = payload.get("manifest_kind")
    if kind not in SUPPORTED_BACKUP_MANIFEST_KINDS:
        raise InvalidInputError(
            f"unsupported backup manifest kind {kind!r}",
            hint="restore requires a matching pre-upgrade snapshot from this Magicite major",
            details={
                "manifest_kind": kind,
                "supported": sorted(SUPPORTED_BACKUP_MANIFEST_KINDS),
            },
        )
    schema_version = int(payload.get("schema_version", 0))
    if schema_version > 1:
        raise InvalidInputError(
            f"backup manifest schema_version {schema_version} is newer than supported",
            details={"schema_version": schema_version, "max_supported": 1},
        )
    storage_ver = int(payload.get("storage_schema_version", 0))
    if storage_ver > MAX_KNOWN_SCHEMA_VERSION:
        raise InvalidInputError(
            f"backup references storage schema {storage_ver} newer than this build "
            f"(max={MAX_KNOWN_SCHEMA_VERSION})",
            details={
                "storage_schema_version": storage_ver,
                "max_known_schema_version": MAX_KNOWN_SCHEMA_VERSION,
            },
        )
    return payload


def _verify_backup_files(backup_root: Path, manifest: dict[str, Any]) -> None:
    """Validate every manifest entry BEFORE any restore I/O into the live tree."""
    root = backup_root.resolve()
    for entry in manifest.get("files", []):
        rel = entry["path"]
        path = _constrained_under(root, rel, label="backup manifest")
        if _is_symlink(path):
            raise InvalidInputError(
                f"backup entry is a symlink: {rel!r}",
                details={"path": rel},
            )
        if not path.is_file():
            raise InvalidInputError(f"backup missing file {rel!r}")
        digest = _sha256_file(path)
        if digest != entry["sha256"]:
            raise InvalidInputError(
                f"backup file digest mismatch for {rel!r}",
                details={"expected": entry["sha256"], "actual": digest},
            )
        if path.stat().st_size != int(entry["size"]):
            raise InvalidInputError(f"backup file size mismatch for {rel!r}")


def _resolve_backup_paths(backup_path: str | Path) -> tuple[Path, Path]:
    """Return ``(backup_root, manifest_path)`` for a restore input path."""
    root = Path(backup_path).resolve()
    if not root.exists():
        raise NotFoundError(f"backup path not found: {backup_path}")

    if root.is_file() and root.name == "manifest.json":
        manifest_path = root
        candidate = root.parent / "backup"
        if not candidate.is_dir():
            raise InvalidInputError(f"manifest {root} has no sibling backup/ directory")
        return candidate, manifest_path

    if not root.is_dir():
        raise InvalidInputError(f"backup path is not a directory: {backup_path}")

    if root.name == "backup" and (root.parent / "manifest.json").is_file():
        return root, root.parent / "manifest.json"

    if (root / "manifest.json").is_file() and (root / "backup").is_dir():
        return root / "backup", root / "manifest.json"

    if (root / "manifest.json").is_file():
        return root, root / "manifest.json"

    raise InvalidInputError(f"no manifest.json adjacent to backup at {backup_path}")


def _restore_tree(
    src: Path,
    dest: Path,
    *,
    data_root: Path,
    clear_glob: str | None = None,
) -> None:
    """Copy ``src`` → ``dest``, constraining every path under ``data_root``."""
    dest_resolved = dest.resolve()
    data_resolved = data_root.resolve()
    try:
        dest_resolved.relative_to(data_resolved)
    except ValueError as exc:
        raise InvalidInputError(
            f"restore destination escapes data_dir: {dest}",
            details={"dest": str(dest), "data_dir": str(data_root)},
        ) from exc

    _mkdir_secure(dest)
    if clear_glob is not None:
        for stale in dest.glob(clear_glob):
            if stale.is_file() and not _is_symlink(stale):
                stale.unlink()
    elif dest.exists():
        for child in dest.iterdir():
            if _is_symlink(child):
                raise InvalidInputError(f"refusing to clear symlink in restore dest: {child}")
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    if not src.is_dir():
        return
    src_root = src.resolve()
    for path in src.rglob("*"):
        if _is_symlink(path):
            raise InvalidInputError(
                f"backup tree contains symlink: {path}",
                details={"path": str(path)},
            )
        if not path.is_file():
            continue
        rel = path.relative_to(src_root).as_posix()
        _constrained_under(src_root, rel, label="backup tree")
        target = _constrained_under(dest_resolved, rel, label="restore target")
        _mkdir_secure(target.parent)
        _copy_file_durable(path, target)


def restore(
    cfg: Config,
    *,
    backup_path: str | Path,
    conn: sqlite3.Connection | None = None,
    operation_id: str | None = None,
    fault_hook: FaultHook | None = None,
    staging_path: str | Path | None = None,
    reviewed_sha256: str | None = None,
) -> MigrationResult:
    """Materialize a verified legacy backup into restricted, inactive staging."""
    from magicite.core import trust_legacy
    from magicite.core.trust_journal import _directory_fd, _read_file

    if staging_path is None:
        raise InvalidInputError(
            "legacy restore requires an explicit inactive staging path",
            hint="use --staging-path outside the active project; reconcile with reviewed legacy apply",
        )
    backup_root, manifest_path = _resolve_backup_paths(backup_path)
    if reviewed_sha256 is not None:
        _, manifest = trust_legacy.read_verified_backup(backup_root, reviewed_sha256=reviewed_sha256)
        source_root = backup_root / "files"
    else:
        manifest = _load_manifest(manifest_path)
        _verify_backup_files(backup_root, manifest)
        source_root = backup_root
    destination = Path(staging_path).absolute()
    # Never overwrite the active registry or the archive being recovered.
    for protected in (cfg.project_root.resolve(), backup_root.resolve()):
        if destination.resolve().is_relative_to(protected) or protected.is_relative_to(destination.resolve()):
            raise InvalidInputError("restore staging must be separate from project and backup")
    commitment = trust_legacy.digest(manifest)
    canonical_id = "stage-" + commitment[:32]
    if operation_id is not None and operation_id != canonical_id:
        raise InvalidInputError("restore operation_id does not match the backup")
    intent_path = destination / "restore-intent.json"
    intent = trust_legacy.canonical(
        {"schema": "RestrictedLegacyRestoreIntent/1", "manifest_digest": commitment}
    )
    if destination.exists():
        with _directory_fd(destination) as directory:
            try:
                existing_intent = _read_file(directory, intent_path.name)
            except FileNotFoundError:
                if any(destination.iterdir()):
                    raise InvalidInputError("restore staging is not empty or bound to this backup") from None
            else:
                if existing_intent != intent:
                    raise InvalidInputError("restore staging belongs to a different backup")
    trust_legacy._write_new_or_exact(intent_path, intent, lambda: None)
    marker = destination / "reconciliation-required.json"
    duplicate = marker.exists()
    # Copy only committed regular-file bytes; no active schema/DB/trust writes.
    entries = (
        [{"path": key, **value} for key, value in manifest["files"].items()]
        if reviewed_sha256 is not None
        else manifest["files"]
    )
    for entry in entries:
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise InvalidInputError("invalid backup member")
        with _directory_fd(source_root / rel.parent) as directory:
            raw = _read_file(directory, rel.name)
        if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise InvalidInputError("backup member changed before staged restore")
        trust_legacy._write_new_or_exact(destination / "files" / rel, raw, lambda: None)
        _invoke_fault(fault_hook, "staged_file:" + rel.as_posix())
    trust_legacy._write_new_or_exact(
        marker,
        trust_legacy.canonical(
            {
                "schema": "RestrictedLegacyRestore/1",
                "manifest_digest": commitment,
                "status": "reconciliation_required",
                "active": False,
                "next_action": "reviewed legacy reconciliation; staged bytes confer no trust",
            }
        ),
        lambda: None,
    )
    return MigrationResult(
        operation_id=canonical_id,
        state="reconciliation_required",
        kind="restore_backup",
        preview_digest=manifest.get("preview_digest", reviewed_sha256),
        backup_relpath=str(destination),
        steps_done=("restricted_backup_materialized",),
        duplicate_noop=duplicate,
    )


# Re-export index pointer helpers for S05 (storage primitives owned by S03).
begin_index_generation = ops.begin_index_generation
complete_index_generation = ops.complete_index_generation
fail_index_generation = ops.fail_index_generation
publish_index_generation = ops.publish_index_generation
rollback_index_generation = ops.rollback_index_generation
active_index_generation = ops.active_index_generation


def preview_as_dict(preview_result: MigrationPreview) -> dict[str, Any]:
    """JSON-friendly projection for S11 CLI/MCP bindings."""
    return {
        "kind": preview_result.kind,
        "source_format": preview_result.source_format,
        "target_format": preview_result.target_format,
        "preview_digest": preview_result.preview_digest,
        "storage_schema_version": preview_result.storage_schema_version,
        "eligibility_diff": preview_result.eligibility_diff,
        "input_digests": preview_result.input_digests,
        "artifact_plans": [
            {
                "relpath": p.relpath,
                "engram_id": p.engram_id,
                "name": p.name,
                "source_format": p.source_format,
                "target_format": p.target_format,
                "source_sha256": p.source_sha256,
                "ok_for_composition": p.ok_for_composition,
                "verification_status": p.verification_status,
                "origin": p.origin,
                "diagnostics": [asdict(d) for d in p.diagnostics],
            }
            for p in preview_result.artifact_plans
        ],
        "eligibility_deltas": [asdict(d) for d in preview_result.eligibility_deltas],
        "blocking_diagnostics": list(preview_result.blocking_diagnostics),
    }
