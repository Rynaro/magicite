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

import hashlib
import hmac
import json
import os
import random
import re
import shutil
import sqlite3
import stat
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

from magicite.config import Config
from magicite.core import approvals as approvals_mod
from magicite.core import communities as communities_mod
from magicite.core import edge_weight as edge_weight_mod
from magicite.core import lifecycle as lifecycle_mod
from magicite.core import trust as trust_mod
from magicite.core import trust_artifacts, writer_guard
from magicite.core.trust_custodian import CustodianError
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
    Synapse,
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
        synapses=[Synapse.model_validate(item) for item in legacy.get("synapses", [])]
        if isinstance(legacy, dict) and isinstance(legacy.get("synapses"), list)
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


# Reserved registry subdirs (crash leftovers must never be ingestible).
_IMPORT_STAGING_DIRNAME = ".import-staging"
_PUBLISH_JOURNAL_NAME = "publish-journal.json"
_QUARANTINE_DIRNAME = "quarantine"
_RESERVED_REGISTRY_DIRNAMES = frozenset({_IMPORT_STAGING_DIRNAME})
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _assert_safe_journal_member_path(name: str) -> PurePosixPath:
    """Same containment rules as zip members (bundles._assert_safe_member_path)."""
    from magicite.core.bundles import _assert_safe_member_path

    return _assert_safe_member_path(name)


def _assert_safe_job_id(job_id: str) -> str:
    """Reject job ids that could introduce path separators or escapes."""
    if not job_id or "\x00" in job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise InvalidInputError(f"unsafe publish job_id rejected: {job_id!r}")
    if not _SAFE_JOB_ID_RE.fullmatch(job_id):
        raise InvalidInputError(f"unsafe publish job_id rejected: {job_id!r}")
    return job_id


def _sanitize_job_id(job_id: str) -> str:
    try:
        return _assert_safe_job_id(job_id)
    except InvalidInputError:
        return f"unsafe-{uuid.uuid4().hex[:16]}"


def _is_reserved_registry_path(
    path: Path | str,
    *,
    registry_root: Path | None = None,
) -> bool:
    """True when ``path`` lies under a magicite-reserved registry subdirectory."""
    p = Path(path)
    parts = p.parts
    if any(part in _RESERVED_REGISTRY_DIRNAMES for part in parts):
        return True
    if registry_root is not None:
        try:
            rel_parts = p.resolve().relative_to(registry_root.resolve()).parts
        except (ValueError, OSError):
            rel_parts = ()
        if any(part in _RESERVED_REGISTRY_DIRNAMES for part in rel_parts):
            return True
    return False


def _is_reserved_intake_path(path: Path | str, *, cfg: Config) -> bool:
    """True for registry staging dirs or ``data_dir/quarantine/**`` debris."""
    if _is_reserved_registry_path(path, registry_root=cfg.registry_dir):
        return True
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    try:
        resolved.relative_to((cfg.data_dir / _QUARANTINE_DIRNAME).resolve())
        return True
    except (ValueError, OSError):
        pass
    # Also catch relative spellings before resolve (e.g. mid-walk).
    if _QUARANTINE_DIRNAME in p.parts:
        try:
            resolved.relative_to(cfg.data_dir.resolve())
            return True
        except (ValueError, OSError):
            pass
    return False


def _iter_registry_files(registry_dir: Path, pattern: str) -> list[Path]:
    """rglob under the registry, skipping reserved staging / control dirs."""
    root = registry_dir.resolve()
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.rglob(pattern)
        if p.is_file() and not _is_reserved_registry_path(p, registry_root=root)
    )


