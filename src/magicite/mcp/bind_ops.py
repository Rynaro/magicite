"""Public CLI/MCP-callable wrappers over domain APIs (S11).

These are **not** registered as additional MCP tools — C8/C9 keep the
stable 16-tool surface. CLI commands in ``magicite.__main__`` call these
wrappers; future surface amendments can register them without reimplementing
domain logic.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import backup as backup_mod
from magicite.core import evidence as evidence_mod
from magicite.core import policy_store as policy_store_mod
from magicite.core import recovery_gate as gate_mod
from magicite.core import registry as registry_mod
from magicite.errors import InvalidInputError, MagiciteError, NotFoundError
from magicite.obs import doctor as doctor_mod
from magicite.storage import authorizer as authorizer_mod
from magicite.storage import db as db_mod


def _cfg(project_root: str | Path) -> Config:
    cfg = Config.load(str(project_root))
    cfg.ensure_dirs()
    return cfg


def _map_policy_store_error(exc: MagiciteError) -> MagiciteError:
    """Surface typed policy-store failure codes for CLI/MCP clients."""
    msg = (exc.message or str(exc)).lower()
    details = dict(exc.details or {})
    if "corrupt" in msg or "integrity mac" in msg or "unreadable" in msg:
        details.setdefault("reason", "policy_store_corrupt")
        return InvalidInputError(exc.message, details=details, hint=exc.hint, code=exc.code)
    if "missing" in msg and "policy" in msg:
        details.setdefault("reason", "policy_store_missing")
        return InvalidInputError(exc.message, details=details, hint=exc.hint, code=exc.code)
    if details.get("reason") in {
        "policy_store_corrupt",
        "policy_store_missing",
        "unknown_active_policy",
        "stale_current",
    }:
        return exc
    return exc


# ── trust (C10) ─────────────────────────────────────────────────────────


def trust_list(project_root: str | Path) -> list[dict[str, Any]]:
    cfg = _cfg(project_root)
    return [d.to_dict() for d in registry_mod.trust_list(cfg)]


def trust_review(project_root: str | Path, *, engram_id: str) -> dict[str, Any]:
    """Read-only trust view for one engram (does **not** imply approval)."""
    cfg = _cfg(project_root)
    conn = db_mod.connect(cfg.db_path)
    try:
        view = registry_mod.trust_view_for(cfg, conn, engram_id=engram_id)
        return {
            "engram_id": view.engram_id,
            "content_digest": view.content_digest,
            "quarantined": view.quarantined,
            "lifecycle_status": view.lifecycle_status,
            "origin_trusted": view.origin_trusted,
            "signature_valid": view.signature_valid,
            "admitted": view.admitted,
            "note": (
                "Review is read-only. Explicit operator approve/reject/revoke "
                "is required for admission changes."
            ),
        }
    finally:
        conn.close()


def trust_approve(
    project_root: str | Path,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if not actor.strip():
        raise InvalidInputError("actor is required for trust approve (explicit operator action)")
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        decision = registry_mod.review_approve(
            cfg,
            conn,
            engram_id=engram_id,
            expected_digest=expected_digest,
            actor=actor,
            reason=reason,
            event_id=event_id,
        )
        return decision.to_dict()
    finally:
        conn.close()


def trust_reject(
    project_root: str | Path,
    *,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if not actor.strip():
        raise InvalidInputError("actor is required for trust reject (explicit operator action)")
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        decision = registry_mod.review_reject(
            cfg,
            conn,
            engram_id=engram_id,
            expected_digest=expected_digest,
            actor=actor,
            reason=reason,
            event_id=event_id,
        )
        return decision.to_dict()
    finally:
        conn.close()


def trust_revoke(
    project_root: str | Path,
    *,
    engram_id: str,
    actor: str,
    expected_digest: str | None = None,
    reason: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if not actor.strip():
        raise InvalidInputError("actor is required for trust revoke (explicit operator action)")
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        decision = registry_mod.review_revoke(
            cfg,
            conn,
            engram_id=engram_id,
            actor=actor,
            expected_digest=expected_digest,
            reason=reason,
            event_id=event_id,
        )
        return decision.to_dict()
    finally:
        conn.close()


def trust_import_bundle(
    project_root: str | Path,
    *,
    archive_path: str,
    actor: str,
) -> dict[str, Any]:
    if not actor.strip():
        raise InvalidInputError("actor is required for bundle import")
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        from magicite.embeddings import get_embedder

        embedder = get_embedder(cfg)
        outcome = registry_mod.import_bundle(
            cfg, conn, embedder, archive_path=archive_path, actor=actor
        )
        return asdict(outcome)
    finally:
        conn.close()


# ── policy store (S07 domain; S11 CLI) ──────────────────────────────────


def policy_status(project_root: str | Path) -> dict[str, Any]:
    cfg = _cfg(project_root)
    try:
        st = policy_store_mod.status(cfg)
    except MagiciteError as exc:
        raise _map_policy_store_error(exc) from exc
    return {
        "active_digest": st.active_digest,
        "prior_digest": st.prior_digest,
        "state_digest": st.state_digest,
        "records": [
            {
                "digest": r.digest,
                "state": r.state,
                "approval_id": r.approval_id,
                "policy_id": r.manifest.policy_id,
                "reviewed": r.manifest.reviewed,
            }
            for r in st.records
        ],
    }


def policy_activate(
    project_root: str | Path,
    *,
    candidate_digest: str,
    approval_id: str,
    expected_current: str | None,
) -> dict[str, Any]:
    cfg = _cfg(project_root)
    try:
        st = policy_store_mod.activate(
            cfg,
            expected_current=expected_current,
            candidate_digest=candidate_digest,
            approval_id=approval_id,
        )
    except MagiciteError as exc:
        raise _map_policy_store_error(exc) from exc
    return {
        "active_digest": st.active_digest,
        "prior_digest": st.prior_digest,
        "state_digest": st.state_digest,
    }


def policy_rollback(
    project_root: str | Path,
    *,
    prior_digest: str,
    expected_current: str | None,
) -> dict[str, Any]:
    cfg = _cfg(project_root)
    try:
        st = policy_store_mod.rollback(
            cfg,
            expected_current=expected_current,
            prior_digest=prior_digest,
        )
    except MagiciteError as exc:
        raise _map_policy_store_error(exc) from exc
    return {
        "active_digest": st.active_digest,
        "prior_digest": st.prior_digest,
        "state_digest": st.state_digest,
    }


# ── evidence (C6) ───────────────────────────────────────────────────────


def evidence_export(
    project_root: str | Path,
    *,
    export_dir: str | None = None,
    event_ids: list[str] | None = None,
) -> dict[str, Any]:
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        out = evidence_mod.export_evidence(
            cfg,
            conn,
            event_ids=event_ids,
            export_dir=Path(export_dir) if export_dir else None,
        )
        return {
            "export_dir": str(out),
            "privacy_deletion_notice": evidence_mod.EXPORT_COPY_DELETION_NOTICE,
        }
    finally:
        conn.close()


def evidence_delete(
    project_root: str | Path,
    *,
    event_id: str,
    actor: str,
    reason: str | None = None,
) -> dict[str, Any]:
    if not actor.strip():
        raise InvalidInputError("actor is required for evidence delete")
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        return evidence_mod.delete_event(cfg, conn, event_id, reason=reason, actor=actor)
    finally:
        conn.close()


def evidence_retention_status(project_root: str | Path) -> dict[str, Any]:
    cfg = _cfg(project_root)
    return {
        "retention_operational_days": int(
            getattr(cfg, "evidence_retention_operational_days", 30)
        ),
        "retention_audit_days": int(getattr(cfg, "evidence_retention_audit_days", 90)),
        "backup_expiry_days": evidence_mod.backup_expiry_days(cfg),
        "privacy_deletion_notice": evidence_mod.EXPORT_COPY_DELETION_NOTICE,
    }


def evidence_apply_retention(project_root: str | Path) -> dict[str, Any]:
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        deleted = evidence_mod.apply_retention(cfg, conn)
        return {"deleted_event_ids": deleted, "count": len(deleted)}
    finally:
        conn.close()


# ── doctor / backup (S12 domain; S11 CLI) ───────────────────────────────


def doctor_report(project_root: str | Path) -> dict[str, Any]:
    cfg = Config.load(str(project_root))
    return doctor_mod.run_doctor(cfg)


def backup_create(project_root: str | Path, *, dest: str) -> dict[str, Any]:
    cfg = _cfg(project_root)
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        return backup_mod.create_snapshot(cfg, conn, dest)
    finally:
        conn.close()


def backup_restore(
    project_root: str | Path,
    *,
    backup_path: str,
    overlay_path: str | None = None,
    anchor_path: str | None = None,
) -> dict[str, Any]:
    cfg = _cfg(project_root)
    overlay = None
    anchor = None
    if overlay_path:
        overlay = json.loads(Path(overlay_path).read_text(encoding="utf-8"))
    if anchor_path:
        anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    conn = authorizer_mod.writer_connection(cfg.db_path)
    try:
        return backup_mod.restore_snapshot(
            cfg,
            conn,
            backup_path,
            overlay=overlay,
            sequence_anchor=anchor,
        )
    finally:
        conn.close()


def backup_status(project_root: str | Path, *, backup_path: str | None = None) -> dict[str, Any]:
    cfg = Config.load(str(project_root))
    recon = gate_mod.reconciliation_status(cfg)
    out: dict[str, Any] = {
        "reconciliation_required": gate_mod.is_reconciliation_required(cfg),
        "reconciliation": recon,
    }
    if backup_path:
        path = Path(backup_path)
        manifest_file = path / "manifest.json" if path.is_dir() else path
        if manifest_file.is_file():
            out["backup_manifest"] = json.loads(manifest_file.read_text(encoding="utf-8"))
        else:
            raise NotFoundError(f"no backup manifest at {backup_path!r}")
    return out
