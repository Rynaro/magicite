"""``magicite doctor`` -- the honest environment check (spec M7 story,
Risks R7/R9; S12 doctor/1).

This module is deliberately **not reassuring**. Per the M7 mission
directive it must:

- warn when lock semantics degrade on a non-local filesystem (R7:
  ``fcntl.flock()`` is unreliable/a no-op on NFS/CIFS -- the two-layer
  ``storage/lease.py::CrossProcessLease`` design exists precisely because
  of this, docs/operations.md §8), and
- report the registry's cold-start standing honestly (R9): registry size
  and its position relative to the ~50-skill heuristic docs/07
  originally proposed -- WITHOUT asserting that heuristic as an evidenced
  break-even. docs/01's Falsification Record (measured 2026-08-15)
  measured a 70-skill registry -- above the heuristic -- and found plain
  dense-embedding retrieval still beat the full pipeline (p=0.00053); see
  ``obs/kpi.py::cold_start_signal`` for the full reasoning. Overstating
  this as a resolved threshold is exactly the overselling this module
  must not do.

Framework-free (INV-1: no MCP import here) and **zero-write** (AC-S12-01):
every check either reads the filesystem/environment or opens an existing
DB via a genuine SQLite ``mode=ro`` URI. Doctor never creates directories,
WAL/shm companions, migration rows, fingerprint keys, or other durable
files under the data directory.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Literal

from magicite import config as config_mod
from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.obs import kpi as kpi_mod
from magicite.storage import db as db_mod

#: Mount fstypes known to give ``fcntl.flock()`` weak or absent semantics
#: (docs/operations.md §8, R7). Not exhaustive -- an unrecognised fstype is
#: treated as "probably local", never as "definitely safe": the DB-row
#: writer lease layer is authoritative regardless of what this list knows.
_NETWORK_FSTYPES: frozenset[str] = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb",
        "smb2",
        "smb3",
        "smbfs",
        "9p",
        "afs",
        "ceph",
        "cephfs",
        "glusterfs",
        "fuse.sshfs",
        "fuse.s3fs",
        "fuse.rclone",
    }
)

#: Local fstypes where writable service configuration is supported.
_SUPPORTED_LOCAL_FSTYPES: frozenset[str] = frozenset(
    {
        "ext2",
        "ext3",
        "ext4",
        "xfs",
        "btrfs",
        "zfs",
        "apfs",
        "hfs",
        "hfsplus",
        "ufs",
        "tmpfs",
        "overlay",
        "overlayfs",
    }
)

CheckStatus = Literal["ok", "warn", "fail", "unknown", "not_applicable"]

DOCTOR_KIND = "doctor/1"


def _detect_fstype(path: Path) -> str | None:
    """Best-effort mount-fstype lookup for ``path`` via ``/proc/mounts``
    (Linux only). Returns ``None`` -- "unknown", never "local" -- on any
    non-Linux host, a missing/unreadable ``/proc/mounts``, or a path that
    matches no mount entry (should not happen for a resolvable path, but a
    parse miss must not be silently read as "safe")."""
    mounts_path = Path("/proc/mounts")
    if not mounts_path.is_file():
        return None
    try:
        lines = mounts_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    try:
        resolved = path.resolve()
    except OSError:
        return None

    best_mount_len = -1
    best_fstype: str | None = None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        # /proc/mounts columns: <device> <mount_point> <fstype> <options> <dump> <pass>
        mount_point, fstype = parts[1], parts[2]
        mp = Path(mount_point)
        try:
            matches = resolved == mp or resolved.is_relative_to(mp)
        except ValueError:
            matches = False
        if matches and len(str(mp)) > best_mount_len:
            best_mount_len = len(str(mp))
            best_fstype = fstype
    return best_fstype


def _check(
    *,
    check_id: str,
    status: CheckStatus,
    evidence: dict[str, Any],
    remediation: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "evidence": evidence,
    }
    if remediation is not None:
        out["remediation"] = remediation
    if note is not None:
        out["note"] = note
    return out


def filesystem_check(path: Path) -> dict[str, Any]:
    """R7: is ``path`` (normally ``Config.data_dir``) on a filesystem
    where ``fcntl.flock()`` single-writer locking is reliable?

    Network shares remain diagnosable (read-only doctor) but are explicitly
    rejected as unsafe for a writable Magicite writer service.
    """
    fstype = _detect_fstype(path)
    writable_service_supported: bool | None
    if fstype is None:
        network = None
        writable_service_supported = None
        status: CheckStatus = "unknown"
        note = (
            f"could not determine the filesystem type for {path} (non-Linux host, or "
            "/proc/mounts unavailable/unreadable) -- flock-based single-writer locking "
            "(spec §4.2, R7) is UNVERIFIED here. If this turns out to be a network mount "
            "(NFS/CIFS/etc.), lock semantics may silently degrade. The TTL/heartbeat-"
            "guarded writer_lease DB row is the portable fallback regardless of what this "
            "check can see (docs/operations.md §8)."
        )
        remediation = (
            "Confirm the data directory is on a local filesystem before enabling a "
            "writable Magicite writer; keep doctor diagnostics available on any mount."
        )
    elif fstype in _NETWORK_FSTYPES:
        network = True
        writable_service_supported = False
        status = "fail"
        note = (
            f"{path} is on a {fstype!r} network filesystem -- fcntl.flock() is known to be "
            "unreliable or a no-op on NFS/CIFS-class mounts (R7). Read-only diagnosis is "
            "still permitted, but a writable writer service on this mount is rejected. "
            "The TTL/heartbeat-guarded writer_lease DB row is the authoritative fallback "
            "(docs/operations.md §8); a crashed holder's lease is only reclaimable after "
            "its TTL (60s default) expires."
        )
        remediation = (
            "Move Magicite data_dir to a supported local filesystem before running a "
            "writable writer; NFS/CIFS remain valid for read-only doctor diagnostics only."
        )
    elif fstype in _SUPPORTED_LOCAL_FSTYPES:
        network = False
        writable_service_supported = True
        status = "ok"
        note = f"{path} is on {fstype!r}, a local filesystem -- flock-based locking is reliable here."
        remediation = None
    else:
        network = False
        writable_service_supported = None
        status = "unknown"
        note = (
            f"{path} is on {fstype!r}; not classified as a known network share, but also "
            "not in the supported local writable-service allowlist. Treat writable "
            "deployment as unverified."
        )
        remediation = (
            "Prefer a known local filesystem (ext4/xfs/apfs/…) for writable service; "
            "doctor can still diagnose the path."
        )
    return {
        "path": str(path),
        "fstype": fstype,
        "network_filesystem": network,
        "writable_service_supported": writable_service_supported,
        "note": note,
        "status": status,
        "remediation": remediation,
    }


def _file_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _open_db_readonly(db_path: Path) -> tuple[sqlite3.Connection | None, Path | None]:
    """Open an existing DB without mutating the data directory (AC-S12-01).

    Mirrors migration preview: ``mode=ro&immutable=1`` when there is no
    non-empty WAL; otherwise copy db+wal into a *temp* directory (never under
    data_dir) and open that copy read-only. Returns ``(conn, temp_dir)``;
    caller must close ``conn`` and ``rmtree(temp_dir)`` when set.
    """
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None, None

    wal_path = Path(f"{db_path}-wal")
    use_wal_copy = wal_path.is_file() and wal_path.stat().st_size > 0
    if not use_wal_copy:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            db_mod.assert_schema_supported(conn)
        except Exception:
            conn.close()
            raise
        return conn, None

    temp_dir = Path(tempfile.mkdtemp(prefix="magicite-doctor-ro-"))
    dest = temp_dir / db_path.name
    try:
        before_db = _file_identity(db_path)
        before_wal = _file_identity(wal_path)
        if before_db is None:
            raise sqlite3.DatabaseError("db disappeared during readonly open")
        import shutil

        shutil.copy2(db_path, dest)
        if wal_path.is_file() and wal_path.stat().st_size > 0:
            shutil.copy2(wal_path, Path(f"{dest}-wal"))
        after_db = _file_identity(db_path)
        after_wal = _file_identity(wal_path)
        if before_db != after_db or before_wal != after_wal:
            raise sqlite3.DatabaseError("db/wal changed during readonly copy")
        uri = f"file:{dest.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        db_mod.assert_schema_supported(conn)
        return conn, temp_dir
    except Exception:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def registry_check(cfg: Config) -> dict[str, Any]:
    """Registry presence + indexed size. Never writes; opens a plain
    read-only URI only if ``skill-graph.db`` already exists (no
    ``ensure_dirs()`` / migration / WAL creation from ``doctor`` itself)."""
    registry_dir_exists = cfg.registry_dir.is_dir()
    egr_md_count = (
        len(registry_mod._iter_registry_files(cfg.registry_dir, "*.egr.md"))
        if registry_dir_exists
        else 0
    )
    db_exists = cfg.db_path.is_file()

    indexed_registry_size: int | None = None
    db_status: CheckStatus = "ok"
    if db_exists:
        conn: sqlite3.Connection | None = None
        temp_dir: Path | None = None
        try:
            conn, temp_dir = _open_db_readonly(cfg.db_path)
            if conn is None:
                indexed_registry_size = None
                db_status = "unknown"
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()
                indexed_registry_size = int(row["n"])
        except (sqlite3.Error, OSError, ValueError):
            indexed_registry_size = None
            db_status = "fail"
        finally:
            if conn is not None:
                conn.close()
            if temp_dir is not None:
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)

    if not registry_dir_exists:
        note = f"{cfg.registry_dir} does not exist yet -- nothing has been registered."
        status: CheckStatus = "warn"
        remediation = "Create the project and run `magicite sync` / register()."
    elif not db_exists:
        note = (
            f"{egr_md_count} .egr.md file(s) on disk but no skill-graph.db yet -- run "
            "`magicite sync` (or call register()) before route() will see anything."
        )
        status = "warn"
        remediation = "Run `magicite sync` (or call register()) to build the projection DB."
    elif indexed_registry_size is None:
        note = f"{cfg.db_path} exists but could not be read (corrupt or mid-migration?)."
        status = "fail"
        remediation = (
            "Restore from a verified backup (core.backup.restore_snapshot) or repair "
            "the projection DB; doctor did not mutate any bytes."
        )
    else:
        note = None
        status = "ok"
        remediation = None

    return {
        "registry_dir": str(cfg.registry_dir),
        "registry_dir_exists": registry_dir_exists,
        "egr_md_file_count": egr_md_count,
        "db_path": str(cfg.db_path),
        "db_exists": db_exists,
        "indexed_registry_size": indexed_registry_size,
        "note": note,
        "status": status if db_status == "ok" else db_status,
        "remediation": remediation,
    }


def embedding_check(cfg: Config) -> dict[str, Any]:
    """R4: which embedding provider is actually configured, and whether
    ``doctor`` can see anything that would make the first real ``embed()``
    call fail. This never *imports* onnxruntime/torch itself (only checks
    importability via ``importlib.util.find_spec``, which does not execute
    module code) -- ``doctor`` must not be the thing that accidentally
    triggers a network fetch."""
    provider = cfg.embedding_provider
    result: dict[str, Any] = {"provider": provider, "offline": cfg.embedding_offline}

    if provider == "fastembed":
        importable = importlib.util.find_spec("fastembed") is not None
        result["fastembed_importable"] = importable
        if not importable:
            result["status"] = "fail"
            result["note"] = (
                "fastembed is not installed in this environment -- the first embed() call "
                "will fail outright. Install the `fastembed` extra, or set "
                "MAGICITE_EMBEDDING_PROVIDER=hashing for a deterministic, dependency-free "
                "fallback (tests/CI only -- lower routing quality)."
            )
            result["remediation"] = "Install the fastembed extra or switch provider."
        elif cfg.embedding_offline:
            result["status"] = "ok"
            result["note"] = (
                "MAGICITE_EMBEDDING_OFFLINE=1: embed() refuses any network fetch (R4). If "
                "the BAAI/bge-small-en-v1.5 model was not baked at build time or "
                "pre-fetched (`magicite fetch-model`), the first embed() call will raise "
                "FastEmbedModelUnavailableError rather than silently falling back to a "
                "different embedding space."
            )
            result["remediation"] = None
        else:
            result["status"] = "warn"
            result["note"] = (
                "online mode: a cache miss on the first embed() call will attempt a "
                "network fetch (R4) -- run `magicite fetch-model` ahead of time, or set "
                "MAGICITE_EMBEDDING_OFFLINE=1 inside a hardened/offline deployment once the "
                "model is baked/cached."
            )
            result["remediation"] = "Pre-fetch the model or set MAGICITE_EMBEDDING_OFFLINE=1."
    elif provider == "hashing":
        result["status"] = "ok"
        result["note"] = (
            "deterministic, offline, zero-download -- this is the test/CI provider "
            "(CR-6). Routing quality with `hashing` is not representative of production "
            "fastembed/ollama embeddings; do not run a real registry against it long-term."
        )
        result["remediation"] = None
    elif provider == "ollama":
        result["status"] = "unknown"
        result["note"] = (
            f"requires a reachable Ollama host at {cfg.ollama_host!r} serving "
            f"{cfg.ollama_model!r} -- doctor does not probe the network, so this is "
            "unverified; a live route()/embed() call is the only real test."
        )
        result["remediation"] = "Verify the Ollama host separately; doctor stays offline."
    else:
        result["status"] = "fail"
        result["note"] = f"unrecognized embedding_provider {provider!r} -- route()/register() will fail."
        result["remediation"] = "Set MAGICITE_EMBEDDING_PROVIDER to hashing|fastembed|ollama."
    return result


def governance_check(cfg: Config) -> dict[str, Any]:
    return {
        "autonomous": cfg.autonomous,
        "hook_token_configured": cfg.hook_token is not None,
        "status": "ok",
        "remediation": None,
        "note": (
            "MAGICITE_AUTONOMOUS=1: R3 proposals (nucleate/sharpen/promote/archive) that "
            "clear their evidence bar execute immediately, no review step. Only appropriate "
            "for a registry you trust end-to-end (docs/operations.md §1)."
            if cfg.autonomous
            else "review mode (default): every R3 tool creates a proposal, never mutates the engram directly."
        ),
    }


def layout_check(cfg: Config) -> dict[str, Any]:
    """[DATA-DIR-AMENDED 2026-08-15] Report which data directory is in use.

    Magicite owns ``.magicite/``; ``.spectra/`` belongs to ESL/tonberry and was
    the pre-amendment location. Resolution still falls back to the old tree
    when a real registry lives there, so that a legacy project keeps working —
    but a silent fallback is how a project stays on a deprecated layout for a
    year, so it is reported as a warning with the exact move to make.
    """
    note = ""
    status: CheckStatus = "ok"
    remediation: str | None = None
    if cfg.uses_legacy_layout:
        status = "warn"
        note = (
            f"Magicite state is under the deprecated '{cfg.data_dir_name}/', which belongs to "
            f"ESL/tonberry (it owns '{cfg.data_dir_name}/changes/'). Move Magicite's own "
            f"directories to '{config_mod.DEFAULT_DATA_DIR_NAME}/': "
            f"git mv {cfg.data_dir_name}/engrams {config_mod.DEFAULT_DATA_DIR_NAME}/engrams "
            f"(likewise archive/, approvals/, runtime/, magicite.toml, bench/ if present). "
            f"Support for the old location will be removed in a future minor version."
        )
        remediation = (
            f"Migrate from {cfg.data_dir_name}/ to {config_mod.DEFAULT_DATA_DIR_NAME}/ "
            "with the git mv steps named in the note."
        )
    return {
        "data_dir": str(cfg.data_dir),
        "data_dir_name": cfg.data_dir_name,
        "legacy": cfg.uses_legacy_layout,
        "expected": config_mod.DEFAULT_DATA_DIR_NAME,
        "note": note,
        "status": status,
        "remediation": remediation,
    }


def reconciliation_check(cfg: Config) -> dict[str, Any]:
    """C8: report whether restore left the instance in reconciliation_required."""
    try:
        from magicite.core import backup as backup_mod
    except ImportError:
        return {
            "reconciliation_required": False,
            "status": "not_applicable",
            "note": "backup module unavailable",
            "remediation": None,
        }
    required = backup_mod.is_reconciliation_required(cfg)
    detail = backup_mod.reconciliation_status(cfg)
    if required:
        return {
            "reconciliation_required": True,
            "status": "fail",
            "evidence": detail,
            "note": detail.get("reason") or "reconciliation_required",
            "remediation": (
                "Supply a verified RecoveryOverlay/1 and sequence anchor, then run "
                "core.backup.reconcile_and_activate (or restore with overlay)."
            ),
        }
    return {
        "reconciliation_required": False,
        "status": "ok",
        "evidence": detail,
        "note": None,
        "remediation": None,
    }


def run_doctor(cfg: Config) -> dict[str, Any]:
    """The full ``magicite doctor`` report (``doctor/1``).

    Every sub-check is independent and best-effort -- one check failing to
    determine something (e.g. an unreadable ``/proc/mounts``) never raises; it
    reports ``unknown`` / ``not_applicable`` and lets the caller decide.
    """
    registry = registry_check(cfg)
    fs_target = cfg.data_dir if cfg.data_dir.is_dir() else cfg.project_root
    filesystem = filesystem_check(fs_target)
    embedding = embedding_check(cfg)
    registry_size = registry["indexed_registry_size"] or 0
    cold_start = kpi_mod.cold_start_signal(registry_size)
    governance = governance_check(cfg)
    layout = layout_check(cfg)
    reconciliation = reconciliation_check(cfg)

    checks = [
        _check(
            check_id="filesystem.lock_semantics",
            status=filesystem["status"],
            evidence={
                "path": filesystem["path"],
                "fstype": filesystem["fstype"],
                "network_filesystem": filesystem["network_filesystem"],
                "writable_service_supported": filesystem["writable_service_supported"],
            },
            remediation=filesystem.get("remediation"),
            note=filesystem.get("note"),
        ),
        _check(
            check_id="registry.presence",
            status=registry["status"],
            evidence={
                "registry_dir": registry["registry_dir"],
                "registry_dir_exists": registry["registry_dir_exists"],
                "egr_md_file_count": registry["egr_md_file_count"],
                "db_path": registry["db_path"],
                "db_exists": registry["db_exists"],
                "indexed_registry_size": registry["indexed_registry_size"],
            },
            remediation=registry.get("remediation"),
            note=registry.get("note"),
        ),
        _check(
            check_id="embedding.provider",
            status=embedding["status"],
            evidence={
                "provider": embedding["provider"],
                "offline": embedding["offline"],
                "fastembed_importable": embedding.get("fastembed_importable"),
            },
            remediation=embedding.get("remediation"),
            note=embedding.get("note"),
        ),
        _check(
            check_id="cold_start.signal",
            status="warn" if cold_start.get("below_reference_size") else "ok",
            evidence=dict(cold_start),
            remediation=(
                "Grow the registry or treat cold-start routing quality as unevaluated."
                if cold_start.get("below_reference_size")
                else None
            ),
            note=cold_start.get("note"),
        ),
        _check(
            check_id="governance.mode",
            status=governance["status"],
            evidence={
                "autonomous": governance["autonomous"],
                "hook_token_configured": governance["hook_token_configured"],
            },
            remediation=governance.get("remediation"),
            note=governance.get("note"),
        ),
        _check(
            check_id="layout.data_dir",
            status=layout["status"],
            evidence={
                "data_dir": layout["data_dir"],
                "data_dir_name": layout["data_dir_name"],
                "legacy": layout["legacy"],
                "expected": layout["expected"],
            },
            remediation=layout.get("remediation"),
            note=layout.get("note") or None,
        ),
        _check(
            check_id="recovery.reconciliation",
            status=reconciliation["status"],
            evidence={
                "reconciliation_required": reconciliation["reconciliation_required"],
                **(reconciliation.get("evidence") or {}),
            },
            remediation=reconciliation.get("remediation"),
            note=reconciliation.get("note"),
        ),
    ]

    warnings: list[str] = []
    if filesystem["network_filesystem"] is True:
        warnings.append(f"[R7 lock semantics] {filesystem['note']}")
    elif filesystem["network_filesystem"] is None:
        warnings.append(f"[R7 lock semantics, unverified] {filesystem['note']}")
    if cold_start["below_reference_size"]:
        warnings.append(f"[R9 cold start] {cold_start['note']}")
    if registry["note"]:
        warnings.append(f"[registry] {registry['note']}")
    if cfg.embedding_provider == "fastembed" and not embedding.get("fastembed_importable", True):
        warnings.append(f"[embedding] {embedding['note']}")
    if layout["legacy"]:
        warnings.append(f"[data layout, deprecated] {layout['note']}")
    if reconciliation["reconciliation_required"]:
        warnings.append(f"[reconciliation_required] {reconciliation.get('note')}")

    return {
        "kind": DOCTOR_KIND,
        "project_root": str(cfg.project_root),
        "layout": layout,
        "registry": registry,
        "filesystem": filesystem,
        "embedding": embedding,
        "cold_start": cold_start,
        "governance": governance,
        "reconciliation_required": bool(reconciliation["reconciliation_required"]),
        "checks": checks,
        "warnings": warnings,
        "healthy": len(warnings) == 0,
    }
