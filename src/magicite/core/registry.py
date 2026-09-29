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

M1 landed §2.6 steps 1-7 (scan/parse/validate/lint, upsert engram +
declared edges + journal, resolve dangling, embed) plus the ``import``
lint profile SKILL.md path (§5.3) and the ``export()`` compile-target
render (§5.4). M2 closes the carry-forward: steps 8-9 (derived
``similar_to`` kNN edges, community detection via
``core/communities.py``) -- ``detector`` in :class:`SyncOutcome` now
honestly reports whichever :class:`~magicite.core.communities
.CommunityDetector` actually ran.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from magicite.config import Config
from magicite.core import approvals as approvals_mod
from magicite.core import communities as communities_mod
from magicite.core import edge_weight as edge_weight_mod
from magicite.core import lifecycle as lifecycle_mod
from magicite.core import trust as trust_mod
from magicite.embeddings.base import Embedder, contraindication_model_name
from magicite.engram import ids as ids_mod
from magicite.engram import lint as lint_mod
from magicite.engram import parser as parser_mod
from magicite.engram import skillmd as skillmd_mod
from magicite.engram import writer as writer_mod
from magicite.engram.model import (
    Engram,
    EngramFrontmatter,
    Intent,
    Plasticity,
    ProvenanceJournalEntry,
    Triggers,
    Trust,
)
from magicite.engram.model_v1 import EngramV1
from magicite.errors import InvalidInputError, PathOutsideProjectError
from magicite.storage import durable as durable_mod
from magicite.storage import ephemeral as ephemeral_mod
from magicite.storage import lease as lease_mod

_EXPORT_STATUS_RANK: dict[str, int] = {"consolidated": 0, "promoted": 1}

#: spec §2.6 step 9: the edge types that participate in community
#: structure. ``inhibits`` is excluded -- it is an anti-affinity signal
#: (spec §3.3 step 5), never a co-membership one.
_COMMUNITY_EDGE_TYPES: tuple[str, ...] = ("co_activation", "composes", "depends_on", "similar_to")

ROUTING_VIEW_SCHEMA = "magicite-routing-view/1"
ROUTING_VIEW_FIELDS: tuple[str, ...] = (
    "intent.does",
    "intent.use_when",
    "triggers.positive",
    "procedure.text",
)
CONTRAINDICATION_VIEW_SCHEMA = "magicite-contraindication-view/1"

#: [DECLARED-EDGES-AMENDED 2026-08-15] ``_COMMUNITY_WEIGHT_FLOOR = 0.1``
#: used to live here (``max(S_edge, 0.1)``): a freshly-``declared``
#: (never-Dream-potentiated) edge starts at ``storage_strength=0.0``, so
#: weighting community structure purely by S_edge made every declared
#: needs/composes edge structurally invisible until Dream (M4) existed.
#: This was this defect's first local workaround, at the wrong
#: magnitude, and is now superseded by the general rule (spec §3.3.1):
#: community weights use ``S_eff = max(edge.storage_strength,
#: w_authored(edge))`` via :func:`magicite.core.edge_weight
#: .effective_strength`, computed in :func:`_compute_communities` below.
#: Two competing floors would be a maintenance trap, so this one is
#: deleted rather than kept alongside the new rule.


