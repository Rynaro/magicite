"""register()/sync() orchestration (spec §2.6, §5.3).

Ties together ``engram`` (parse/lint/ids), ``storage`` (the durable
mirror) and ``embeddings`` (Tier-C vectors) for the two ingestion tools.
Framework-free (INV-1): no MCP import here; ``magicite.mcp.bind_registry``
adapts this module's plain dataclasses onto the pydantic tool schemas.

M0 scope note: this is the "reduced" ingestion path the M0 story
describes. It implements §2.6 steps 1-7 (scan/parse/validate/lint, upsert
engram + declared edges, resolve dangling, embed) against the
``strict`` lint profile only. Steps 8-9 (derived ``similar_to`` kNN edges,
community detection) land in M2 alongside ``core/activation.py`` and
``core/communities.py`` -- this module reports ``detector="none"``
honestly rather than faking a detector that does not exist yet. SKILL.md
import (``format="skill"``) is M1's ``engram/skillmd.py``; until then
``register()`` raises a typed ``not_implemented`` for that path rather
than silently no-oping.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from magicite.config import Config
from magicite.embeddings.base import Embedder
from magicite.engram import ids as ids_mod
from magicite.engram import lint as lint_mod
from magicite.engram import parser as parser_mod
from magicite.engram.model import Engram
from magicite.errors import (
    InvalidInputError,
    NotImplementedToolError,
    PathOutsideProjectError,
)

_EDGE_TYPE_FOR_FIELD = {
    "needs": "depends_on",
    "composes": "composes",
    "inhibits": "inhibits",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def embeddable_text(engram: Engram) -> str:
    """The text register()/sync() embed (docs/04: "embed triggers + procedure").

    Extended with the routing intent fields, since those are the primary
    routing signal per docs/04's Routing Block and their absence would
    make the toy registry's lexical overlap far weaker than intended.
    """
    fm = engram.frontmatter
    parts = [fm.intent.does, fm.intent.use_when]
    parts.extend(fm.triggers.positive)
    parts.extend(step.text for step in engram.body.procedure)
    return " ".join(p for p in parts if p)


@dataclass
class RegisteredEntry:
    id: str
    name: str
    origin: str
    status: str
    verification_status: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationError:
    path: str
    message: str


@dataclass
class IngestOutcome:
    registered: list[RegisteredEntry] = field(default_factory=list)
    validation_errors: list[ValidationError] = field(default_factory=list)
    skipped_unchanged: int = 0
    dangling: list[str] = field(default_factory=list)


@dataclass
class RegisterOutcome:
    ingested: int
    registered: list[RegisteredEntry]
    validation_errors: list[ValidationError]
    skipped_unchanged: int
    consolidation_scheduled: bool = False


@dataclass
class SyncOutcome:
    synced: int
    removed: list[str]
    validation_errors: list[ValidationError]
    dangling: list[str]
    detector: str
    consolidation_scheduled: bool = False


def _resolve_scan_root(project_root: Path, path: str) -> Path:
    candidate = (project_root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError as exc:
        raise PathOutsideProjectError(
            f"path {path!r} resolves outside the project root", hint="pass a path inside project_root"
        ) from exc
    return candidate


def _discover_files(scan_root: Path, fmt: str) -> tuple[list[Path], list[Path]]:
    """Returns (egr_files, skill_files) under ``scan_root``."""
    if scan_root.is_file():
        if scan_root.suffix == ".md" and scan_root.name.endswith(".egr.md"):
            return [scan_root], []
        if scan_root.name == "SKILL.md":
            return [], [scan_root]
        raise InvalidInputError(f"{scan_root} is neither a .egr.md nor a SKILL.md file")

    egr_files = sorted(scan_root.rglob("*.egr.md")) if fmt in ("auto", "egr") else []
    skill_files = sorted(scan_root.rglob("SKILL.md")) if fmt in ("auto", "skill") else []
    return egr_files, skill_files


def _upsert_engram_row(conn: sqlite3.Connection, engram: Engram, *, registry_dir: Path) -> str:
    fm = engram.frontmatter
    now = _now()
    plasticity = fm.plasticity
    trust = fm.trust

    identity_hash = ids_mod.identity_sha256(
        ids_mod.identity_routing_payload(
            fm.name,
            fm.intent.does,
            fm.intent.use_when,
            fm.intent.not_when,
            fm.triggers.positive,
            fm.triggers.negative,
        )
    )

    existing = conn.execute("SELECT created_at FROM engram WHERE id = ?", (fm.id,)).fetchone()
    created_at = existing["created_at"] if existing else now

    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, intent_not_when,
          storage_strength, s_decayed_at, exposure_count, success_count, failure_count,
          excitability, last_applied, last_checkpoint,
          embedding_model, embedding_ref, embedding_refreshed_at,
          has_exec_blocks, identity_sha256, content_sha256, body_sha256, file_mtime_ns,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?, ?,?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, path=excluded.path, spec_version=excluded.spec_version,
          version=excluded.version, origin=excluded.origin,
          verification_status=excluded.verification_status, status=excluded.status,
          intent_does=excluded.intent_does, intent_use_when=excluded.intent_use_when,
          intent_not_when=excluded.intent_not_when,
          storage_strength=excluded.storage_strength, exposure_count=excluded.exposure_count,
          success_count=excluded.success_count, failure_count=excluded.failure_count,
          excitability=excluded.excitability, last_applied=excluded.last_applied,
          last_checkpoint=excluded.last_checkpoint,
          has_exec_blocks=excluded.has_exec_blocks, identity_sha256=excluded.identity_sha256,
          content_sha256=excluded.content_sha256, body_sha256=excluded.body_sha256,
          file_mtime_ns=excluded.file_mtime_ns, updated_at=excluded.updated_at
        """,
        (
            fm.id,
            fm.name,
            engram.path,
            fm.spec,
            fm.version,
            fm.provenance,
            trust.verification_status if trust else "pending",
            plasticity.status if plasticity else "nascent",
            fm.intent.does,
            fm.intent.use_when,
            fm.intent.not_when,
            plasticity.storage_strength if plasticity else 0.0,
            now,
            plasticity.exposure_count if plasticity else 0,
            plasticity.outcome.success if plasticity else 0,
            plasticity.outcome.failure if plasticity else 0,
            plasticity.excitability if plasticity else 0.05,
            plasticity.last_applied if plasticity else None,
            plasticity.last_checkpoint if plasticity else None,
            None,
            None,
            None,
            int(bool(engram.body.exec_blocks)),
            identity_hash,
            engram.content_sha256,
            engram.body_sha256,
            engram.file_mtime_ns,
            created_at,
            now,
        ),
    )

    conn.execute("DELETE FROM engram_step WHERE engram_id = ?", (fm.id,))
    for step in engram.body.procedure:
        conn.execute(
            "INSERT INTO engram_step (engram_id, step_no, text, ok_count, total_count) "
            "VALUES (?,?,?,?,?)",
            (fm.id, step.step_no, step.text, step.ok_count, step.total_count),
        )

    conn.execute("DELETE FROM engram_trigger WHERE engram_id = ?", (fm.id,))
    for ordinal, text in enumerate(fm.triggers.positive):
        conn.execute(
            "INSERT INTO engram_trigger (engram_id, polarity, ord, text) VALUES (?,?,?,?)",
            (fm.id, "positive", ordinal, text),
        )
    for ordinal, text in enumerate(fm.triggers.negative):
        conn.execute(
            "INSERT INTO engram_trigger (engram_id, polarity, ord, text) VALUES (?,?,?,?)",
            (fm.id, "negative", ordinal, text),
        )

    return fm.id


