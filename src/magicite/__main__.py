"""``magicite`` click CLI: serve|sync|dream|export|tools|doctor|fetch-model
plus S11 operator surfaces: trust|policy|evidence|backup.
"""

from __future__ import annotations

import asyncio
import json
import sys

import click

from magicite.config import Config
from magicite.errors import MagiciteError


def _echo_json(payload: object) -> None:
    click.echo(json.dumps(payload, indent=2, default=str))


def _die_magicite(exc: MagiciteError) -> None:
    from magicite.mcp.redact import redact_error_payload

    _echo_json(redact_error_payload(exc.to_dict()))
    sys.exit(1)


@click.group()
def cli() -> None:
    """Magicite -- a local-first, plasticity-inspired skill router speaking MCP over stdio."""


@cli.command()
@click.option("--project-root", default=".", show_default=True, help="Registry project root.")
def serve(project_root: str) -> None:
    """Boot the stdio MCP server (AC-001/AC-002)."""
    from magicite.mcp.app import run_stdio

    cfg = Config.load(project_root)
    asyncio.run(run_stdio(cfg))


@cli.command(name="tools")
def tools_cmd() -> None:
    """Print the authoritative 16-tool manifest as JSON (AC-003/AC-004)."""
    # Importing mcp.app registers every bind_*.py tool onto TOOL_REGISTRY.
    from magicite.mcp import app as _app  # noqa: F401
    from magicite.mcp.registry import manifest

    click.echo(json.dumps({"tools": manifest()}, indent=2))


@cli.command(name="sync")
@click.option("--project-root", default=".", show_default=True, help="Registry project root.")
def sync_cmd(project_root: str) -> None:
    """Rebuild the durable index from the .egr.md registry (spec §2.6)."""
    from dataclasses import asdict

    from magicite.core import registry as registry_mod
    from magicite.embeddings import get_embedder
    from magicite.storage import db as db_mod

    cfg = Config.load(project_root)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    embedder = get_embedder(cfg)
    outcome = registry_mod.sync(cfg, conn, embedder)
    click.echo(json.dumps(asdict(outcome), indent=2, default=str))


@cli.command(name="dream")
@click.option("--once", is_flag=True, default=False)
@click.option("--autonomous", is_flag=True, default=False)
@click.option("--project-root", default=".", show_default=True, help="Registry project root.")
def dream_cmd(once: bool, autonomous: bool, project_root: str) -> None:
    """Run the Dream consolidation worker inline (spec §4.1)."""
    import json as _json

    from magicite.core import dream as dream_mod
    from magicite.storage import authorizer as authorizer_mod

    if not once:
        raise click.ClickException("magicite dream currently only supports --once (spec §4.1 v1 scope)")

    cfg = Config.load(project_root)
    if autonomous:
        cfg.autonomous = True
    cfg.ensure_dirs()
    conn = authorizer_mod.writer_connection(cfg.db_path)
    result = dream_mod.run(cfg, conn, trigger="cli")
    click.echo(
        _json.dumps(
            {
                "run_id": result.run_id,
                "state": result.state,
                "checkpoint_write_ratio": result.checkpoint_write_ratio,
                "modified_engrams": result.modified_engrams,
                "archived_engrams": result.archived_engrams,
                "stats": result.stats,
            },
            indent=2,
            default=str,
        )
    )


@cli.command(name="export")
@click.option("--out-dir", required=True)
@click.option("--project-root", default=".", show_default=True)
@click.option(
    "--min-status",
    default="consolidated",
    type=click.Choice(["consolidated", "promoted"]),
    show_default=True,
)
def export_cmd(out_dir: str, project_root: str, min_status: str) -> None:
    """Render SKILL.md shims from consolidated+ engrams (spec §5.4)."""
    from dataclasses import asdict

    from magicite.core import registry as registry_mod
    from magicite.storage import db as db_mod

    cfg = Config.load(project_root)
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    outcome = registry_mod.export(cfg, conn, out_dir=out_dir, min_status=min_status)
    click.echo(json.dumps(asdict(outcome), indent=2, default=str))