def _now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_routing_view(engram: Engram) -> str:
    """Canonical, versioned positive routing text embedded for an engram."""
    fm = engram.frontmatter
    return json.dumps(
        {
            "schema": ROUTING_VIEW_SCHEMA,
            "fields": list(ROUTING_VIEW_FIELDS),
            "intent.does": fm.intent.does,
            "intent.use_when": fm.intent.use_when,
            "triggers.positive": list(fm.triggers.positive),
            "procedure.text": [step.text for step in engram.body.procedure],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_contraindication_view(engram: Engram) -> str | None:
    """Canonical negative routing text, kept separate from positive text."""
    fm = engram.frontmatter
    if fm.intent.not_when is None and not fm.triggers.negative:
        return None
    return json.dumps(
        {
            "schema": CONTRAINDICATION_VIEW_SCHEMA,
            "fields": ["intent.not_when", "triggers.negative"],
            "intent.not_when": fm.intent.not_when,
            "triggers.negative": list(fm.triggers.negative),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def embeddable_text(engram: Engram) -> str:
    """Compatibility alias for the canonical v1 positive routing view."""
    return canonical_routing_view(engram)


def project_v1_to_durable_engram(artifact: EngramV1, *, server_origin: str) -> Engram:
    """Project an admitted EngramV1 onto the 0.2 durable mirror shape.

    The on-disk file remains engram/1.0; only the rebuildable SQLite row set
    uses this projection. Local trust/admission is never taken from the file.
    """
    fm1 = artifact.frontmatter
    legacy = fm1.legacy or {}
    plasticity_raw = legacy.get("plasticity") if isinstance(legacy, dict) else None
    if isinstance(plasticity_raw, dict):
        plasticity = Plasticity.model_validate(plasticity_raw)
    else:
        plasticity = Plasticity(status="nascent")

    journal: list[ProvenanceJournalEntry] = []
    for entry in fm1.origin.journal:
        journal.append(
            ProvenanceJournalEntry(
                version=entry.version,
                timestamp=entry.timestamp,
                author=entry.author,
                event=entry.event,
                note=entry.note,
                summary_of_change=entry.summary_of_change,
                signal_tier=entry.signal_tier,
                base_version=entry.base_version,
            )
        )

    needs: list[str] = []
    if isinstance(legacy, dict):
        for key in ("needs", "composes", "inhibits", "yields", "affinity"):
            vals = legacy.get(key)
            if isinstance(vals, list):
                if key == "needs":
                    needs = [str(v) for v in vals]

    frontmatter = EngramFrontmatter(
        spec="engram/0.2",
        name=fm1.name,
        id=fm1.id,
        version=fm1.version,
        provenance=server_origin,  # type: ignore[arg-type]
        parents=list(fm1.parents),
        intent=Intent(
            does=fm1.intent.does,
            use_when=fm1.intent.use_when,
            not_when=fm1.intent.not_when,
        ),
        triggers=Triggers(
            positive=list(fm1.routing.positive),
            negative=list(fm1.routing.negative),
        ),
        plasticity=plasticity,
        peak_storage_strength=float(legacy.get("peak_storage_strength") or 0.0)
        if isinstance(legacy, dict)
        else 0.0,
        needs=needs,
        composes=[str(v) for v in legacy.get("composes", [])]
        if isinstance(legacy, dict) and isinstance(legacy.get("composes"), list)
        else [],
        inhibits=[str(v) for v in legacy.get("inhibits", [])]
        if isinstance(legacy, dict) and isinstance(legacy.get("inhibits"), list)
        else [],
        yields=[str(v) for v in legacy.get("yields", [])]
        if isinstance(legacy, dict) and isinstance(legacy.get("yields"), list)
        else [],
        affinity=[str(v) for v in legacy.get("affinity", [])]
        if isinstance(legacy, dict) and isinstance(legacy.get("affinity"), list)
        else [],
        provenance_journal=journal,
        trust=Trust(
            origin="imported"
            if server_origin in ("imported", "distilled")
            else "authored",
            verification_status="pending",
            signer=fm1.origin.signer,
            import_source=fm1.origin.import_source,
        ),
        skill_md_source=fm1.skill_md_source,
    )
    return Engram(
        frontmatter=frontmatter,
        body=artifact.body,
        path=artifact.path,
        content_sha256=artifact.content_sha256,
        body_sha256=artifact.body_sha256,
        file_mtime_ns=artifact.file_mtime_ns,
    )


def _path_inside_registry(cfg: Config, file_path: Path) -> bool:
    try:
        file_path.resolve().relative_to(cfg.registry_dir.resolve())
        return True
    except ValueError:
        return False


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


def identity_hash(engram: Engram) -> str:
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


def embed_and_store(conn: sqlite3.Connection, embedder: Embedder, engram: Engram) -> None:
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
    contraindication_text = canonical_contraindication_view(engram)
    negative_model = contraindication_model_name(embedder.model_name)
    if contraindication_text is None:
        ephemeral_mod.delete_embedding(conn, engram_id=engram.id, model_name=negative_model)
    else:
        ephemeral_mod.upsert_embedding(
            conn,
            engram_id=engram.id,
            model_name=negative_model,
            dim=embedder.dim,
            vec=embedder.embed(contraindication_text),
            source_sha256=identity_hash(engram),
        )
    durable_mod.set_embedding_ref(
        conn, engram_id=engram.id, model_name=embedder.model_name, source_sha256=engram.body_sha256
    )


def _lint_profile_for(
    engram: Engram,
    *,
    intake_channel: trust_mod.SourceChannel | None = None,
) -> str:
    """CR-4: an imported engram's *file* keeps the lenient ``import`` lint
    profile on every subsequent parse -- register()'s native-``.egr.md``
    path re-scanning it, and sync()'s full-registry rebuild scan -- not
    just at the moment of conversion.

    S04: intake channel also forces the import profile for external /
    bundle / skillmd channels so a forged ``provenance: authored`` claim
    cannot opt into the strict native path (C10).
    """
    if intake_channel in ("external_file", "bundle_import", "skillmd_import"):
        return "import"
    return "import" if engram.frontmatter.provenance == "imported" else "strict"


def _ingest_one(
    conn: sqlite3.Connection,
    embedder: Embedder,
    engram: Engram,
    *,
    profile: str,
    registry_dir: Path,
    cfg: Config | None = None,
    intake_channel: trust_mod.SourceChannel = "local_register",
    signature_valid: bool | None = None,
    signer_fingerprint: str | None = None,
) -> tuple[RegisteredEntry | None, ValidationError | None, bool, list[str]]:
    """Returns (registered_entry, validation_error, skipped_unchanged, dangling)."""
    result = lint_mod.lint(engram, profile=profile)  # type: ignore[arg-type]
    if profile == "strict" and not result.ok:
        msg = "; ".join(f"{i.rule}: {i.message}" for i in result.errors)
        return None, ValidationError(path=engram.path, message=msg), False, []

    existing = conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (engram.id,)).fetchone()
    if existing is not None and existing["content_sha256"] == engram.content_sha256:
        return None, None, True, []

    # C10: server-owned origin from intake channel — file-declared authored
    # / verified cannot impersonate locally authored content.
    file_declared = engram.frontmatter.provenance
    server_origin = lifecycle_mod.server_owned_origin(
        intake_channel=intake_channel, file_declared_origin=file_declared
    )
    engram.frontmatter.provenance = server_origin  # type: ignore[assignment]

    # M5 security fix #1 + AC-028 (docs/06 §Injection-Surface Analysis):
    # verification_status is SERVER-ASSIGNED here, never read from the
    # file's own `trust.verification_status` -- an engram whose frontmatter
    # declares `verification_status: verified` must not be accepted
    # verbatim (that is the exact enabler a planted, "pre-verified" import
    # would need). The scan also runs on every native `.egr.md` re-scan,
    # not just first import, so an engram edited on disk to add an exec
    # block after registration is caught on the next register()/sync()
    # pass, not only at first ingestion.
    scan = lint_mod.injection_scan(engram)
    verification_status = lifecycle_mod.initial_verification_status(
        origin=server_origin, lint_ok=result.ok, scan=scan
    )
    # S04: a still-valid digest-bound admission restores verified on rebuild.
    if (
        cfg is not None
        and not scan.quarantine_recommended
        and trust_mod.admission_still_valid(
            cfg, engram_id=engram.id, content_digest=engram.content_sha256
        )
    ):
        verification_status = "verified"

    fm = engram.frontmatter
    trust_origin = (
        server_origin if server_origin in ("authored", "imported", "distilled") else "authored"
    )
    if fm.trust is None:
        fm.trust = Trust(origin=trust_origin, verification_status=verification_status)  # type: ignore[arg-type]
    else:
        fm.trust = fm.trust.model_copy(
            update={"origin": trust_origin, "verification_status": verification_status}
        )

    durable_mod.upsert_engram(conn, engram, identity_sha256=identity_hash(engram))
    durable_mod.wire_context_affinity(conn, engram)
    durable_mod.reconcile_file_edges(conn, engram)
    dangling = durable_mod.wire_declared_edges(conn, engram)
    # spec §2.6 step 4, second half: upsert learned/declared-with-learned-
    # weight edges from the file's own `synapses:` block, provenance from
    # the file. Runs *after* wire_declared_edges -- see
    # storage.durable.wire_synapse_edges's docstring for why the order is
    # load-bearing (a checkpointed declared edge's real S/evidence_count
    # must win over wire_declared_edges's S=0.0 baseline, not the reverse).
    dangling = list(dict.fromkeys([*dangling, *durable_mod.wire_synapse_edges(conn, engram)]))
    embed_and_store(conn, embedder, engram)

    # S04: external / imported intake is staged pending local admission.
    # Do not emit a newer pending record that would shadow a still-valid admit.
    if (
        cfg is not None
        and server_origin in ("imported", "distilled")
        and verification_status != "verified"
        and not trust_mod.admission_still_valid(
            cfg, engram_id=fm.id, content_digest=engram.content_sha256
        )
    ):
        trust_mod.record_pending_intake(
            cfg,
            conn,
            engram_id=fm.id,
            content_digest=engram.content_sha256,
            source_channel=intake_channel,
            actor="register",
            signature_valid=signature_valid,
            signer_fingerprint=signer_fingerprint,
            reasons=("awaiting local admission",),
        )

    warnings = [w.message for w in result.warnings]
    if scan.quarantine_recommended:
        reasons = []
        if scan.has_exec_blocks:
            reasons.append("exec block(s) present")
        if scan.over_broad_triggers:
            reasons.append("over-broad triggers")
        if scan.suspicious_pitfalls:
            reasons.append("suspicious pitfall text")
        warnings.append(f"quarantined by injection scan: {', '.join(reasons)}")

    entry = RegisteredEntry(
        id=fm.id,
        name=fm.name,
        origin=server_origin,
        status=fm.plasticity.status if fm.plasticity else "nascent",
        verification_status=verification_status,
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
    cfg: Config | None = None,
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

    existing_by_name = conn.execute("SELECT id FROM engram WHERE name = ?", (engram.name,)).fetchone()
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
        conn,
        embedder,
        engram,
        profile="import",
        registry_dir=registry_dir,
        cfg=cfg,
        intake_channel="skillmd_import",
    )
    return entry, verr, False, dangling


def _artifact_to_engram(
    artifact: Engram | EngramV1,
    *,
    intake_channel: trust_mod.SourceChannel,
) -> Engram:
    if isinstance(artifact, EngramV1):
        server_origin = lifecycle_mod.server_owned_origin(
            intake_channel=intake_channel,
            file_declared_origin=artifact.frontmatter.origin.channel,
        )
        return project_v1_to_durable_engram(artifact, server_origin=server_origin)
    return artifact


def _cross_process_lease(
    cfg: Config, conn: sqlite3.Connection, holder_prefix: str
) -> lease_mod.CrossProcessLease:
    """M5 data-integrity fix (defect confirmed by adversarial re-test):
    ``register()``/``sync()``/``export()`` previously held only the
    *logical*, in-process G2 lease (``storage.lease.writer_lease()``) --
    never the *cross-process* ``WriterLease`` (spec §4.2) Dream's own
    ``run()``/``run_checkpoint_only()`` already acquire. A concurrent
    ``sync()`` in a second process (or a second `magicite serve`) could
    therefore run **while Dream held the cross-process lease**, read the
    file Dream had not yet checkpointed its in-memory commits to, and
    re-ingest it -- silently clobbering just-committed learned state
    (S, success/failure counts, `last_applied`) with the stale on-disk
    values, with Dream then checkpointing *that* clobbered state and
    reporting success. Every durable-write entry point in this module now
    acquires the same cross-process lease Dream does, first -- one real,
    OS-and-DB-backed single-writer guarantee, not two independently
    partial ones."""
    return lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path,
        conn=conn,
        holder=f"{holder_prefix}:{os.getpid()}:{uuid.uuid4().hex[:6]}",
    )


def _ensure_registry_gitignore(cfg: Config) -> None:
    """spec §1: register() writes a .gitignore into .magicite/engrams/ on first
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
    cfg.ensure_dirs()
    trust_mod.ensure_trust_dirs(cfg)
    project_root = cfg.project_root.resolve()
    _ensure_registry_gitignore(cfg)
    scan_root = _resolve_scan_root(project_root, path)
    egr_files, skill_files = _discover_files(scan_root, fmt)

    outcome = IngestOutcome()
    cross_lease = _cross_process_lease(cfg, conn, "register")
    with cross_lease.acquire(), lease_mod.writer_lease():
        for file_path in egr_files:
            inside = _path_inside_registry(cfg, file_path)
            channel = trust_mod.classify_intake_channel(
                fmt="egr",
                from_bundle=False,
                path_inside_registry=inside,
            )
            try:
                artifact, _doc = parser_mod.load_artifact_file(
                    file_path,
                    registry_root=project_root,
                    require_asset_files=inside,
                )
                engram = _artifact_to_engram(artifact, intake_channel=channel)
            except parser_mod.EngramParseError as exc:
                outcome.validation_errors.append(ValidationError(path=str(file_path), message=str(exc)))
                continue

            # C10 staging: external intake is copied into the registry root so
            # rebuild/sync cannot drop the quarantined/pending bytes.
            if not inside:
                staged = cfg.registry_dir / f"{engram.frontmatter.name}.egr.md"
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(file_path.read_bytes())
                engram.path = str(staged.resolve().relative_to(project_root))
                try:
                    engram.file_mtime_ns = staged.stat().st_mtime_ns
                except OSError:
                    pass

            entry, verr, skipped, dangling = _ingest_one(
                conn,
                embedder,
                engram,
                profile=_lint_profile_for(engram, intake_channel=channel),
                registry_dir=cfg.registry_dir,
                cfg=cfg,
                intake_channel=channel,
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
                cfg=cfg,
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


def _compute_similar_to_edges(conn: sqlite3.Connection, model_name: str, *, top_m: int) -> None:
    """spec §2.6 step 8: derived top-``m`` cosine kNN ``similar_to`` edges,
    DB-only (``storage.durable.replace_similar_to_edges``'s own docstring
    explains why they never enter ``synapses:``)."""
    # eph_embedding carries no FK to engram (it is Tier-C, keyed to
    # survive a provider/model change independently of any one engram's
    # lifecycle) -- a row can outlive its engram (e.g. sync() just deleted
    # the engram for a vanished file, spec §2.6 step 5, but has not yet
    # pruned the orphaned Tier-C vector). Joining against the *current*
    # engram table here is what keeps a derived edge from ever naming a
    # src/dst that no longer exists (which would otherwise trip the
    # `edge.src_id REFERENCES engram(id)` foreign key).
    rows = conn.execute(
        """
        SELECT x.engram_id AS engram_id, x.vec AS vec
        FROM eph_embedding x JOIN engram e ON e.id = x.engram_id
        WHERE x.model = ?
        """,
        (model_name,),
    ).fetchall()
    if len(rows) < 2:
        durable_mod.replace_similar_to_edges(conn, {})
        return

    ids = [r["engram_id"] for r in rows]
    matrix = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    names_by_id = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM engram").fetchall()}
    sims = matrix @ matrix.T

    neighbors_by_id: dict[str, list[tuple[str, str, float]]] = {}
    for i, src_id in enumerate(ids):
        order = np.argsort(-sims[i])
        picked: list[tuple[str, str, float]] = []
        for j in order:
            dst_id = ids[int(j)]
            if dst_id == src_id or dst_id not in names_by_id:
                continue
            picked.append((names_by_id[dst_id], dst_id, float(sims[i, int(j)])))
            if len(picked) >= top_m:
                break
        neighbors_by_id[src_id] = picked
    durable_mod.replace_similar_to_edges(conn, neighbors_by_id)


def _compute_communities(conn: sqlite3.Connection, cfg: Config) -> str:
    """spec §2.6 step 9: recompute communities (leiden if available, else
    label_propagation). Returns the detector name that actually ran, for
    :attr:`SyncOutcome.detector` (AC-022: honest reporting).

    [DECLARED-EDGES-AMENDED 2026-08-15] edge weight is ``S_eff`` (spec
    §3.3.1), not ``max(S_edge, _COMMUNITY_WEIGHT_FLOOR)`` -- see that
    constant's former docstring, now above :data:`_COMMUNITY_EDGE_TYPES`.
    """
    node_ids = [r["id"] for r in conn.execute("SELECT id FROM engram").fetchall()]
    placeholders = ",".join("?" for _ in _COMMUNITY_EDGE_TYPES)
    rows = conn.execute(
        f"""
        SELECT src_id, dst_id, storage_strength, provenance FROM edge
        WHERE type IN ({placeholders}) AND dangling = 0 AND dst_id IS NOT NULL
        """,
        _COMMUNITY_EDGE_TYPES,
    ).fetchall()
    edges = [
        (
            r["src_id"],
            r["dst_id"],
            edge_weight_mod.effective_strength(
                float(r["storage_strength"]), r["provenance"], cfg.declared_edge_strength
            ),
        )
        for r in rows
    ]

    detector = communities_mod.get_detector()
    partition = detector.detect(node_ids, edges)
    durable_mod.upsert_communities(conn, partition, algo=detector.name)
    return detector.name


def sync(cfg: Config, conn: sqlite3.Connection, embedder: Embedder) -> SyncOutcome:
    cfg.ensure_dirs()
    registry_dir = cfg.registry_dir
    registry_dir.mkdir(parents=True, exist_ok=True)
    project_root = cfg.project_root.resolve()

    on_disk_paths: set[str] = set()
    outcome = IngestOutcome()

    cross_lease = _cross_process_lease(cfg, conn, "sync")
    with cross_lease.acquire(), lease_mod.writer_lease():
        for file_path in sorted(registry_dir.rglob("*.egr.md")):
            relpath = str(file_path.resolve().relative_to(project_root))
            on_disk_paths.add(relpath)
            try:
                artifact, _doc = parser_mod.load_artifact_file(
                    file_path, registry_root=project_root, require_asset_files=True
                )
                engram = _artifact_to_engram(artifact, intake_channel="local_register")
            except parser_mod.EngramParseError as exc:
                outcome.validation_errors.append(ValidationError(path=relpath, message=str(exc)))
                durable_mod.disable_projection_by_path(conn, relpath)
                continue

            _entry, verr, _skipped, _dangling = _ingest_one(
                conn,
                embedder,
                engram,
                profile=_lint_profile_for(engram, intake_channel="local_register"),
                registry_dir=registry_dir,
                cfg=cfg,
                intake_channel="local_register",
            )
            if verr:
                outcome.validation_errors.append(verr)
                durable_mod.disable_projection_by_path(conn, relpath)

        # M5 data-integrity fix: an archived engram's DB row legitimately
        # points at `.magicite/archive/...`, outside `registry_dir` -- this
        # loop must not treat that as "the file vanished". Before this
        # fix, `sync()` deleted every archived engram's row on the very
        # next call (no restoration action required to trigger it),
        # silently losing its index/history/edges even though the file
        # itself was correctly still sitting in `.magicite/archive/`
        # ("archive, never delete" held for the file but not the index).
        removed: list[str] = []
        for row in conn.execute("SELECT id, name, path, status FROM engram").fetchall():
            if row["status"] == "archived":
                continue
            if row["path"] not in on_disk_paths:
                durable_mod.delete_engram(conn, row["id"])
                removed.append(row["name"])

        # spec §2.6 step 6: one comprehensive dangling-resolution pass,
        # after deletions, independent of file processing order (see
        # storage.durable.recompute_dangling's docstring).
        dangling = durable_mod.recompute_dangling(conn)

        # spec §2.6 step 8: derived similar_to kNN edges (rebuilt from
        # whatever embeddings exist for this run's provider/model).
        _compute_similar_to_edges(conn, embedder.model_name, top_m=cfg.similar_to_top_m)

        # spec §2.6 step 9: recompute communities -- leiden if available,
        # else label_propagation (AC-022). Runs *after* step 8 so the
        # community graph can see the kNN edges it just derived.
        detector_name = _compute_communities(conn, cfg)

        durable_mod.write_schema_meta(conn, "last_sync", _now())

        # spec §5.2: approvals are "durable outside the rebuildable DB...
        # reloaded on sync()". A deleted-and-rebuilt skill-graph.db (this
        # very function's own AC-009 scenario) starts with an empty
        # `approval` table; this repopulates it from the JSON mirror so a
        # rebuild never silently drops pending/decided governance state.
        approvals_mod.reload_from_mirror(cfg, conn)
        trust_mod.reload_from_mirror(cfg, conn)

    synced = conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    return SyncOutcome(
        synced=synced,
        removed=removed,
        validation_errors=outcome.validation_errors,
        dangling=dangling,
        detector=detector_name,
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
    cfg.ensure_dirs()
    project_root = cfg.project_root.resolve()
    target_root = _resolve_scan_root(project_root, out_dir)
    min_rank = _EXPORT_STATUS_RANK[min_status]

    rows = conn.execute("SELECT name, path, status FROM engram ORDER BY name").fetchall()
    eligible = [r for r in rows if _EXPORT_STATUS_RANK.get(r["status"], -1) >= min_rank]

    exported = 0
    cross_lease = _cross_process_lease(cfg, conn, "export")
    with cross_lease.acquire(), lease_mod.writer_lease():
        # Preflight the complete batch before writing any target. A preserved
        # host skill that cannot be exported losslessly aborts the invocation
        # without leaving a partial compatibility tree.
        rendered: list[tuple[Path, str]] = []
        for row in eligible:
            file_path = project_root / row["path"]
            artifact, _doc = parser_mod.load_artifact_file(
                file_path, registry_root=project_root, require_asset_files=False
            )
            engram = _artifact_to_engram(artifact, intake_channel="local_register")
            target = target_root / row["name"] / "SKILL.md"
            rendered.append((target, skillmd_mod.render_skillmd(engram)))

        for target, skillmd_text in rendered:
            writer_mod.atomic_write(target, skillmd_text)
            exported += 1

    return ExportOutcome(
        exported=exported,
        target_dir=str(target_root),
        format="skill",
        note=f"rendered {exported} SKILL.md shim(s) at status >= {min_status!r}",
    )


@dataclass
class BundleImportOutcome:
    ingested: int
    registered: list[RegisteredEntry]
    validation_errors: list[ValidationError]
    manifest_digest: str
    signer_fingerprint: str | None


def import_bundle(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    archive_path: str | Path,
    actor: str = "bundle-import",
) -> BundleImportOutcome:
    """Verify a signed portable bundle offline, then stage members as pending.

    Fail closed on zip-slip, digest mismatch, unknown/revoked keys, or
    non-canonical manifests. Successful members remain pending until
    :func:`review_approve`.
    """
    from magicite.core import bundles as bundles_mod

    cfg.ensure_dirs()
    trust_mod.ensure_trust_dirs(cfg)
    policy = trust_mod.load_policy(cfg)
    archive = Path(archive_path)
    verified = bundles_mod.verify_bundle(
        archive,
        roots=list(policy.active_roots()),
        staging_parent=cfg.runtime_dir / "bundle-staging",
    )
    assert verified.staging_dir is not None
    assert verified.manifest is not None

    project_root = cfg.project_root.resolve()
    outcome = IngestOutcome()
    cross_lease = _cross_process_lease(cfg, conn, "bundle-import")
    with cross_lease.acquire(), lease_mod.writer_lease():
        for entry in verified.manifest.entries:
            if not entry.path.endswith(".egr.md"):
                continue
            src = verified.staging_dir / entry.path
            dest = cfg.registry_dir / Path(entry.path).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            try:
                artifact, _doc = parser_mod.load_artifact_file(
                    dest, registry_root=project_root, require_asset_files=False
                )
                engram = _artifact_to_engram(artifact, intake_channel="bundle_import")
            except parser_mod.EngramParseError as exc:
                outcome.validation_errors.append(ValidationError(path=entry.path, message=str(exc)))
                dest.unlink(missing_ok=True)
                continue
            registered, verr, _skipped, dangling = _ingest_one(
                conn,
                embedder,
                engram,
                profile="import",
                registry_dir=cfg.registry_dir,
                cfg=cfg,
                intake_channel="bundle_import",
                signature_valid=True,
                signer_fingerprint=verified.signer_fingerprint,
            )
            if verr:
                outcome.validation_errors.append(verr)
            if registered:
                outcome.registered.append(registered)
            outcome.dangling.extend(dangling)

    return BundleImportOutcome(
        ingested=len(outcome.registered),
        registered=outcome.registered,
        validation_errors=outcome.validation_errors,
        manifest_digest=verified.manifest_digest or "",
        signer_fingerprint=verified.signer_fingerprint,
    )


def review_approve(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> trust_mod.TrustDecision:
    """Digest-bound local admission + verification_status flip + approval audit."""
    from magicite.errors import BusyError

    if event_id:
        for existing in trust_mod.list_decisions(cfg):
            if existing.event_id == event_id and existing.decision == "admit":
                return existing

    attempts = 0
    while True:
        attempts += 1
        try:
            cross_lease = _cross_process_lease(cfg, conn, "trust-approve")
            with cross_lease.acquire(), lease_mod.writer_lease():
                # Re-check under the writer lease for concurrent callers.
                if event_id:
                    for existing in trust_mod.list_decisions(cfg):
                        if existing.event_id == event_id and existing.decision == "admit":
                            return existing
                decision = trust_mod.approve(
                    cfg,
                    conn,
                    engram_id=engram_id,
                    expected_digest=expected_digest,
                    actor=actor,
                    reason=reason,
                    event_id=event_id,
                )
                lifecycle_mod.apply_local_admission(conn, engram_id=engram_id, admit=True)
                approvals_mod.propose(
                    conn,
                    cfg,
                    op="trust_approve",
                    target_name=engram_id,
                    payload={
                        "decision_id": decision.decision_id,
                        "content_digest": expected_digest,
                        "event_id": event_id,
                    },
                    proposed_by=actor,
                )
            return decision
        except BusyError:
            if event_id is None or attempts >= 32:
                raise
            # Concurrent retry of the same event: wait for the winner, then
            # return the single applied admit.
            for existing in trust_mod.list_decisions(cfg):
                if existing.event_id == event_id and existing.decision == "admit":
                    return existing
            time.sleep(0.01)
            continue


def review_reject(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> trust_mod.TrustDecision:
    cross_lease = _cross_process_lease(cfg, conn, "trust-reject")
    with cross_lease.acquire(), lease_mod.writer_lease():
        decision = trust_mod.reject(
            cfg,
            conn,
            engram_id=engram_id,
            expected_digest=expected_digest,
            actor=actor,
            reason=reason,
            event_id=event_id,
        )
        lifecycle_mod.apply_local_admission(conn, engram_id=engram_id, admit=False)
        approvals_mod.propose(
            conn,
            cfg,
            op="trust_reject",
            target_name=engram_id,
            payload={"decision_id": decision.decision_id, "content_digest": expected_digest},
            proposed_by=actor,
        )
    return decision


def review_revoke(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    actor: str,
    expected_digest: str | None = None,
    reason: str | None = None,
    event_id: str | None = None,
) -> trust_mod.TrustDecision:
    cross_lease = _cross_process_lease(cfg, conn, "trust-revoke")
    with cross_lease.acquire(), lease_mod.writer_lease():
        decision = trust_mod.revoke(
            cfg,
            conn,
            engram_id=engram_id,
            expected_digest=expected_digest,
            actor=actor,
            reason=reason,
            event_id=event_id,
        )
        lifecycle_mod.apply_local_admission(conn, engram_id=engram_id, admit=False)
        approvals_mod.propose(
            conn,
            cfg,
            op="trust_revoke",
            target_name=engram_id,
            payload={"decision_id": decision.decision_id, "content_digest": decision.content_digest},
            proposed_by=actor,
        )
    return decision


def trust_list(cfg: Config) -> list[trust_mod.TrustDecision]:
    """Domain API for S11 ``trust list`` binding."""
    return trust_mod.list_decisions(cfg)


def trust_view_for(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    engram_id: str,
) -> trust_mod.TrustDecisionView:
    """Project current trust state as a TrustDecisionView for S06."""
    row = conn.execute(
        "SELECT content_sha256, status, verification_status, origin FROM engram WHERE id = ?",
        (engram_id,),
    ).fetchone()
    if row is None:
        raise InvalidInputError(f"no engram {engram_id!r}")
    channel: trust_mod.SourceChannel
    origin = str(row["origin"])
    if origin == "authored":
        channel = "local_register"
    elif origin == "imported":
        prior = trust_mod.latest_decision_for(cfg, engram_id)
        channel = prior.source_channel if prior else "external_file"
    else:
        channel = "unknown"
    return trust_mod.project_trust_view(
        cfg,
        engram_id=engram_id,
        content_digest=str(row["content_sha256"]),
        lifecycle_status=str(row["status"]),
        verification_status=str(row["verification_status"]),
        intake_channel=channel,
    )