def _wire_context_affinity(conn: sqlite3.Connection, engram: Engram) -> None:
    fm = engram.frontmatter
    names = sorted(set(fm.context_affinity) | set(fm.affinity))
    conn.execute("DELETE FROM engram_context WHERE engram_id = ?", (fm.id,))
    for name in names:
        row = conn.execute("SELECT id FROM context_node WHERE name = ?", (name,)).fetchone()
        if row is None:
            context_id = f"ctx_{name}"
            conn.execute(
                "INSERT OR IGNORE INTO context_node (id, name, kind) VALUES (?,?,?)",
                (context_id, name, "project"),
            )
        else:
            context_id = row["id"]
        conn.execute(
            "INSERT OR REPLACE INTO engram_context (engram_id, context_id, weight) VALUES (?,?,1.0)",
            (fm.id, context_id),
        )


def _wire_declared_edges(conn: sqlite3.Connection, engram: Engram) -> list[str]:
    fm = engram.frontmatter
    dangling: list[str] = []
    now = _now()
    for field_name, edge_type in _EDGE_TYPE_FOR_FIELD.items():
        for target_name in getattr(fm, field_name):
            target = conn.execute("SELECT id FROM engram WHERE name = ?", (target_name,)).fetchone()
            dst_id = target["id"] if target else None
            is_dangling = dst_id is None
            if is_dangling:
                dangling.append(target_name)
            conn.execute(
                """
                INSERT INTO edge (
                  src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                  evidence_count, provenance, first_observed, dangling
                ) VALUES (?,?,?,?, 0.0, ?, 0, 'declared', ?, ?)
                ON CONFLICT(src_id, dst_name, type) DO UPDATE SET
                  dst_id=excluded.dst_id, dangling=excluded.dangling
                """,
                (fm.id, target_name, dst_id, edge_type, now, now, int(is_dangling)),
            )
    # Resolve any pre-existing dangling edges that now point at this newly-registered name.
    conn.execute(
        "UPDATE edge SET dst_id = ?, dangling = 0 WHERE dst_name = ? AND dangling = 1",
        (fm.id, fm.name),
    )
    return dangling


