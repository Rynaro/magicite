"""``magicite`` click CLI: serve|sync|dream|export|tools|doctor|fetch-model.

M0 implements ``serve``, ``sync`` and ``tools`` for real. The remaining
subcommands exist (the CLI surface is named in spec §1) but raise a clear
``ClickException`` naming the milestone that implements them, rather than
silently doing nothing -- the CLI-level equivalent of the MCP tools'
typed ``not_implemented`` error (INV-4).
"""

from __future__ import annotations

import asyncio
import json

import click

from magicite.config import Config


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
def dream_cmd(once: bool, autonomous: bool) -> None:
    """Run the Dream consolidation worker inline."""
    raise click.ClickException("magicite dream lands in M4 (storage/lease.py, core/dream.py)")


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
    """Diagnose the registry/filesystem/embedding-provider setup."""
    raise click.ClickException("magicite doctor lands in M7 (docs/operations.md, packaging hardening)")


@cli.command(name="fetch-model")
@click.option("--model-name", default=None, help="Override the default fastembed model name.")
def fetch_model_cmd(model_name: str | None) -> None:
    """Pre-download the default ONNX embedding model for offline use (R4:
    the one legitimate network-touching step -- run this before setting
    MAGICITE_EMBEDDING_OFFLINE=1 / baking a hardened image)."""
    from magicite.embeddings.fastembed_provider import DEFAULT_MODEL_NAME, fetch_model

    resolved = model_name or DEFAULT_MODEL_NAME
    click.echo(f"fetching {resolved!r} ...")
    fetch_model(model_name=resolved)
    click.echo(json.dumps({"fetched": resolved}, indent=2))


if __name__ == "__main__":
    cli()
