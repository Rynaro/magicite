"""``magicite`` click CLI: serve|sync|dream|export|tools|doctor|fetch-model
plus S11 operator surfaces: trust|policy|evidence|backup.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import NoReturn, TextIO

import click

from magicite.config import Config
from magicite.errors import MagiciteError


def _echo_json(payload: object) -> None:
    click.echo(json.dumps(payload, indent=2, default=str))


def _die_magicite(exc: MagiciteError) -> NoReturn:
    from magicite.mcp.redact import redact_error_payload

    payload = redact_error_payload(exc.to_dict(), strict=True)
    payload["code"] = exc.code.value
    payload["message"] = f"command failed ({exc.code.value})"
    payload["hint"] = "inspect the error code and command help; correct input or reconcile state"
    _echo_json(payload)
    sys.exit(1)


class SafeGroup(click.Group):
    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except MagiciteError as exc:
            if ctx.invoked_subcommand == "serve":
                click.echo(f"server startup failed ({exc.code.value})", err=True)
                sys.exit(1)
            _die_magicite(exc)
        except (click.ClickException, click.Abort, click.exceptions.Exit):
            # click.exceptions.Exit (e.g. --help) subclasses RuntimeError; it is
            # normal control flow, not an internal error.
            raise
        except Exception as exc:
            if ctx.invoked_subcommand == "serve":
                category = "PermissionError" if isinstance(exc, PermissionError) else "internal"
                click.echo(f"server startup failed ({category})", err=True)
                sys.exit(1)
            _echo_json(
                {
                    "code": "internal",
                    "message": "internal command error",
                    "hint": "inspect local state with doctor; no exception content is disclosed",
                }
            )
            sys.exit(1)


@click.group(cls=SafeGroup)
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
    from magicite.core.writer_guard import preflight_custody

    preflight_custody(cfg)
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
    from magicite.core.writer_guard import preflight_custody

    preflight_custody(cfg)
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
    from magicite.core.writer_guard import preflight_custody

    preflight_custody(cfg)
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
        _echo_json(bind_ops.trust_import_bundle(project_root, archive_path=archive_path, actor=actor))
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
def policy_rollback_cmd(project_root: str, prior_digest: str, expected_current: str | None) -> None:
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


@policy_group.command(name="register-evaluated")
@click.option("--project-root", default=".", show_default=True)
@click.option("--manifest", "manifest_path", required=True, type=click.Path(exists=True))
@click.option(
    "--evaluation-status", required=True, type=click.Choice(["pass", "fail", "inconclusive", "unevaluated"])
)
@click.option("--evidence", required=True)
def policy_register_evaluated_cmd(
    project_root: str, manifest_path: str, evaluation_status: str, evidence: str
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.policy_register_evaluated(
                project_root,
                manifest_path=manifest_path,
                evaluation_status=evaluation_status,
                evidence=evidence,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@policy_group.command(name="approve")
@click.option("--project-root", default=".", show_default=True)
@click.option("--policy-digest", required=True)
@click.option("--actor", required=True)
def policy_approve_cmd(project_root: str, policy_digest: str, actor: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.policy_approve(project_root, policy_digest=policy_digest, actor=actor))
    except MagiciteError as exc:
        _die_magicite(exc)


@cli.group(name="migration")
def migration_group() -> None:
    """Preview, apply, inspect, resume or restore a fenced format migration."""


@migration_group.command(name="preview")
@click.option("--project-root", default=".", show_default=True)
def migration_preview_cmd(project_root: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.migration_preview(project_root))
    except MagiciteError as exc:
        _die_magicite(exc)


@migration_group.command(name="apply")
@click.option("--project-root", default=".", show_default=True)
@click.option("--operation-id", default=None)
@click.option("--reviewed-sha256", default=None)
@click.option("--backup-path", default=None, type=click.Path(exists=True))
def migration_apply_cmd(
    project_root: str, operation_id: str | None, reviewed_sha256: str | None, backup_path: str | None
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.migration_apply(
                project_root,
                operation_id=operation_id,
                reviewed_sha256=reviewed_sha256,
                backup_path=backup_path,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@migration_group.command(name="status")
@click.option("--project-root", default=".", show_default=True)
@click.option("--operation-id", required=True)
def migration_status_cmd(project_root: str, operation_id: str) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.migration_status(project_root, operation_id=operation_id))
    except MagiciteError as exc:
        _die_magicite(exc)


@migration_group.command(name="resume")
@click.option("--project-root", default=".", show_default=True)
@click.option("--operation-id", required=True)
@click.option("--reviewed-sha256", default=None)
@click.option("--backup-path", default=None, type=click.Path(exists=True))
def migration_resume_cmd(
    project_root: str, operation_id: str, reviewed_sha256: str | None, backup_path: str | None
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.migration_resume(
                project_root,
                operation_id=operation_id,
                reviewed_sha256=reviewed_sha256,
                backup_path=backup_path,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


@migration_group.command(name="restore")
@click.option("--project-root", default=".", show_default=True)
@click.option("--backup-path", required=True, type=click.Path(exists=True))
@click.option("--staging-path", default=None, type=click.Path())
@click.option("--reviewed-sha256", default=None)
def migration_restore_cmd(
    project_root: str, backup_path: str, staging_path: str | None, reviewed_sha256: str | None
) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(
            bind_ops.migration_restore(
                project_root,
                backup_path=backup_path,
                staging_path=staging_path,
                reviewed_sha256=reviewed_sha256,
            )
        )
    except MagiciteError as exc:
        _die_magicite(exc)


# ── S11: evidence ───────────────────────────────────────────────────────


@cli.group(name="evidence")
def evidence_group() -> None:
    """Evidence export, erasure, and retention (C6)."""


@evidence_group.command(name="checkpoint")
@click.option("--project-root", default=".", show_default=True)
@click.option("--decision-event-id", default=None)
@click.option(
    "--route-request",
    type=click.File("r"),
    default=None,
    help="Explicit new route and checkpoint JSON file; use - for stdin.",
)
@click.option("--event-id", required=True, help="Stable idempotency identity for retries.")
@click.option("--outcome", default=None, type=click.Choice(["success", "failure", "unknown"]))
def evidence_checkpoint_cmd(
    project_root: str,
    decision_event_id: str | None,
    route_request: TextIO | None,
    event_id: str,
    outcome: str | None,
) -> None:
    """Explicit new decision checkpoint, or linked operator outcome self-report."""
    from magicite.mcp import bind_ops

    if route_request is not None:
        if decision_event_id is not None or outcome is not None:
            raise click.UsageError("route-request cannot be combined with outcome/decision-event-id")
        _echo_json(
            bind_ops.evidence_route_checkpoint(
                project_root, request=json.load(route_request), event_id=event_id
            )
        )
    else:
        if decision_event_id is None or outcome is None:
            raise click.UsageError("provide route-request, or both decision-event-id and outcome")
        _echo_json(
            bind_ops.evidence_checkpoint(
                project_root, decision_event_id=decision_event_id, event_id=event_id, outcome=outcome
            )
        )


@evidence_group.command(name="export")
@click.option("--project-root", default=".", show_default=True)
@click.option("--export-dir", default=None, type=click.Path())
@click.option("--event-id", "event_ids", multiple=True)
def evidence_export_cmd(project_root: str, export_dir: str | None, event_ids: tuple[str, ...]) -> None:
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
def evidence_delete_cmd(project_root: str, event_id: str, actor: str, reason: str | None) -> None:
    from magicite.mcp import bind_ops

    try:
        _echo_json(bind_ops.evidence_delete(project_root, event_id=event_id, actor=actor, reason=reason))
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


from magicite.core.custody_admin import custody_cli  # noqa: E402

cli.add_command(custody_cli)


if __name__ == "__main__":
    cli()