def _embed_and_store(conn: sqlite3.Connection, embedder: Embedder, engram: Engram) -> None:
    text = embeddable_text(engram)
    vec = embedder.embed(text)
    now = _now()
    conn.execute(
        """
        INSERT INTO eph_embedding (engram_id, model, dim, vec, source_sha256, created_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(engram_id, model) DO UPDATE SET
          dim=excluded.dim, vec=excluded.vec, source_sha256=excluded.source_sha256,
          created_at=excluded.created_at
        """,
        (engram.id, embedder.model_name, embedder.dim, vec.tobytes(), engram.body_sha256, now),
    )
    conn.execute(
        "UPDATE engram SET embedding_model=?, embedding_ref=?, embedding_refreshed_at=? WHERE id=?",
        (embedder.model_name, engram.body_sha256, now, engram.id),
    )


def _ingest_one(
    conn: sqlite3.Connection,
    embedder: Embedder,
    engram: Engram,
    *,
    profile: str,
    registry_dir: Path,
) -> tuple[RegisteredEntry | None, ValidationError | None, bool, list[str]]:
    """Returns (registered_entry, validation_error, skipped_unchanged, dangling)."""
    result = lint_mod.lint(engram, profile=profile)  # type: ignore[arg-type]
    if profile == "strict" and not result.ok:
        msg = "; ".join(f"{i.rule}: {i.message}" for i in result.errors)
        return None, ValidationError(path=engram.path, message=msg), False, []

    existing = conn.execute(
        "SELECT content_sha256 FROM engram WHERE id = ?", (engram.id,)
    ).fetchone()
    if existing is not None and existing["content_sha256"] == engram.content_sha256:
        return None, None, True, []

    _upsert_engram_row(conn, engram, registry_dir=registry_dir)
    _wire_context_affinity(conn, engram)
    dangling = _wire_declared_edges(conn, engram)
    _embed_and_store(conn, embedder, engram)

    fm = engram.frontmatter
    warnings = [w.message for w in result.warnings]
    entry = RegisteredEntry(
        id=fm.id,
        name=fm.name,
        origin=fm.provenance,
        status=fm.plasticity.status if fm.plasticity else "nascent",
        verification_status=fm.trust.verification_status if fm.trust else "pending",
        warnings=warnings,
    )
    return entry, None, False, dangling


