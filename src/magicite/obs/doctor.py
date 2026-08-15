"""``magicite doctor`` -- the honest environment check (spec M7 story,
Risks R7/R9).

This module is deliberately **not reassuring**. Per the M7 mission
directive it must:

- warn when lock semantics degrade on a non-local filesystem (R7:
  ``fcntl.flock()`` is unreliable/a no-op on NFS/CIFS -- the two-layer
  ``storage/lease.py::CrossProcessLease`` design exists precisely because
  of this, docs/operations.md §8), and
- state the cold-start break-even honestly (R9: under ~50 skills native
  SKILL.md matching is competitive and Magicite is overhead) rather than
  overselling.

Framework-free (INV-1: no MCP import here) and read-only: every check here
either reads the filesystem/environment or opens a plain, non-authorizer
connection to inspect (never mutate) the registry DB. ``magicite doctor``
is a CLI diagnostic, not one of the 16 MCP tools, and is never called from
inside a live ``serve`` process.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

from magicite.config import Config
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


def filesystem_check(path: Path) -> dict[str, Any]:
    """R7: is ``path`` (normally ``Config.spectra_dir``) on a filesystem
    where ``fcntl.flock()`` single-writer locking is reliable?"""
    fstype = _detect_fstype(path)
    if fstype is None:
        network = None
        note = (
            f"could not determine the filesystem type for {path} (non-Linux host, or "
            "/proc/mounts unavailable/unreadable) -- flock-based single-writer locking "
            "(spec §4.2, R7) is UNVERIFIED here. If this turns out to be a network mount "
            "(NFS/CIFS/etc.), lock semantics may silently degrade. The TTL/heartbeat-"
            "guarded writer_lease DB row is the portable fallback regardless of what this "
            "check can see (docs/operations.md §8)."
        )
    elif fstype in _NETWORK_FSTYPES:
        network = True
        note = (
            f"{path} is on a {fstype!r} network filesystem -- fcntl.flock() is known to be "
            "unreliable or a no-op on NFS/CIFS-class mounts (R7). The TTL/heartbeat-guarded "
            "writer_lease DB row is the authoritative fallback (docs/operations.md §8), but "
            "throughput under contention will be worse than local disk, and a crashed "
            "holder's lease is only reclaimable after its TTL (60s default) expires."
        )
    else:
        network = False
        note = f"{path} is on {fstype!r}, a local filesystem -- flock-based locking is reliable here."
    return {"path": str(path), "fstype": fstype, "network_filesystem": network, "note": note}


def registry_check(cfg: Config) -> dict[str, Any]:
    """Registry presence + indexed size. Never writes; opens a plain
    read-only-by-convention connection only if ``skill-graph.db`` already
    exists (no ``ensure_dirs()``/migration-run side effect from ``doctor``
    itself)."""
    registry_dir_exists = cfg.registry_dir.is_dir()
    egr_md_count = len(list(cfg.registry_dir.glob("*.egr.md"))) if registry_dir_exists else 0
    db_exists = cfg.db_path.is_file()

    indexed_registry_size: int | None = None
    if db_exists:
        conn: sqlite3.Connection | None = None
        try:
            conn = db_mod.connect(cfg.db_path)
            row = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()
            indexed_registry_size = int(row["n"])
        except sqlite3.Error:
            indexed_registry_size = None
        finally:
            if conn is not None:
                conn.close()

    if not registry_dir_exists:
        note = f"{cfg.registry_dir} does not exist yet -- nothing has been registered."
    elif not db_exists:
        note = (
            f"{egr_md_count} .egr.md file(s) on disk but no skill-graph.db yet -- run "
            "`magicite sync` (or call register()) before route() will see anything."
        )
    elif indexed_registry_size is None:
        note = f"{cfg.db_path} exists but could not be read (corrupt or mid-migration?)."
    else:
        note = None

    return {
        "registry_dir": str(cfg.registry_dir),
        "registry_dir_exists": registry_dir_exists,
        "egr_md_file_count": egr_md_count,
        "db_path": str(cfg.db_path),
        "db_exists": db_exists,
        "indexed_registry_size": indexed_registry_size,
        "note": note,
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
            result["note"] = (
                "fastembed is not installed in this environment -- the first embed() call "
                "will fail outright. Install the `fastembed` extra, or set "
                "MAGICITE_EMBEDDING_PROVIDER=hashing for a deterministic, dependency-free "
                "fallback (tests/CI only -- lower routing quality)."
            )
        elif cfg.embedding_offline:
            result["note"] = (
                "MAGICITE_EMBEDDING_OFFLINE=1: embed() refuses any network fetch (R4). If "
                "the BAAI/bge-small-en-v1.5 model was not baked at build time or "
                "pre-fetched (`magicite fetch-model`), the first embed() call will raise "
                "FastEmbedModelUnavailableError rather than silently falling back to a "
                "different embedding space."
            )
        else:
            result["note"] = (
                "online mode: a cache miss on the first embed() call will attempt a "
                "network fetch (R4) -- run `magicite fetch-model` ahead of time, or set "
                "MAGICITE_EMBEDDING_OFFLINE=1 inside a hardened/offline deployment once the "
                "model is baked/cached."
            )
    elif provider == "hashing":
        result["note"] = (
            "deterministic, offline, zero-download -- this is the test/CI provider "
            "(CR-6). Routing quality with `hashing` is not representative of production "
            "fastembed/ollama embeddings; do not run a real registry against it long-term."
        )
    elif provider == "ollama":
        result["note"] = (
            f"requires a reachable Ollama host at {cfg.ollama_host!r} serving "
            f"{cfg.ollama_model!r} -- doctor does not probe the network, so this is "
            "unverified; a live route()/embed() call is the only real test."
        )
    else:
        result["note"] = f"unrecognized embedding_provider {provider!r} -- route()/register() will fail."
    return result


def governance_check(cfg: Config) -> dict[str, Any]:
    return {
        "autonomous": cfg.autonomous,
        "hook_token_configured": cfg.hook_token is not None,
        "note": (
            "MAGICITE_AUTONOMOUS=1: R3 proposals (nucleate/sharpen/promote/archive) that "
            "clear their evidence bar execute immediately, no review step. Only appropriate "
            "for a registry you trust end-to-end (docs/operations.md §1)."
            if cfg.autonomous
            else "review mode (default): every R3 tool creates a proposal, never mutates the engram directly."
        ),
    }


def run_doctor(cfg: Config) -> dict[str, Any]:
    """The full ``magicite doctor`` report. Every sub-check is independent
    and best-effort -- one check failing to determine something (e.g. an
    unreadable ``/proc/mounts``) never raises; it reports ``None``/unknown
    and lets the caller decide what to do with that."""
    registry = registry_check(cfg)
    fs_target = cfg.spectra_dir if cfg.spectra_dir.is_dir() else cfg.project_root
    filesystem = filesystem_check(fs_target)
    embedding = embedding_check(cfg)
    registry_size = registry["indexed_registry_size"] or 0
    cold_start = kpi_mod.cold_start_signal(registry_size)
    governance = governance_check(cfg)

    warnings: list[str] = []
    if filesystem["network_filesystem"] is True:
        warnings.append(f"[R7 lock semantics] {filesystem['note']}")
    elif filesystem["network_filesystem"] is None:
        warnings.append(f"[R7 lock semantics, unverified] {filesystem['note']}")
    if cold_start["below_break_even"]:
        warnings.append(f"[R9 cold start] {cold_start['note']}")
    if registry["note"]:
        warnings.append(f"[registry] {registry['note']}")
    if cfg.embedding_provider == "fastembed" and not embedding.get("fastembed_importable", True):
        warnings.append(f"[embedding] {embedding['note']}")

    return {
        "project_root": str(cfg.project_root),
        "registry": registry,
        "filesystem": filesystem,
        "embedding": embedding,
        "cold_start": cold_start,
        "governance": governance,
        "warnings": warnings,
        "healthy": len(warnings) == 0,
    }
