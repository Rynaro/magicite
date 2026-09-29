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
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from magicite.config import Config
from magicite.engram.model import Engram
from magicite.engram.parser import EngramParseError, parse_artifact_file, parse_file
from magicite.engram.transform import TransformDiagnostic, transform_0_2_to_1_0
from magicite.engram.writer import write_engram_as
from magicite.errors import InvalidInputError, NotFoundError
from magicite.storage import db as db_mod
from magicite.storage import lease as lease_mod
from magicite.storage import migration_ops as ops
from magicite.storage.migrations.registry import (
    MAX_KNOWN_SCHEMA_VERSION,
    SUPPORTED_BACKUP_MANIFEST_KINDS,
    SUPPORTED_SOURCE_ENGRAM_FORMATS,
    SUPPORTED_TARGET_ENGRAM_FORMATS,
)

MigrationKind = Literal["upgrade_engram_0_2_to_1_0", "restore_backup"]
OperationState = Literal["pending", "running", "completed", "failed", "restored"]
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
    before_ok_for_composition: bool
    after_ok_for_composition: bool
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


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _invoke_fault(hook: FaultHook | None, boundary: str) -> None:
    if hook is not None:
        hook(boundary)


def _cross_process_lease(cfg: Config, conn: sqlite3.Connection, holder: str) -> Any:
    return lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path,
        conn=conn,
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
            artifact, _doc = parse_artifact_file(path, registry_root=cfg.project_root)
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
    artifact, _doc = parse_artifact_file(path, registry_root=cfg.project_root)
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
    """Dry-run migration plan. Guaranteed zero durable writes (AC-S03-01)."""
    if target_format not in SUPPORTED_TARGET_ENGRAM_FORMATS:
        raise InvalidInputError(
            f"unsupported target format {target_format!r}",
            details={"supported": sorted(SUPPORTED_TARGET_ENGRAM_FORMATS)},
        )

    own_conn = conn is None
    if own_conn:
        cfg.ensure_dirs()
        conn = db_mod.connect(cfg.db_path, migrate=True)
    assert conn is not None
    try:
        db_mod.assert_schema_supported(conn)
        schema_ver = db_mod.schema_version(conn)
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
            before_ok = True  # 0.2 artifacts were already admitted
            after_ok = plan.ok_for_composition
            eligibility.append(
                EligibilityDelta(
                    engram_id=plan.engram_id,
                    before_ok_for_composition=before_ok,
                    after_ok_for_composition=after_ok,
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
        )
        digests_after = _input_digests(cfg)
        if digests_after != digests_before:
            raise RuntimeError("migration preview mutated durable bytes (invariant violation)")
        return result
    finally:
        if own_conn:
            conn.close()


def status(cfg: Config, operation_id: str, conn: sqlite3.Connection | None = None) -> MigrationStatus:
    own_conn = conn is None
    if own_conn:
        cfg.ensure_dirs()
        conn = db_mod.connect(cfg.db_path, migrate=True)
    assert conn is not None
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
            state=cast(OperationState, str(row["state"])),
            source_format=str(row["source_format"]),
            target_format=str(row["target_format"]),
            backup_relpath=row["backup_relpath"],
            preview_digest=row["preview_digest"],
            manifest_digest=row["manifest_digest"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=row["completed_at"],
            error_message=row["error_message"],
            steps_done=steps,
        )
    finally:
        if own_conn:
            conn.close()


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
    manifest_path.write_bytes(payload)
    return manifest_path, hashlib.sha256(payload).hexdigest()


def _copy_authoritative_backup(cfg: Config, operation_id: str) -> list[dict[str, Any]]:
    backup_root = _backup_dir(cfg, operation_id)
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True)

    entries: list[dict[str, Any]] = []

    def _copy_file(src: Path, dest_rel: str) -> None:
        dest = backup_root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        entries.append(
            {
                "path": dest_rel,
                "sha256": _sha256_file(dest),
                "size": dest.stat().st_size,
            }
        )

    for path in _scan_registry_files(cfg):
        _copy_file(path, f"engrams/{path.name}")

    if cfg.approvals_dir.is_dir():
        for path in sorted(cfg.approvals_dir.glob("*.json")):
            _copy_file(path, f"approvals/{path.name}")

    if cfg.archive_dir.is_dir():
        for path in sorted(cfg.archive_dir.rglob("*")):
            if path.is_file():
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
    ops.record_journal_step(
        ctx.conn,
        operation_id=ctx.operation_id,
        step_key=step_key,
        detail=detail,
    )
    # Append-only file journal for forensics (retained even on restore).
    journal_path = _operation_dir(ctx.cfg, ctx.operation_id) / "journal.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": _now(),
        "operation_id": ctx.operation_id,
        "step_key": step_key,
        "detail": detail or {},
    }
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
    ctx.steps_done.append(step_key)
    _invoke_fault(ctx.fault_hook, boundary or f"step:{step_key}")