def _ensure_registry_gitignore(cfg: Config) -> None:
    """spec §1: register() writes a .gitignore into .spectra/engrams/ on first
    run excluding the rebuildable DB (CR-2); MAGICITE_COMMIT_DB=1 opts out."""
    if cfg.commit_db:
        return
    gitignore_path = cfg.registry_dir / ".gitignore"
    if gitignore_path.exists():
        return
    cfg.registry_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path.write_text("skill-graph.db*\n", encoding="utf-8")


def register(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    path: str,
    fmt: str = "auto",
) -> RegisterOutcome:
    project_root = cfg.project_root.resolve()
    _ensure_registry_gitignore(cfg)
    scan_root = _resolve_scan_root(project_root, path)
    egr_files, skill_files = _discover_files(scan_root, fmt)

    if skill_files:
        raise NotImplementedToolError(
            "SKILL.md import lands in M1 (engram/skillmd.py); register() only ingests "
            "native .egr.md files in M0",
            hint="pass format='egr' or point at a directory containing only .egr.md files",
        )

    outcome = IngestOutcome()
    for file_path in egr_files:
        try:
            parsed = parser_mod.parse_file(file_path, registry_root=project_root)
        except parser_mod.EngramParseError as exc:
            outcome.validation_errors.append(ValidationError(path=str(file_path), message=str(exc)))
            continue

        entry, verr, skipped, dangling = _ingest_one(
            conn, embedder, parsed.engram, profile="strict", registry_dir=cfg.registry_dir
        )
        if verr:
            outcome.validation_errors.append(verr)
        if entry:
            outcome.registered.append(entry)
        if skipped:
            outcome.skipped_unchanged += 1
        outcome.dangling.extend(dangling)

    return RegisterOutcome(
        ingested=len(outcome.registered),
        registered=outcome.registered,
        validation_errors=outcome.validation_errors,
        skipped_unchanged=outcome.skipped_unchanged,
        consolidation_scheduled=False,
    )


def sync(cfg: Config, conn: sqlite3.Connection, embedder: Embedder) -> SyncOutcome:
    registry_dir = cfg.registry_dir
    registry_dir.mkdir(parents=True, exist_ok=True)
    project_root = cfg.project_root.resolve()

    on_disk_paths: set[str] = set()
    outcome = IngestOutcome()

    for file_path in sorted(registry_dir.rglob("*.egr.md")):
        relpath = str(file_path.resolve().relative_to(project_root))
        on_disk_paths.add(relpath)
        try:
            parsed = parser_mod.parse_file(file_path, registry_root=project_root)
        except parser_mod.EngramParseError as exc:
            outcome.validation_errors.append(ValidationError(path=relpath, message=str(exc)))
            continue

        _entry, verr, _skipped, dangling = _ingest_one(
            conn, embedder, parsed.engram, profile="strict", registry_dir=registry_dir
        )
        if verr:
            outcome.validation_errors.append(verr)
        outcome.dangling.extend(dangling)

    removed: list[str] = []
    for row in conn.execute("SELECT id, name, path FROM engram").fetchall():
        if row["path"] not in on_disk_paths:
            conn.execute("DELETE FROM engram WHERE id = ?", (row["id"],))
            removed.append(row["name"])

    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('last_sync', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_now(),),
    )

    synced = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    return SyncOutcome(
        synced=synced,
        removed=removed,
        validation_errors=outcome.validation_errors,
        dangling=sorted(set(outcome.dangling)),
        detector="none",  # core/communities.py (leiden | label_propagation) lands in M2
        consolidation_scheduled=False,
    )