def _discover_files(
    scan_root: Path, fmt: str, *, cfg: Config | None = None
) -> tuple[list[Path], list[Path]]:
    """Returns (egr_files, skill_files) under ``scan_root``."""

    def _reserved(p: Path) -> bool:
        if cfg is not None:
            return _is_reserved_intake_path(p, cfg=cfg)
        return _is_reserved_registry_path(p)

    if scan_root.is_file():
        if _reserved(scan_root):
            raise InvalidInputError(
                f"refusing reserved path under staging/quarantine: {scan_root}"
            )
        if scan_root.suffix == ".md" and scan_root.name.endswith(".egr.md"):
            return [scan_root], []
        if scan_root.name == "SKILL.md":
            return [], [scan_root]
        raise InvalidInputError(f"{scan_root} is neither a .egr.md nor a SKILL.md file")

    if _reserved(scan_root):
        raise InvalidInputError(
            f"refusing reserved path under staging/quarantine: {scan_root}"
        )

    egr_files = (
        sorted(
            p
            for p in scan_root.rglob("*.egr.md")
            if p.is_file() and not _reserved(p)
        )
        if fmt in ("auto", "egr")
        else []
    )
    skill_files = (
        sorted(
            p
            for p in scan_root.rglob("SKILL.md")
            if p.is_file() and not _reserved(p)
        )
        if fmt in ("auto", "skill")
        else []
    )
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
    resource_digest: str | None = None,
) -> tuple[RegisteredEntry | None, ValidationError | None, bool, list[str]]:
    """Returns (registered_entry, validation_error, skipped_unchanged, dangling)."""
    if cfg is not None and engram.path:
        candidate = Path(engram.path)
        if not candidate.is_absolute():
            candidate = (cfg.project_root / engram.path).resolve()
        if _is_reserved_registry_path(candidate, registry_root=cfg.registry_dir):
            return (
                None,
                ValidationError(
                    path=engram.path,
                    message=(
                        f"refusing to ingest reserved registry path under "
                        f"{_IMPORT_STAGING_DIRNAME}"
                    ),
                ),
                False,
                [],
            )

    if cfg is not None:
        try:
            verified = trust_artifacts.require_bound_artifact(cfg, cfg.project_root / engram.path)
            if verified.id != engram.id or verified.content_sha256 != engram.content_sha256:
                raise CustodianError("parsed object differs from authenticated bytes")
        except (CustodianError, OSError, parser_mod.EngramParseError):
            return (None, ValidationError(path=engram.path,
                    message="authenticated enrollment artifact required"), False, [])

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
            cfg,
            engram_id=engram.id,
            content_digest=engram.content_sha256,
            resource_digest=(
                resource_digest
                if resource_digest is not None
                else trust_mod.compute_resource_digest_at(cfg, relpath=engram.path)
            ),
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
            cfg,
            engram_id=fm.id,
            content_digest=engram.content_sha256,
            resource_digest=(
                resource_digest
                if resource_digest is not None
                else trust_mod.compute_resource_digest_at(cfg, relpath=engram.path)
            ),
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
            resource_digest=(
                resource_digest
                if resource_digest is not None
                else trust_mod.compute_resource_digest_at(cfg, relpath=engram.path)
            ),
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
    raw_source = path.read_bytes()
    raw_text = raw_source.decode("utf-8")
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
    if cfg is not None:
        trust_artifacts.publish_new_artifact(
            cfg, target_path, writer_mod.render_document(engram, None).encode(), actor=actor,
            source_document=raw_source
        )
        artifact, _doc = trust_artifacts.load_registry_artifact(cfg, target_path)
        engram = _artifact_to_engram(artifact, intake_channel="skillmd_import")
    else:
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
    return writer_guard.registry_writer_lease(cfg, conn,
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
    if _is_reserved_intake_path(scan_root, cfg=cfg):
        raise InvalidInputError(
            f"refusing reserved path under staging/quarantine: {scan_root}"
        )
    egr_files, skill_files = _discover_files(scan_root, fmt, cfg=cfg)

    outcome = IngestOutcome()
    cross_lease = _cross_process_lease(cfg, conn, "register")
    with cross_lease.acquire(), lease_mod.writer_lease():
        _recover_incomplete_bundle_publishes(cfg, conn, cfg.registry_dir)
        for file_path in egr_files:
            inside = _path_inside_registry(cfg, file_path)
            channel = trust_mod.classify_intake_channel(
                fmt="egr",
                from_bundle=False,
                path_inside_registry=inside,
            )
            try:
                artifact, _doc = trust_artifacts.load_registry_artifact(
                    cfg, file_path, require_asset_files=inside,
                )
                engram = _artifact_to_engram(artifact, intake_channel=channel)
            except parser_mod.EngramParseError as exc:
                outcome.validation_errors.append(ValidationError(path=str(file_path), message=str(exc)))
                continue

            target = file_path if inside else cfg.registry_dir / f"{engram.frontmatter.name}.egr.md"
            try:
                trust_artifacts.publish_new_artifact(cfg, target, file_path.read_bytes(), actor="register")
                artifact, _doc = trust_artifacts.load_registry_artifact(cfg, target)
                engram = _artifact_to_engram(artifact, intake_channel=channel)
            except (CustodianError, parser_mod.EngramParseError) as exc:
                outcome.validation_errors.append(ValidationError(path=str(file_path), message=str(exc)))
                continue

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
        ORDER BY x.engram_id
        """,
        (model_name,),
    ).fetchall()
    if len(rows) < 2:
        durable_mod.replace_similar_to_edges(conn, {})
        return

    ids = [r["engram_id"] for r in rows]
    matrix = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]).astype(np.float64)
    names_by_id = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM engram").fetchall()}
    sims = matrix @ matrix.T

    neighbors_by_id: dict[str, list[tuple[str, str, float]]] = {}
    for i, src_id in enumerate(ids):
        # rows are ordered by engram_id, so a stable sort breaks exact
        # similarity ties by ascending id (deterministic, order-independent).
        order = np.argsort(-sims[i], kind="stable")
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
        _recover_incomplete_bundle_publishes(cfg, conn, registry_dir)
        for file_path in _iter_registry_files(registry_dir, "*.egr.md"):
            relpath = str(file_path.resolve().relative_to(project_root))
            on_disk_paths.add(relpath)
            try:
                artifact, _doc = trust_artifacts.load_registry_artifact(cfg, file_path)
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


_ENGRAM_ID_RE = re.compile(r"(?m)^id:\s*[\"']?([^\s\"'#]+)[\"']?\s*$")


def normalize_registry_path_key(rel: str) -> str:
    """NFKC + casefold key for registry path collision checks (APFS-safe)."""
    return unicodedata.normalize("NFKC", rel.replace("\\", "/")).casefold()


def build_registry_path_index(registry_root: Path) -> dict[str, Path]:
    """Map normalize_registry_path_key → relative Path for existing files."""
    root = registry_root.resolve()
    index: dict[str, Path] = {}
    if not root.is_dir():
        return index
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _is_reserved_registry_path(path, registry_root=root):
            continue
        rel = path.relative_to(root).as_posix()
        if rel == ".gitignore":
            continue
        if rel.endswith(".db") or ".db-" in rel or rel.endswith(".db-wal") or rel.endswith(".db-shm"):
            continue
        index[normalize_registry_path_key(rel)] = Path(rel)
    return index


def assert_no_registry_path_collision(
    existing_index: dict[str, Path],
    *,
    candidate: str,
) -> None:
    """Refuse when ``candidate`` casefold/NFC-collides with a different spelling."""
    cand = candidate.replace("\\", "/")
    key = normalize_registry_path_key(cand)
    prior = existing_index.get(key)
    if prior is None:
        return
    prior_s = prior.as_posix()
    if prior_s != cand:
        raise InvalidInputError(
            f"casefold collision with existing registry path: {prior_s!r} vs {cand!r}"
        )


def _parse_engram_id_from_bytes(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    match = _ENGRAM_ID_RE.search(text)
    return match.group(1) if match else None


def _engram_id_owning_registry_path(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    rel_posix: str,
) -> str | None:
    """Best-effort owner of an on-disk registry relative path."""
    project_root = cfg.project_root.resolve()
    registry_root = cfg.registry_dir.resolve()
    abs_path = (registry_root / rel_posix).resolve()
    try:
        project_rel = str(abs_path.relative_to(project_root))
    except ValueError:
        project_rel = rel_posix

    row = conn.execute(
        "SELECT id FROM engram WHERE path = ? OR path = ?",
        (project_rel, rel_posix),
    ).fetchone()
    if row is not None:
        return str(row["id"])

    # Fallback: parse the on-disk .egr.md if present.
    if abs_path.is_file() and rel_posix.endswith(".egr.md"):
        return _parse_engram_id_from_bytes(abs_path.read_bytes())
    return None


def _assert_import_destinations_safe(
    *,
    conn: sqlite3.Connection,
    cfg: Config,
    registry_root: Path,
    verified_staging: Path,
    manifest_entries: list[Any],
    transformed_bytes: dict[str, bytes] | None = None,
) -> tuple[dict[str, bool], list[str]]:
    """Pre-check every destination. Returns (skip_publish, pre_existing_paths).

    Refuses the whole import (no destination writes) when any destination
    exists with differing bytes, a casefold collision with a different
    spelling, or byte-identical content owned by a different engram.
    ``pre_existing_paths`` is the registry path-index snapshot used by the
    authenticated publish journal so crash recovery never moves authored files.
    """
    existing = build_registry_path_index(registry_root)
    skip_publish: dict[str, bool] = {}
    bundle_keys: dict[str, str] = {}

    for entry in manifest_entries:
        rel = entry.path.replace("\\", "/")
        key = normalize_registry_path_key(rel)
        if key in bundle_keys and bundle_keys[key] != rel:
            raise InvalidInputError(
                f"casefold collision among bundle members: "
                f"{bundle_keys[key]!r} vs {rel!r}"
            )
        bundle_keys[key] = rel

        # Casefold vs existing registry (distinct spelling).
        assert_no_registry_path_collision(existing, candidate=rel)

        dest = registry_root / rel
        src = verified_staging / rel
        incoming = transformed_bytes[rel] if transformed_bytes is not None else src.read_bytes()

        # Exact path exists?
        if dest.exists() or (key in existing and existing[key].as_posix() == rel):
            # Resolve through the index when FS is case-insensitive.
            existing_rel = existing.get(key)
            if existing_rel is not None and existing_rel.as_posix() != rel:
                raise InvalidInputError(
                    f"casefold collision with existing registry path: "
                    f"{existing_rel.as_posix()!r} vs {rel!r}"
                )
            on_disk = (registry_root / (existing_rel.as_posix() if existing_rel else rel)).read_bytes()
            if on_disk != incoming:
                raise InvalidInputError(
                    f"import would overwrite existing registry path: {rel!r}",
                    details={"path": rel, "reason": "clobber"},
                )
            # Byte-identical: require same-engram ownership for .egr.md.
            if rel.endswith(".egr.md"):
                incoming_id = _parse_engram_id_from_bytes(incoming)
                owner = _engram_id_owning_registry_path(conn, cfg, rel_posix=rel)
                if owner is None:
                    owner = _parse_engram_id_from_bytes(on_disk)
                if incoming_id is None or owner is None or incoming_id != owner:
                    raise InvalidInputError(
                        f"import would clobber path owned by another engram: {rel!r}",
                        details={
                            "path": rel,
                            "incoming_id": incoming_id,
                            "owner_id": owner,
                            "reason": "clobber",
                        },
                    )
            skip_publish[rel] = True
        else:
            # Destination absent under this spelling, but casefold hit?
            if key in existing:
                raise InvalidInputError(
                    f"casefold collision with existing registry path: "
                    f"{existing[key].as_posix()!r} vs {rel!r}"
                )
            skip_publish[rel] = False

    pre_existing_paths = sorted({p.as_posix() for p in existing.values()})
    return skip_publish, pre_existing_paths


def _fsync_dir(path: Path) -> None:
    dir_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


_JOURNAL_MAC_DOMAIN = b"magicite/bundle-publish-journal/v1"
_QUARANTINE_IMPORT_ROLLBACK = ("quarantine", "import-rollback")


def _journal_mac_subkey(cfg: Config) -> bytes:
    """Domain-separated HMAC subkey from the local fingerprint key material."""
    from magicite.core import fingerprint_key as fingerprint_key_mod

    root = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    return hmac.new(root, _JOURNAL_MAC_DOMAIN, hashlib.sha256).digest()


def _canonical_journal_payload(payload: dict[str, Any]) -> bytes:
    from magicite.engram.digests import canonical_json_bytes

    body = {k: v for k, v in payload.items() if k != "mac"}
    return canonical_json_bytes(body)


def _sign_publish_journal(cfg: Config, payload: dict[str, Any]) -> dict[str, Any]:
    """Attach ``mac`` over the canonical journal body (excluding ``mac``)."""
    mac = hmac.new(
        _journal_mac_subkey(cfg),
        _canonical_journal_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    signed = dict(payload)
    signed["mac"] = mac
    return signed


def _verify_publish_journal(cfg: Config, payload: dict[str, Any]) -> bool:
    mac = payload.get("mac")
    if not isinstance(mac, str) or not mac:
        return False
    expected = hmac.new(
        _journal_mac_subkey(cfg),
        _canonical_journal_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(mac, expected)


def _write_publish_journal(cfg: Config, job_dir: Path, payload: dict[str, Any]) -> Path:
    """Write + fsync an authenticated publish journal before the first os.replace."""
    signed = _sign_publish_journal(cfg, payload)
    path = job_dir / _PUBLISH_JOURNAL_NAME
    raw = json.dumps(signed, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    _fsync_dir(job_dir)
    return path


def _load_publish_journal(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _import_rollback_quarantine_root(cfg: Config) -> Path:
    return cfg.data_dir.joinpath(*_QUARANTINE_IMPORT_ROLLBACK)


def _quarantine_tree(cfg: Config, src: Path, *, job_id: str) -> Path:
    """Move ``src`` (file or dir) under quarantine/import-rollback/<job_id>/."""
    safe_job = _sanitize_job_id(job_id)
    dest_root = _import_rollback_quarantine_root(cfg) / safe_job
    dest_root.mkdir(parents=True, exist_ok=True)
    target = dest_root / src.name
    # Avoid clobbering a prior quarantine of the same name.
    if target.exists():
        target = dest_root / f"{src.name}.{uuid.uuid4().hex[:8]}"
    # Containment: target must stay under quarantine root.
    qroot = _import_rollback_quarantine_root(cfg).resolve()
    try:
        target.resolve(strict=False).relative_to(qroot)
    except ValueError as exc:
        raise InvalidInputError(
            f"quarantine target escapes quarantine root: {target}"
        ) from exc
    os.replace(src, target)
    _fsync_dir(dest_root)
    return target


def _quarantine_registry_member(
    cfg: Config,
    registry_root: Path,
    *,
    rel: str,
    job_id: str,
) -> tuple[Path | None, str | None]:
    """Move a published registry member into quarantine; never unlink.

    Returns ``(dest, None)`` on success or ``(None, reason)`` when the member
    path fails containment / symlink checks (fail closed: leave in place).
    """
    try:
        safe_rel = str(_assert_safe_journal_member_path(rel))
        safe_job = _assert_safe_job_id(job_id)
    except InvalidInputError as exc:
        return None, f"left in place (unsafe path): {rel} ({exc})"

    reg_root = registry_root.resolve()
    src = (reg_root / safe_rel)
    try:
        src_resolved = src.resolve(strict=False)
        src_resolved.relative_to(reg_root)
    except (ValueError, OSError) as exc:
        return None, f"left in place (escapes registry): {rel} ({exc})"

    try:
        st = src.lstat()
    except OSError as exc:
        return None, f"left in place (unreadable): {rel} ({exc})"
    if stat.S_ISLNK(st.st_mode):
        return None, f"left in place (symlink): {rel}"
    if not stat.S_ISREG(st.st_mode):
        return None, f"left in place (not a regular file): {rel}"

    q_job = _import_rollback_quarantine_root(cfg).resolve() / safe_job
    dest = q_job / safe_rel
    try:
        dest.resolve(strict=False).relative_to(q_job)
    except ValueError as exc:
        return None, f"left in place (quarantine escape): {rel} ({exc})"

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(dest.name + f".{uuid.uuid4().hex[:8]}")
        try:
            dest.resolve(strict=False).relative_to(q_job)
        except ValueError as exc:
            return None, f"left in place (quarantine escape): {rel} ({exc})"

    os.replace(src, dest)
    _fsync_dir(dest.parent)
    return dest, None


def _compensate_published_members(
    cfg: Config,
    conn: sqlite3.Connection,
    registry_root: Path,
    journal: dict[str, Any],
    *,
    job_id: str,
) -> list[str]:
    """Quarantine published members that are safe to roll back. Never unlink.

    A member is moved only when: (i) not owned by a durable engram other than
    the aborted import's engrams, (ii) absent from the pre-import path index
    snapshot, (iii) live bytes still match the journal digest, and (iv) the
    path passes zip-member containment + non-symlink checks. Returns
    human-readable skip reasons for anything left in place.
    """
    from magicite.engram.digests import sha256_hex

    reports: list[str] = []
    try:
        safe_job = _assert_safe_job_id(job_id)
    except InvalidInputError as exc:
        reports.append(f"refusing compensation for unsafe job_id: {job_id!r} ({exc})")
        return reports

    members = journal.get("members")
    if not isinstance(members, list):
        return reports
    pre_existing = {
        str(p).replace("\\", "/")
        for p in (journal.get("pre_existing_paths") or [])
        if isinstance(p, str)
    }
    aborted_ids = {
        str(i) for i in (journal.get("aborted_engram_ids") or []) if isinstance(i, str)
    }

    for member in members:
        if not isinstance(member, dict):
            continue
        if member.get("pre_existing"):
            continue
        rel_raw = str(member.get("path", ""))
        if not rel_raw:
            continue
        try:
            rel = str(_assert_safe_journal_member_path(rel_raw))
        except InvalidInputError as exc:
            reports.append(f"left in place (unsafe path): {rel_raw!r} ({exc})")
            continue
        if rel in pre_existing:
            reports.append(f"left in place (pre-existing snapshot): {rel}")
            continue
        dest = registry_root / rel
        try:
            st = dest.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            reports.append(f"left in place (symlink): {rel}")
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        expected = str(member.get("sha256", ""))
        try:
            live = sha256_hex(dest.read_bytes())
        except OSError as exc:
            reports.append(f"left in place (unreadable): {rel} ({exc})")
            continue
        if not expected or live != expected:
            reports.append(f"left in place (digest mismatch): {rel}")
            continue
        owner = _engram_id_owning_registry_path(conn, cfg, rel_posix=rel)
        if owner is not None and owner not in aborted_ids:
            reports.append(
                f"left in place (owned by other engram {owner}): {rel}"
            )
            continue
        _moved, reason = _quarantine_registry_member(
            cfg, registry_root, rel=rel, job_id=safe_job
        )
        if reason:
            reports.append(reason)
    return reports


def _recover_incomplete_bundle_publishes(
    cfg: Config,
    conn: sqlite3.Connection,
    registry_root: Path,
) -> list[str]:
    """Recover incomplete publish jobs under the writer lease.

    Unauthenticated/corrupt/legacy journals never delete or move registry
    files; the staging job is quarantined for operator review. Authenticated
    incomplete journals quarantine only members that pass ownership +
    pre-existing + digest checks.
    """
    reports: list[str] = []
    staging_root = registry_root / _IMPORT_STAGING_DIRNAME
    if not staging_root.is_dir():
        return reports
    for job_dir in list(staging_root.iterdir()):
        if not job_dir.is_dir():
            continue
        job_id = _sanitize_job_id(job_dir.name)
        journal_path = job_dir / _PUBLISH_JOURNAL_NAME
        if not journal_path.is_file():
            _quarantine_tree(cfg, job_dir, job_id=f"{job_id}-no-journal")
            reports.append(f"quarantined staging job without journal: {job_dir.name}")
            continue
        journal = _load_publish_journal(journal_path)
        if journal is None or not _verify_publish_journal(cfg, journal):
            _quarantine_tree(cfg, job_dir, job_id=f"{job_id}-unauth")
            reports.append(
                f"quarantined unauthenticated/corrupt publish journal: {job_dir.name}"
            )
            continue
        if journal.get("complete") is True:
            shutil.rmtree(job_dir, ignore_errors=True)
            continue
        reports.extend(
            _compensate_published_members(
                cfg, conn, registry_root, journal, job_id=job_id
            )
        )
        # Staging leftovers after compensation: quarantine remaining job dir.
        if job_dir.exists():
            _quarantine_tree(cfg, job_dir, job_id=f"{job_id}-staging")
    return reports


def import_bundle(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    archive_path: str | Path,
    actor: str = "bundle-import",
) -> BundleImportOutcome:
    """Verify a signed portable bundle offline, then stage members as pending.

    Preserves archive-relative hierarchy under the registry root (including
    non-``.egr.md`` assets) so admitted resource digests match on-disk bytes.
    Fail closed on zip-slip, digest mismatch, unknown/revoked keys,
    non-canonical manifests, or any destination that would clobber an
    existing registry path (unless byte-identical same-engram re-import).
    Successful members remain pending until :func:`review_approve`.
    """
    from magicite.core import bundles as bundles_mod
    from magicite.engram.digests import sha256_hex

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
    registry_root = cfg.registry_dir.resolve()
    outcome = IngestOutcome()
    cross_lease = _cross_process_lease(cfg, conn, "bundle-import")
    publish_staging: Path | None = None
    with cross_lease.acquire(), lease_mod.writer_lease():
        _recover_incomplete_bundle_publishes(cfg, conn, registry_root)
        current_policy = trust_mod.load_policy(cfg)
        if verified.signer_fingerprint not in {root.fingerprint for root in current_policy.active_roots()}:
            raise InvalidInputError("bundle signer no longer admitted by current policy")
        journal, held, _ = writer_guard.bound_journal(cfg)
        transformed: dict[str, trust_artifacts.MarkedArtifact] = {}
        targets: dict[str, bytes] = {}
        revisions: dict[str, int] = {}
        for entry in verified.manifest.entries:
            rel = str(_assert_safe_journal_member_path(entry.path))
            raw = (verified.staging_dir / rel).read_bytes()
            if sha256_hex(raw) != entry.sha256 or len(raw) != entry.size:
                raise InvalidInputError("verified bundle source changed before transformation")
            targets[rel] = raw
            if rel.endswith(".egr.md"):
                artifact, _ = parser_mod.parse_artifact(raw.decode(), relpath=rel, admit=True)
                revisions[artifact.id] = artifact.frontmatter.version
        for rel, raw in list(targets.items()):
            if rel.endswith(".egr.md"):
                marked = trust_artifacts.mark_artifact(raw,registry_id=journal.registry_id,relpath=rel,
                    actor=actor,revision_map=revisions,source_signature={"signature_valid": True,
                    "signer_fingerprint": verified.signer_fingerprint,
                    "manifest_digest": verified.manifest_digest})
                transformed[rel] = marked
                targets[rel] = marked.target
        skip_publish, pre_existing_paths = _assert_import_destinations_safe(
            conn=conn,
            cfg=cfg,
            registry_root=registry_root,
            verified_staging=verified.staging_dir,
            manifest_entries=list(verified.manifest.entries),
            transformed_bytes=targets,
        )

        # Stage under the registry root; publish only after checks + re-hash.
        job_id = _assert_safe_job_id(uuid.uuid4().hex)
        publish_staging = registry_root / _IMPORT_STAGING_DIRNAME / job_id
        journal_members: list[dict[str, Any]] = []
        aborted_engram_ids: list[str] = []
        journal_body: dict[str, Any] | None = None
        try:
            for marked in transformed.values():
                trust_artifacts.bind_prepared_transform(cfg, marked)
            for entry in verified.manifest.entries:
                rel = str(_assert_safe_journal_member_path(entry.path))
                src = verified.staging_dir / rel
                staged = publish_staging / rel
                try:
                    staged.resolve(strict=False).relative_to(publish_staging.resolve())
                except ValueError as exc:
                    raise InvalidInputError(
                        f"bundle member escapes publish staging: {rel!r}"
                    ) from exc
                staged.parent.mkdir(parents=True, exist_ok=True)
                raw = src.read_bytes()
                # TOCTOU: re-hash staged bytes against the verified manifest.
                if sha256_hex(raw) != entry.sha256 or len(raw) != entry.size:
                    raise InvalidInputError(
                        f"staged bytes diverged from verified manifest for {rel!r}"
                    )
                raw = targets[rel]
                staged.write_bytes(raw)
                if not skip_publish.get(rel):
                    journal_members.append(
                        {
                            "path": rel,
                            "sha256": sha256_hex(raw),
                            "size": len(raw),
                            "pre_existing": False,
                        }
                    )
                    if rel.endswith(".egr.md"):
                        eid = _parse_engram_id_from_bytes(raw)
                        if eid is not None:
                            aborted_engram_ids.append(eid)

            # Re-validate every journal + pre-existing path before signing.
            for member in journal_members:
                _assert_safe_journal_member_path(str(member["path"]))
            safe_pre_existing = [
                str(_assert_safe_journal_member_path(p)) for p in pre_existing_paths
            ]

            journal_body = {
                "schema": "BundlePublishJournal/1",
                "bundle_id": verified.manifest_digest or job_id,
                "complete": False,
                "members": journal_members,
                "pre_existing_paths": safe_pre_existing,
                "aborted_engram_ids": aborted_engram_ids,
            }

            # Journal BEFORE the first os.replace (crash recovery contract).
            publish_staging.mkdir(parents=True, exist_ok=True)
            _write_publish_journal(cfg, publish_staging, journal_body)

            staged_egr: list[Path] = []
            for entry in verified.manifest.entries:
                rel = str(_assert_safe_journal_member_path(entry.path))
                dest = (registry_root / rel).resolve(strict=False)
                try:
                    dest.relative_to(registry_root)
                except ValueError as exc:
                    raise InvalidInputError(
                        f"bundle member escapes registry root: {rel!r}"
                    ) from exc
                if skip_publish.get(rel):
                    if rel.endswith(".egr.md"):
                        staged_egr.append(dest)
                    continue
                # Refuse to publish through a symlink at the destination.
                if dest.exists() or dest.is_symlink():
                    try:
                        if dest.is_symlink() or stat.S_ISLNK(dest.lstat().st_mode):
                            raise InvalidInputError(
                                f"refusing to publish onto symlink destination: {rel!r}"
                            )
                    except FileNotFoundError:
                        pass
                dest.parent.mkdir(parents=True, exist_ok=True)
                from magicite.core.trust_journal import _directory_fd, _replace_file
                with _directory_fd(dest.parent) as destination_directory:
                    held.assert_owned()
                    _replace_file(destination_directory,dest.name,targets[rel],held.assert_owned)
                held.assert_owned()
                if rel.endswith(".egr.md"):
                    staged_egr.append(dest)

            journal_body = {**journal_body, "complete": True}
            _write_publish_journal(cfg, publish_staging, journal_body)

            for dest in staged_egr:
                rel_in_registry = str(dest.relative_to(registry_root).as_posix())
                try:
                    artifact, _doc = parser_mod.load_artifact_file(
                        dest,
                        registry_root=registry_root,
                        require_asset_files=True,
                    )
                    engram = _artifact_to_engram(artifact, intake_channel="bundle_import")
                    engram.path = str(dest.resolve().relative_to(project_root))
                except parser_mod.EngramParseError as exc:
                    outcome.validation_errors.append(
                        ValidationError(path=rel_in_registry, message=str(exc))
                    )
                    continue
                resource = trust_mod.compute_resource_digest_at(cfg, relpath=engram.path)
                registered, verr, _skipped, dangling = _ingest_one(
                    conn,
                    embedder,
                    engram,
                    profile="import",
                    registry_dir=cfg.registry_dir,
                    cfg=cfg,
                    intake_channel="bundle_import",
                    signature_valid=None,
                    signer_fingerprint=None,
                    resource_digest=resource,
                )
                if verr:
                    outcome.validation_errors.append(verr)
                if registered:
                    outcome.registered.append(registered)
                outcome.dangling.extend(dangling)
        except BaseException:
            if journal_body is not None:
                _compensate_published_members(
                    cfg, conn, registry_root, journal_body, job_id=job_id
                )
            raise
        finally:
            if publish_staging is not None:
                shutil.rmtree(publish_staging, ignore_errors=True)

    return BundleImportOutcome(
        ingested=len(outcome.registered),
        registered=outcome.registered,
        validation_errors=outcome.validation_errors,
        manifest_digest=verified.manifest_digest or "",
        signer_fingerprint=verified.signer_fingerprint,
    )


#: Bound on how long an idempotent ``review_approve`` replay (one carrying an
#: ``event_id``) waits for a competing holder of the trust-approve writer lease.
#: A wall-clock budget (not an attempt count) so slow runners cannot exhaust it;
#: 10s is a generous multiple of any healthy critical section yet well inside
#: ``lease.DEFAULT_LEASE_TTL_S`` (60s, heartbeat 10s), so a wedged holder still
#: fails closed with ``BusyError`` long before its lease could be considered stale.
_REPLAY_BUSY_WAIT_S = 10.0
_REPLAY_BUSY_SLEEP_MIN_S = 0.01
_REPLAY_BUSY_SLEEP_MAX_S = 0.1


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

    deadline = time.monotonic() + _REPLAY_BUSY_WAIT_S
    delay = _REPLAY_BUSY_SLEEP_MIN_S
    while True:
        acquired = False
        try:
            cross_lease = _cross_process_lease(cfg, conn, "trust-approve")
            with cross_lease.acquire():
                acquired = True
                # Nested acquisition delegates to the existing context owner;
                # the newly constructed lease object does not hold its token.
                owner = lease_mod.current_cross_process_lease() or cross_lease
                with lease_mod.writer_lease():
                    decision = None
                    # Re-check under the writer lease for concurrent callers.
                    if event_id:
                        for existing in trust_mod.list_decisions(cfg):
                            if existing.event_id == event_id and existing.decision == "admit":
                                decision = existing
                                break
                    if decision is None:
                        decision = trust_mod.approve(
                            cfg,
                            conn,
                            engram_id=engram_id,
                            expected_digest=expected_digest,
                            actor=actor,
                            reason=reason,
                            event_id=event_id,
                        )
                    # A committed decision alone does not acknowledge its local
                    # effects. Replays must bind the same subject and live bytes,
                    # then finish admission and the one decision-bound audit.
                    if (
                        decision.engram_id != engram_id
                        or decision.content_digest != expected_digest
                        or trust_mod.live_content_digest(conn, engram_id) != decision.content_digest
                    ):
                        raise InvalidInputError(
                            "approval replay does not match the live subject (stale_decision)"
                        )
                    live_resource = trust_mod.live_resource_digest(cfg, conn, engram_id)
                    if not trust_mod.admission_still_valid(
                        cfg,
                        engram_id=engram_id,
                        content_digest=decision.content_digest,
                        resource_digest=live_resource,
                    ):
                        raise InvalidInputError(
                            "admission is not valid after approve (stale_decision)",
                            details={"engram_id": engram_id, "reason": "stale_decision"},
                        )
                    owner.assert_owned()
                    lifecycle_mod.apply_local_admission(conn, engram_id=engram_id, admit=True)
                    owner.assert_owned()
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
                        proposed_by=decision.actor,
                        idempotency_key=decision.decision_id,
                    )
                    owner.assert_owned()
            return decision
        except BusyError:
            # Contention before acquisition is safe to wait out. An error
            # after acquisition (possibly after a durable decision) must be
            # visible to the caller; it cannot become a successful replay.
            if acquired or event_id is None or time.monotonic() >= deadline:
                raise
            # Wait for the winner, then inspect its committed event under
            # the next acquired lease; an in-flight snapshot is not authority.
            time.sleep(delay * (0.5 + random.random() / 2))
            delay = min(delay * 2, _REPLAY_BUSY_SLEEP_MAX_S)
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
        "SELECT content_sha256, status, verification_status, origin, path FROM engram WHERE id = ?",
        (engram_id,),
    ).fetchone()
    if row is None:
        raise InvalidInputError(f"no engram {engram_id!r}")
    channel: trust_mod.SourceChannel
    origin = str(row["origin"])
    if origin == "authored":
        channel = "local_register"
    elif origin == "imported":
        # AC-TH-07: no separate journal read here. ``project_trust_view``
        # resolves the recorded source channel from its single authenticated
        # snapshot; this fallback applies only when that snapshot has no
        # decision (or is unavailable, which also fails admission closed).
        channel = "external_file"
    else:
        channel = "unknown"
    try:
        resource = trust_mod.live_resource_digest(cfg, conn, engram_id)
    except InvalidInputError:
        resource = None
    return trust_mod.project_trust_view(
        cfg,
        engram_id=engram_id,
        content_digest=str(row["content_sha256"]),
        lifecycle_status=str(row["status"]),
        verification_status=str(row["verification_status"]),
        intake_channel=channel,
        resource_digest=resource,
    )