def _ensure_backup(ctx: _ApplyContext) -> str:
    if _step_done(ctx, STEP_BACKUP):
        row = ops.get_migration_operation(ctx.conn, ctx.operation_id)
        assert row is not None and row["backup_relpath"]
        return str(row["backup_relpath"])

    entries = _copy_authoritative_backup(ctx.cfg, ctx.operation_id)
    _invoke_fault(ctx.fault_hook, "boundary:backup_files_copied")
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
            _commit_step(
                ctx,
                step_key,
                detail={"skipped": True, "reason": "already_target_format"},
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
    for plan in ctx.preview.artifact_plans:
        if plan.source_format == ctx.preview.target_format:
            continue
        path = ctx.cfg.project_root / plan.relpath
        artifact, _doc = parse_artifact_file(path, registry_root=ctx.cfg.project_root)
        content_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        body_sha = artifact.body_sha256 or content_sha
        ops.update_engram_format_mirror(
            ctx.conn,
            engram_id=plan.engram_id,
            spec_version=ctx.preview.target_format,
            content_sha256=content_sha,
            body_sha256=body_sha,
            file_mtime_ns=path.stat().st_mtime_ns,
            path=plan.relpath,
        )
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


def apply(
    cfg: Config,
    conn: sqlite3.Connection | None = None,
    *,
    operation_id: str | None = None,
    target_format: str = "engram/1.0",
    fault_hook: FaultHook | None = None,
) -> MigrationResult:
    """Explicit upgrade under the shared writer lease (AC-S03-02/03)."""
    cfg.ensure_dirs()
    own_conn = conn is None
    if own_conn:
        conn = db_mod.connect(cfg.db_path, migrate=True)
    assert conn is not None

    preview_result = preview(cfg, conn, target_format=target_format)
    op_id = operation_id or _new_operation_id()

    existing = ops.get_migration_operation(conn, op_id)
    if existing is not None and existing["state"] == "completed":
        steps = tuple(ops.list_journal_steps(conn, op_id).keys())
        if own_conn:
            conn.close()
        return MigrationResult(
            operation_id=op_id,
            state="completed",
            kind=cast(MigrationKind, str(existing["kind"])),
            preview_digest=existing["preview_digest"],
            backup_relpath=existing["backup_relpath"],
            steps_done=steps,
            resumed=False,
            duplicate_noop=True,
        )

    if existing is not None and existing["state"] == "restored":
        raise InvalidInputError(
            f"operation {op_id!r} was restored; allocate a new operation_id to upgrade again",
        )

    ctx = _ApplyContext(
        cfg=cfg,
        conn=conn,
        operation_id=op_id,
        preview=preview_result,
        fault_hook=fault_hook,
        resumed=existing is not None and existing["state"] in {"pending", "running", "failed"},
    )
    _operation_dir(cfg, op_id).mkdir(parents=True, exist_ok=True)

    try:
        lease = _cross_process_lease(cfg, conn, f"migration:{op_id}")
        with lease.acquire(), lease_mod.writer_lease(f"migration:{op_id}"):
            return _run_apply(ctx)
    finally:
        if own_conn:
            conn.close()


def resume(
    cfg: Config,
    operation_id: str,
    conn: sqlite3.Connection | None = None,
    *,
    fault_hook: FaultHook | None = None,
) -> MigrationResult:
    """Resume an interrupted apply; final state equals uninterrupted (AC-S03-02)."""
    cfg.ensure_dirs()
    own_conn = conn is None
    if own_conn:
        conn = db_mod.connect(cfg.db_path, migrate=True)
    assert conn is not None
    try:
        row = ops.get_migration_operation(conn, operation_id)
        if row is None:
            raise NotFoundError(f"unknown migration operation {operation_id!r}")
        if row["state"] == "completed":
            steps = tuple(ops.list_journal_steps(conn, operation_id).keys())
            return MigrationResult(
                operation_id=operation_id,
                state="completed",
                kind=cast(MigrationKind, str(row["kind"])),
                preview_digest=row["preview_digest"],
                backup_relpath=row["backup_relpath"],
                steps_done=steps,
                resumed=True,
                duplicate_noop=True,
            )
        if row["state"] == "restored":
            raise InvalidInputError(
                f"operation {operation_id!r} was restored and cannot be resumed as an upgrade"
            )
        return apply(
            cfg,
            conn,
            operation_id=operation_id,
            target_format=row["target_format"],
            fault_hook=fault_hook,
        )
    finally:
        if own_conn:
            conn.close()


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
    for entry in manifest.get("files", []):
        rel = entry["path"]
        path = backup_root / rel
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
    root = Path(backup_path)
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
        # Manifest living inside the backup tree itself.
        return root, root / "manifest.json"

    raise InvalidInputError(f"no manifest.json adjacent to backup at {backup_path}")


def _restore_tree(src: Path, dest: Path, *, clear_glob: str | None = None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if clear_glob is not None:
        for stale in dest.glob(clear_glob):
            if stale.is_file():
                stale.unlink()
    elif dest.exists():
        for child in dest.iterdir():
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    if not src.is_dir():
        return
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def restore(
    cfg: Config,
    *,
    backup_path: str | Path,
    conn: sqlite3.Connection | None = None,
    operation_id: str | None = None,
    fault_hook: FaultHook | None = None,
) -> MigrationResult:
    """Restore a matching pre-upgrade backup (AC-S03-04).

    Authoritative trees (engrams/approvals/archive) are restored byte-for-byte
    from the backup. The SQLite DB remains a rebuildable projection: mirror
    columns are refreshed from restored files. Migration journal rows are
    retained (never deleted to fake a downgrade).
    """
    cfg.ensure_dirs()
    backup_root, manifest_path = _resolve_backup_paths(backup_path)
    manifest = _load_manifest(manifest_path)
    _verify_backup_files(backup_root, manifest)

    own_conn = conn is None
    if own_conn:
        conn = db_mod.connect(cfg.db_path, migrate=True)
    assert conn is not None

    restore_op_id = operation_id or f"rst_{uuid.uuid4().hex}"
    source_op = str(manifest.get("operation_id") or "")
    try:
        backup_rel: str
        try:
            backup_rel = _rel_to_data(cfg, backup_root)
        except ValueError:
            backup_rel = str(backup_root)

        lease = _cross_process_lease(cfg, conn, f"restore:{restore_op_id}")
        with lease.acquire(), lease_mod.writer_lease(f"restore:{restore_op_id}"):
            ops.upsert_migration_operation(
                conn,
                operation_id=restore_op_id,
                kind="restore_backup",
                state="running",
                source_format=str(manifest.get("target_engram_format") or "engram/1.0"),
                target_format=str(manifest.get("source_engram_format") or "engram/0.2"),
                backup_relpath=backup_rel,
                preview_digest=manifest.get("preview_digest"),
                manifest_digest=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            )
            _invoke_fault(fault_hook, "boundary:restore_begin")

            _restore_tree(backup_root / "engrams", cfg.registry_dir, clear_glob="*.egr.md")
            _invoke_fault(fault_hook, "boundary:restore_tree:engrams")
            _restore_tree(backup_root / "approvals", cfg.approvals_dir)
            _invoke_fault(fault_hook, "boundary:restore_tree:approvals")
            _restore_tree(backup_root / "archive", cfg.archive_dir)
            _invoke_fault(fault_hook, "boundary:restore_tree:archive")

            # Refresh rebuildable mirrors from restored authoritative files.
            for path in _scan_registry_files(cfg):
                parsed = parse_file(path, registry_root=cfg.project_root)
                fm = parsed.engram.frontmatter
                content_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                ops.update_engram_format_mirror(
                    conn,
                    engram_id=fm.id,
                    spec_version=fm.spec,
                    content_sha256=content_sha,
                    body_sha256=parsed.engram.body_sha256 or content_sha,
                    file_mtime_ns=path.stat().st_mtime_ns,
                    path=path.relative_to(cfg.project_root).as_posix(),
                )
            _invoke_fault(fault_hook, "boundary:restore_mirrors")

            ops.upsert_migration_operation(
                conn,
                operation_id=restore_op_id,
                kind="restore_backup",
                state="completed",
                source_format=str(manifest.get("target_engram_format") or "engram/1.0"),
                target_format=str(manifest.get("source_engram_format") or "engram/0.2"),
                completed_at=_now(),
                error_message=None,
            )
            ops.record_journal_step(
                conn,
                operation_id=restore_op_id,
                step_key=STEP_COMPLETED,
                detail={"restored_from": source_op, "manifest": str(manifest_path)},
            )
            if source_op and ops.get_migration_operation(conn, source_op):
                src_row = ops.get_migration_operation(conn, source_op)
                assert src_row is not None
                ops.upsert_migration_operation(
                    conn,
                    operation_id=source_op,
                    kind=str(src_row["kind"]),
                    state="restored",
                    source_format=str(src_row["source_format"]),
                    target_format=str(src_row["target_format"]),
                )
            _invoke_fault(fault_hook, "boundary:restore_completed")

            steps = tuple(ops.list_journal_steps(conn, restore_op_id).keys())
            return MigrationResult(
                operation_id=restore_op_id,
                state="completed",
                kind="restore_backup",
                preview_digest=manifest.get("preview_digest"),
                backup_relpath=backup_rel,
                steps_done=steps,
            )
    finally:
        if own_conn:
            conn.close()


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