@cli.command(name="doctor")
@click.option("--project-root", default=".", show_default=True)
def doctor_cmd(project_root: str) -> None:
    """Diagnose the registry/filesystem/embedding-provider setup (doctor/1)."""
    from magicite.mcp import bind_ops

    report = bind_ops.doctor_report(project_root)
    click.echo(json.dumps(report, indent=2, default=str))
    if not report["healthy"]:
        for warning in report["warnings"]:
            click.echo(f"WARNING: {warning}", err=True)
        raise SystemExit(1)


@cli.command(name="fetch-model")
@click.option("--model-name", default=None, help="Override the default fastembed model name.")
def fetch_model_cmd(model_name: str | None) -> None:
    """Pre-download the default ONNX embedding model for offline use."""
    from magicite.embeddings.fastembed_provider import DEFAULT_MODEL_NAME, fetch_model

    resolved = model_name or DEFAULT_MODEL_NAME
    click.echo(f"fetching {resolved!r} ...")
    fetch_model(model_name=resolved)
    click.echo(json.dumps({"fetched": resolved}, indent=2))


# ── S11: trust ──────────────────────────────────────────────────────────


@cli.group(name="trust")
def trust_group() -> None:
    """Bundle trust and local review (C10). Requires explicit operator actor."""


@trust_group.command(name="list")
@click.option("--project-root", default=".", show_default=True)
def trust_list_cmd(project_root: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json({"decisions": bind_ops.trust_list(project_root)})
    except MagiciteError as exc:
        _die_magicite(exc)


@trust_group.command(name="review")
@click.option("--project-root", default=".", show_default=True)
@click.option("--engram-id", required=True)
def trust_review_cmd(project_root: str, engram_id: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.trust_review(project_root, engram_id=engram_id))
    except MagiciteError as exc:
        _die_magicite(exc)


@trust_group.command(name="approve")
@click.option("--project-root", default=".", show_default=True)
@click.option("--engram-id", required=True)
@click.option("--expected-digest", required=True)
@click.option("--actor", required=True, help="Explicit operator identity (required).")
@click.option("--reason", default=None)
@click.option("--event-id", default=None)
def trust_approve_cmd(
    project_root: str,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None,
    event_id: str | None,
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.trust_approve(
                project_root,
                engram_id=engram_id,
                expected_digest=expected_digest,
                actor=actor,
                reason=reason,
                event_id=event_id,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@trust_group.command(name="reject")
@click.option("--project-root", default=".", show_default=True)
@click.option("--engram-id", required=True)
@click.option("--expected-digest", required=True)
@click.option("--actor", required=True)
@click.option("--reason", default=None)
@click.option("--event-id", default=None)
def trust_reject_cmd(
    project_root: str,
    engram_id: str,
    expected_digest: str,
    actor: str,
    reason: str | None,
    event_id: str | None,
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.trust_reject(
                project_root,
                engram_id=engram_id,
                expected_digest=expected_digest,
                actor=actor,
                reason=reason,
                event_id=event_id,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@trust_group.command(name="revoke")
@click.option("--project-root", default=".", show_default=True)
@click.option("--engram-id", required=True)
@click.option("--actor", required=True)
@click.option("--expected-digest", default=None)
@click.option("--reason", default=None)
@click.option("--event-id", default=None)
def trust_revoke_cmd(
    project_root: str,
    engram_id: str,
    actor: str,
    expected_digest: str | None,
    reason: str | None,
    event_id: str | None,
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.trust_revoke(
                project_root,
                engram_id=engram_id,
                actor=actor,
                expected_digest=expected_digest,
                reason=reason,
                event_id=event_id,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@trust_group.command(name="import-bundle")
@click.option("--project-root", default=".", show_default=True)
@click.option("--archive", "archive_path", required=True, type=click.Path(exists=True))
@click.option("--actor", required=True)
def trust_import_bundle_cmd(project_root: str, archive_path: str, actor: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.trust_import_bundle(
                project_root, archive_path=archive_path, actor=actor
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


# ── S11: policy ─────────────────────────────────────────────────────────


@cli.group(name="policy")
def policy_group() -> None:
    """Reviewed policy activation/rollback (compare-and-swap)."""


@policy_group.command(name="status")
@click.option("--project-root", default=".", show_default=True)
def policy_status_cmd(project_root: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.policy_status(project_root))
    except MagiciteError as exc:
        _die_magicite(exc)


@policy_group.command(name="reconcile")
@click.option("--project-root", default=".", show_default=True)
def policy_reconcile_cmd(project_root: str) -> None:
    """Finish an authenticated interrupted policy commit under the writer lease."""
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.policy_reconcile(project_root))
    except MagiciteError as exc:
        _die_magicite(exc)


@policy_group.command(name="activate")
@click.option("--project-root", default=".", show_default=True)
@click.option("--candidate-digest", required=True)
@click.option("--approval-id", required=True)
@click.option(
    "--expected-current",
    default=None,
    help="Compare-and-swap guard; omit/empty when no incumbent.",
)
def policy_activate_cmd(
    project_root: str,
    candidate_digest: str,
    approval_id: str,
    expected_current: str | None,
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.policy_activate(
                project_root,
                candidate_digest=candidate_digest,
                approval_id=approval_id,
                expected_current=expected_current or None,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@policy_group.command(name="rollback")
@click.option("--project-root", default=".", show_default=True)
@click.option("--prior-digest", required=True)
@click.option("--expected-current", default=None)
def policy_rollback_cmd(
    project_root: str, prior_digest: str, expected_current: str | None
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.policy_rollback(
                project_root,
                prior_digest=prior_digest,
                expected_current=expected_current or None,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


# ── S11: evidence ───────────────────────────────────────────────────────


@cli.group(name="evidence")
def evidence_group() -> None:
    """Evidence export, erasure, and retention (C6)."""


@evidence_group.command(name="export")
@click.option("--project-root", default=".", show_default=True)
@click.option("--export-dir", default=None, type=click.Path())
@click.option("--event-id", "event_ids", multiple=True)
def evidence_export_cmd(
    project_root: str, export_dir: str | None, event_ids: tuple[str, ...]
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.evidence_export(
                project_root,
                export_dir=export_dir,
                event_ids=list(event_ids) or None,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@evidence_group.command(name="delete")
@click.option("--project-root", default=".", show_default=True)
@click.option("--event-id", required=True)
@click.option("--actor", required=True)
@click.option("--reason", default=None)
def evidence_delete_cmd(
    project_root: str, event_id: str, actor: str, reason: str | None
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.evidence_delete(
                project_root, event_id=event_id, actor=actor, reason=reason
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@evidence_group.command(name="retention-status")
@click.option("--project-root", default=".", show_default=True)
def evidence_retention_status_cmd(project_root: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.evidence_retention_status(project_root))
    except MagiciteError as exc:
        _die_magicite(exc)


@evidence_group.command(name="apply-retention")
@click.option("--project-root", default=".", show_default=True)
def evidence_apply_retention_cmd(project_root: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.evidence_apply_retention(project_root))
    except MagiciteError as exc:
        _die_magicite(exc)


# ── S11: backup ─────────────────────────────────────────────────────────


@cli.group(name="backup")
def backup_group() -> None:
    """Backup create/restore/status (C8)."""


@backup_group.command(name="create")
@click.option("--project-root", default=".", show_default=True)
@click.option("--dest", required=True, type=click.Path())
def backup_create_cmd(project_root: str, dest: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.backup_create(project_root, dest=dest))
    except MagiciteError as exc:
        _die_magicite(exc)


@backup_group.command(name="restore")
@click.option("--project-root", default=".", show_default=True)
@click.option("--backup-path", required=True, type=click.Path(exists=True))
@click.option("--overlay", "overlay_path", default=None, type=click.Path(exists=True))
@click.option("--anchor", "anchor_path", default=None, type=click.Path(exists=True))
def backup_restore_cmd(
    project_root: str,
    backup_path: str,
    overlay_path: str | None,
    anchor_path: str | None,
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.backup_restore(
                project_root,
                backup_path=backup_path,
                overlay_path=overlay_path,
                anchor_path=anchor_path,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@backup_group.command(name="status")
@click.option("--project-root", default=".", show_default=True)
@click.option("--backup-path", default=None, type=click.Path())
def backup_status_cmd(project_root: str, backup_path: str | None) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.backup_status(project_root, backup_path=backup_path))
    except MagiciteError as exc:
        _die_magicite(exc)


if __name__ == "__main__":
    cli()
