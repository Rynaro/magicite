"""register()/sync()/export() orchestration (spec §2.6, §5.3, §5.4).

Ties together ``engram`` (parse/lint/ids/skillmd/writer), ``storage``
(the durable mirror + Tier-C cache) and ``embeddings`` (Tier-C vectors)
for the ingestion + compile-target tools. Framework-free (INV-1): no MCP
import here; ``magicite.mcp.bind_registry`` adapts this module's plain
dataclasses onto the pydantic tool schemas.

This module is the *orchestrator*: every durable write goes through
``storage.durable`` (which asserts G2, spec §6.2) inside one
``storage.lease.writer_lease()`` acquisition per call (spec §2.6 step 1),
and every Tier-C write goes through ``storage.ephemeral``. Nothing here
issues raw ``INSERT``/``UPDATE``/``DELETE`` SQL against a non-``eph_``
table directly.

M1 scope: §2.6 steps 1-7 (scan/parse/validate/lint, upsert engram +
declared edges + journal, resolve dangling, embed) plus the ``import``
lint profile SKILL.md path (§5.3) and the ``export()`` compile-target
render (§5.4). Steps 8-9 of ``sync()`` (derived ``similar_to`` kNN edges,
community detection) land in M2 alongside ``core/activation.py``/
``core/communities.py`` -- this module honestly reports
``detector="none"`` rather than faking a detector that does not exist yet.
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
from magicite.engram import skillmd as skillmd_mod
from magicite.engram import writer as writer_mod
from magicite.engram.model import Engram
from magicite.errors import InvalidInputError, PathOutsideProjectError
from magicite.storage import durable as durable_mod
from magicite.storage import ephemeral as ephemeral_mod
from magicite.storage import lease as lease_mod

_EXPORT_STATUS_RANK: dict[str, int] = {"consolidated": 0, "promoted": 1}


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


@dataclass
class ExportOutcome:
    exported: int
    target_dir: str
    format: str
    note: str


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


def _identity_hash(engram: Engram) -> str:
    fm = engram.frontmatter
    return ids_mod.identity_sha256(
        ids_mod.identity_routing_payload(
            fm.name,
            fm.intent.does,
            fm.intent.use_when,
            fm.intent.not_when,
            fm.triggers.positive,
            fm.triggers.negative,
        )
    )


def _embed_and_store(conn: sqlite3.Connection, embedder: Embedder, engram: Engram) -> None:
    text = embeddable_text(engram)
    vec = embedder.embed(text)
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=engram.id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=vec,
        source_sha256=engram.body_sha256,
    )
    durable_mod.set_embedding_ref(
        conn, engram_id=engram.id, model_name=embedder.model_name, source_sha256=engram.body_sha256
    )


def _lint_profile_for(engram: Engram) -> str:
    """CR-4: an imported engram's *file* keeps the lenient ``import`` lint
    profile on every subsequent parse -- register()'s native-``.egr.md``
    path re-scanning it, and sync()'s full-registry rebuild scan -- not
    just at the moment of conversion.

    Without this, a freshly-imported draft's own file would hard-fail
    ``strict`` re-lint (e.g. ``negative_triggers`` is always unmet, by
    design, for an import) on the very next ``sync()``, and since a
    ``strict``-failed ``_ingest_one`` never upserts, the engram would
    simply never re-appear after a DB rebuild -- silently contradicting
    both CR-4 ("nothing is silently accepted... or rejected") and the
    rebuild invariant (AC-009) for every imported engram. ``provenance``
    is itself durable, file-level, spec-defined content (§2.2's
    ``origin`` column), so keying the profile off it is a read of the
    file, not an invented side channel.
    """
    return "import" if engram.frontmatter.provenance == "imported" else "strict"


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

    durable_mod.upsert_engram(conn, engram, identity_sha256=_identity_hash(engram))
    durable_mod.wire_context_affinity(conn, engram)
    dangling = durable_mod.wire_declared_edges(conn, engram)
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


def _ingest_skillmd_one(
    conn: sqlite3.Connection,
    embedder: Embedder,
    path: Path,
    *,
    project_root: Path,
    registry_dir: Path,
    actor: str = "register",
) -> tuple[RegisteredEntry | None, ValidationError | None, bool, list[str]]:
    """SKILL.md ingestion (spec §5.3 steps 3-9): convert -> lint(import) ->
    write -> index. Returns the same shape as :func:`_ingest_one`."""
    raw_text = path.read_text(encoding="utf-8")
    try:
        source = skillmd_mod.parse_source(raw_text)
    except skillmd_mod.SkillMdParseError as exc:
        return None, ValidationError(path=str(path), message=str(exc)), False, []

    target_path = registry_dir / f"{source.name}.egr.md"
    target_relpath = str(target_path.relative_to(project_root))
    engram = skillmd_mod.to_engram(source, target_relpath=target_relpath, actor=actor)

    existing_by_id = conn.execute("SELECT id FROM engram WHERE id = ?", (engram.id,)).fetchone()
    if existing_by_id is not None:
        # CR-8 duplicate-import detection (AC-018): identical identity+routing
        # content (name/intent/triggers) is already registered under this id.
        # A freshly re-derived provenance_journal timestamp is not "changed
        # content" for this purpose -- content_sha256 would spuriously differ
        # on every re-import, so the id (a content hash of identity+routing
        # only) is the correct dedup key here, not the whole-file digest.
        return None, None, True, []

    existing_by_name = conn.execute(
        "SELECT id FROM engram WHERE name = ?", (engram.name,)
    ).fetchone()
    if existing_by_name is not None:
        return (
            None,
            ValidationError(
                path=str(path),
                message=(
                    f"{engram.name!r} is already registered under a different identity "
                    f"({existing_by_name['id']} != {engram.id}); re-importing changed "
                    "SKILL.md content under an existing name is not supported in v1 -- "
                    "use sharpen() instead"
                ),
            ),
            False,
            [],
        )

    # spec §5.3 step 6: write before step 7 (index).
    writer_mod.write_engram(target_path, engram)

    entry, verr, _skipped, dangling = _ingest_one(
        conn, embedder, engram, profile="import", registry_dir=registry_dir
    )
    return entry, verr, False, dangling


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

    outcome = IngestOutcome()
    with lease_mod.writer_lease():
        for file_path in egr_files:
            try:
                parsed = parser_mod.parse_file(file_path, registry_root=project_root)
            except parser_mod.EngramParseError as exc:
                outcome.validation_errors.append(ValidationError(path=str(file_path), message=str(exc)))
                continue

            entry, verr, skipped, dangling = _ingest_one(
                conn,
                embedder,
                parsed.engram,
                profile=_lint_profile_for(parsed.engram),
                registry_dir=cfg.registry_dir,
            )
            if verr:
                outcome.validation_errors.append(verr)
            if entry:
                outcome.registered.append(entry)
            if skipped:
                outcome.skipped_unchanged += 1
            outcome.dangling.extend(dangling)

        for file_path in skill_files:
            entry, verr, skipped, dangling = _ingest_skillmd_one(
                conn,
                embedder,
                file_path,
                project_root=project_root,
                registry_dir=cfg.registry_dir,
                actor="register",
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

    with lease_mod.writer_lease():
        for file_path in sorted(registry_dir.rglob("*.egr.md")):
            relpath = str(file_path.resolve().relative_to(project_root))
            on_disk_paths.add(relpath)
            try:
                parsed = parser_mod.parse_file(file_path, registry_root=project_root)
            except parser_mod.EngramParseError as exc:
                outcome.validation_errors.append(ValidationError(path=relpath, message=str(exc)))
                continue

            _entry, verr, _skipped, _dangling = _ingest_one(
                conn,
                embedder,
                parsed.engram,
                profile=_lint_profile_for(parsed.engram),
                registry_dir=registry_dir,
            )
            if verr:
                outcome.validation_errors.append(verr)

        removed: list[str] = []
        for row in conn.execute("SELECT id, name, path FROM engram").fetchall():
            if row["path"] not in on_disk_paths:
                durable_mod.delete_engram(conn, row["id"])
                removed.append(row["name"])

        # spec §2.6 step 6: one comprehensive dangling-resolution pass,
        # after deletions, independent of file processing order (see
        # storage.durable.recompute_dangling's docstring).
        dangling = durable_mod.recompute_dangling(conn)

        durable_mod.write_schema_meta(conn, "last_sync", _now())

    synced = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    return SyncOutcome(
        synced=synced,
        removed=removed,
        validation_errors=outcome.validation_errors,
        dangling=dangling,
        detector="none",  # core/communities.py (leiden | label_propagation) lands in M2
        consolidation_scheduled=False,
    )


def export(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    out_dir: str,
    min_status: str = "consolidated",
) -> ExportOutcome:
    """spec §5.4: render ``out_dir/<name>/SKILL.md`` shims for every engram
    at ``min_status`` or above -- the inverse of SKILL.md import."""
    project_root = cfg.project_root.resolve()
    target_root = _resolve_scan_root(project_root, out_dir)
    min_rank = _EXPORT_STATUS_RANK[min_status]

    rows = conn.execute(
        "SELECT name, path, status FROM engram ORDER BY name"
    ).fetchall()
    eligible = [r for r in rows if _EXPORT_STATUS_RANK.get(r["status"], -1) >= min_rank]

    exported = 0
    with lease_mod.writer_lease():
        for row in eligible:
            file_path = project_root / row["path"]
            parsed = parser_mod.parse_file(file_path, registry_root=project_root)
            skillmd_text = skillmd_mod.render_skillmd(parsed.engram)
            target = target_root / row["name"] / "SKILL.md"
            writer_mod.atomic_write(target, skillmd_text)
            exported += 1

    return ExportOutcome(
        exported=exported,
        target_dir=str(target_root),
        format="skill",
        note=f"rendered {exported} SKILL.md shim(s) at status >= {min_status!r}",
    )
