"""Safe Claude project integration, using existing custody and admission APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import click

from magicite.config import Config

START = "<!-- magicite:init:start -->"
END = "<!-- magicite:init:end -->"
BLOCK = (
    START + "\nUse Magicite's route tool for tasks, then load the selected skill body with\n"
    "load_skill_body before following it. Respect admission, lifecycle and routing refusals.\n" + END
)
MODEL_TIMEOUT = 60.0
MCP_TIMEOUT = 15.0


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    identity: tuple[int, int, int, int]
    mode: int


def snapshot(path: Path) -> Snapshot | None:
    """Read a regular target without following symlinks, including on the open."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise click.ClickException(f"Unsafe target {path.name}; use a regular file.") from exc
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        os.close(fd)
        raise click.ClickException(f"Unsafe target {path.name}; use a regular file.")
    with os.fdopen(fd, "rb") as stream:
        data = stream.read()
        after = os.fstat(stream.fileno())
    identity = (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise click.ClickException(f"Concurrent edit of {path.name}; retry after editing stops.")
    return Snapshot(data, identity, stat.S_IMODE(after.st_mode))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError("nonfinite JSON constant")


def executable() -> Path:
    # Console scripts alongside this interpreter are the actual installed environment.
    candidate = Path(sys.executable).absolute().parent / "magicite"
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise click.ClickException(
            "Install Magicite in this Python environment; its console script is missing."
        )
    return candidate.absolute()


def prepare(root: Path, command: Path) -> dict[Path, tuple[Snapshot | None, bytes]]:
    mcp_path, instructions = root / ".mcp.json", root / "CLAUDE.md"
    old_mcp, old_instructions = snapshot(mcp_path), snapshot(instructions)
    entry = {
        "command": str(command),
        "args": ["serve", "--project-root", str(root)],
        "env": {"MAGICITE_EMBEDDING_OFFLINE": "1"},
    }
    try:
        config = (
            json.loads(old_mcp.data, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
            if old_mcp
            else {}
        )
        if not isinstance(config, dict):
            raise ValueError("object required")
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcpServers object required")
        if "magicite" in servers and servers["magicite"] != entry:
            raise click.ClickException(
                "Conflicting magicite server in .mcp.json; review and remove that entry, then rerun init."
            )
        servers["magicite"] = entry
        mcp_data = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode()
        # Semantically correct existing configurations do not need reformatting/backups.
        if (
            old_mcp
            and json.loads(old_mcp.data, object_pairs_hook=_pairs, parse_constant=_invalid_constant) == config
        ):
            mcp_data = old_mcp.data
        text = old_instructions.data.decode("utf-8") if old_instructions else ""
    except (ValueError, UnicodeError) as exc:
        raise click.ClickException(
            "Invalid .mcp.json or non-UTF-8 CLAUDE.md; repair the file and retry."
        ) from exc
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise click.ClickException("Malformed/duplicate Magicite markers in CLAUDE.md; review them manually.")
    if START in text:
        begin, finish = text.index(START), text.index(END)
        if finish < begin:
            raise click.ClickException("Reversed Magicite markers in CLAUDE.md; review them manually.")
        text = text[:begin] + BLOCK + text[finish + len(END) :]
    else:
        text += ("\n" if text and not text.endswith("\n") else "") + "\n" + BLOCK + "\n"
    return {mcp_path: (old_mcp, mcp_data), instructions: (old_instructions, text.encode())}


def _replace(path: Path, data: bytes, mode: int, expected: Snapshot | None) -> Snapshot:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.magicite-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
            owned = Snapshot(data, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size), mode)
        # Recheck after writing/fsyncing, immediately before replacing the target.
        if snapshot(path) != expected:
            raise click.ClickException(f"Concurrent edit of {path.name}; replacement refused.")
        os.replace(name, path)
        return owned
    finally:
        Path(name).unlink(missing_ok=True)


class ConfigurationTransaction:
    """Own only our two host files; database/cache effects are never rolled back."""

    def __init__(self, changes: dict[Path, tuple[Snapshot | None, bytes]]):
        self.changes = changes
        self.written: dict[Path, Snapshot] = {}
        self.backups: list[Path] = []

    def apply(self) -> None:
        for path, (original, data) in self.changes.items():
            if original is not None and original.data == data:
                continue
            if snapshot(path) != original:
                raise click.ClickException(
                    f"Concurrent edit of {path.name}; configuration was not overwritten."
                )
            if original:
                digest = hashlib.sha256(original.data).hexdigest()
                backup = path.with_name(f"{path.name}.magicite-{digest}.bak")
                try:
                    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                except FileExistsError:
                    saved = snapshot(backup)
                    if saved is None or saved.data != original.data or saved.mode & 0o077:
                        raise click.ClickException(
                            f"Unsafe backup collision for {path.name}; review backups."
                        ) from None
                else:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original.data)
                        stream.flush()
                        os.fsync(stream.fileno())
                self.backups.append(backup)
            if snapshot(path) != original:
                raise click.ClickException(
                    f"Concurrent edit of {path.name}; configuration was not overwritten."
                )
            owned = _replace(path, data, original.mode if original else 0o600, original)
            self.written[path] = owned
            if snapshot(path) != owned:
                raise click.ClickException(
                    f"Concurrent edit of {path.name}; review configuration and backups."
                )

    def rollback(self) -> list[str]:
        manual = []
        for path, owned in reversed(list(self.written.items())):
            original, _ = self.changes[path]
            try:
                if snapshot(path) != owned:
                    manual.append(str(path))
                elif original is None:
                    path.unlink()
                else:
                    _replace(path, original.data, original.mode, owned)
            except (OSError, click.ClickException):
                manual.append(str(path))
        return manual


def probe_model() -> None:
    """Actually construct/load/infer offline, with a killable finite deadline."""
    code = (
        "from magicite.embeddings.fastembed_provider import FastEmbedProvider; "
        "import numpy as np; v=FastEmbedProvider(offline=True).embed('Magicite setup probe'); "
        "assert v.size and np.isfinite(v).all() and np.linalg.norm(v)>0"
    )
    try:
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=MODEL_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException("Offline model probe timed out; inspect the local model cache.") from exc
    if result.returncode:
        raise click.ClickException("Offline model unavailable; run magicite fetch-model, then retry init.")


async def _handshake(argv: list[str], env: dict[str, str], timeout: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        limit=65536,
    )

    async def exchange(method: str, params: dict[str, Any], request_id: int) -> dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()
        frame = json.loads(await proc.stdout.readline())
        if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0" or frame.get("id") != request_id:
            raise ValueError("invalid response")
        result = frame.get("result")
        if "error" in frame or not isinstance(result, dict):
            raise ValueError("unsuccessful response")
        return result

    async def protocol() -> None:
        initialized = await exchange(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "magicite-init", "version": "1"},
            },
            1,
        )
        if initialized.get("protocolVersion") != "2025-11-25" or not isinstance(
            initialized.get("serverInfo"), dict
        ):
            raise ValueError("invalid initialize response")
        assert proc.stdin is not None
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await proc.stdin.drain()
        result = await exchange("tools/list", {}, 2)
        names = {tool["name"] for tool in result["tools"]}
        if not {"route", "load_skill_body", "register"}.issubset(names):
            raise ValueError("required tools absent")

    try:
        await asyncio.wait_for(protocol(), timeout=timeout)
    finally:
        if proc.stdin:
            proc.stdin.close()
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(proc.wait(), timeout=1)
        else:
            await proc.wait()


def verify_connection(command: Path, root: Path) -> None:
    try:
        asyncio.run(
            _handshake(
                [str(command), "serve", "--project-root", str(root)],
                {**os.environ, "MAGICITE_EMBEDDING_OFFLINE": "1"},
                MCP_TIMEOUT,
            )
        )
    except Exception as exc:
        raise click.ClickException("Local MCP connection failed; inspect magicite doctor and retry.") from exc


def inventory(cfg: Config) -> list[dict[str, Any]]:
    from magicite.core.registry import trust_view_for
    from magicite.core.trust_artifacts import require_bound_artifact
    from magicite.storage.db import assert_schema_supported

    if not cfg.db_path.exists():
        return []
    conn = sqlite3.connect(cfg.db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        assert_schema_supported(conn)
        views = []
        for row in conn.execute("SELECT id, path, content_sha256 FROM engram ORDER BY id"):
            view = asdict(trust_view_for(cfg, conn, engram_id=row["id"]))
            try:
                artifact = require_bound_artifact(cfg, cfg.project_root / row["path"])
                current = artifact.id == row["id"] and artifact.content_sha256 == row["content_sha256"]
            except (OSError, ValueError, TypeError):
                current = False
            if not current:
                view["admitted"] = False
                view["origin_trusted"] = False
            views.append(view)
        return views
    finally:
        conn.close()


def onboarding(cfg: Config, skills: Path | None, interactive: bool) -> bool:
    """Return false for partial errors, preserving already committed registry effects."""
    from magicite.core import registry
    from magicite.embeddings import get_embedder
    from magicite.mcp import bind_ops
    from magicite.storage import db

    successful = True
    if not interactive:
        click.echo(
            "Import through MCP register with path='skills', format='auto' (or your selected directory)."
        )
        click.echo("Review: magicite trust review --project-root PROJECT --engram-id ID")
        click.echo(
            "Approve: magicite trust approve --project-root PROJECT --engram-id ID "
            "--expected-digest DIGEST --actor OPERATOR"
        )
        return True
    if skills and click.confirm(f"Import skills from {skills.relative_to(cfg.project_root)}?", default=False):
        cfg.embedding_offline = True
        conn = db.connect(cfg.db_path)
        try:
            outcome = registry.register(
                cfg, conn, get_embedder(cfg), path=str(skills.relative_to(cfg.project_root)), fmt="auto"
            )
        finally:
            conn.close()
        # Untrusted validation text is not echoed as instructions.
        click.echo(
            json.dumps(
                {
                    "ingested": outcome.ingested,
                    "registered": [e.id for e in outcome.registered],
                    "skipped_unchanged": outcome.skipped_unchanged,
                    "validation_errors": [e.path for e in outcome.validation_errors],
                }
            )
        )
        successful = not outcome.validation_errors
    views = inventory(cfg)
    if views and click.confirm("Review current skill digests for explicit approval?", default=False):
        actor = click.prompt("Operator identity", type=str).strip()
        if not actor:
            raise click.ClickException("Explicit operator identity is required; nothing was approved.")
        for view in views:
            # Refresh immediately before displaying the digest; existing approval rejects subsequent changes.
            current = bind_ops.trust_review(cfg.project_root, engram_id=view["engram_id"])
            click.echo(
                json.dumps(
                    {k: current[k] for k in ("engram_id", "content_digest", "admitted", "lifecycle_status")}
                )
            )
            if click.confirm("Approve this exact digest?", default=False):
                try:
                    bind_ops.trust_approve(
                        cfg.project_root,
                        engram_id=current["engram_id"],
                        expected_digest=current["content_digest"],
                        actor=actor,
                    )
                except Exception:
                    click.echo("Approval failed; earlier committed imports/approvals remain.", err=True)
                    successful = False
    return successful


def run_init(project_root: str, *, fetch: bool, skills_path: str | None) -> None:
    raw_root = Path(project_root).absolute()
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise click.ClickException("Project root must be an existing regular directory, not a symlink.")
    root = raw_root.resolve()
    cfg = Config.load(str(root))
    if cfg.embedding_provider != "fastembed":
        raise click.ClickException(
            "init supports the FastEmbed backend; select fastembed and fetch its model."
        )
    command = executable()
    changes = prepare(root, command)  # Validate every host target before any possible side effects.
    selected = Path(skills_path) if skills_path else Path("skills")
    selected = selected if selected.is_absolute() else root / selected
    resolved_skills = selected.resolve()
    if not resolved_skills.is_relative_to(root) or (skills_path and not resolved_skills.is_dir()):
        raise click.ClickException("--skills must be an existing directory contained in this project.")
    skills: Path | None = resolved_skills if resolved_skills.is_dir() else None
    from magicite.core.writer_guard import preflight_custody
    from magicite.obs.doctor import custody_check

    try:
        preflight_custody(cfg)
        custody = custody_check(cfg)
    except Exception as exc:
        raise click.ClickException(
            "Protected custody unavailable; complete administrator Setup and doctor first."
        ) from exc
    if custody["status"] != "ok":
        raise click.ClickException(
            "Protected custody/journal needs reconciliation; complete Setup and doctor first."
        )
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    try:
        probe_model()
    except click.ClickException:
        consent = fetch or (
            interactive
            and click.confirm(
                "Download the embedding model over the network? This does not approve/import skills.",
                default=False,
            )
        )
        if not consent:
            raise
        from magicite.embeddings.fastembed_provider import fetch_model

        try:
            fetch_model()
            probe_model()
        except BaseException as exc:
            raise click.ClickException(
                "Model fetch/probe failed or interrupted; host configuration untouched. "
                "Partial model-cache effects may remain; inspect cache and retry magicite fetch-model."
            ) from exc
    transaction = ConfigurationTransaction(changes)
    try:
        transaction.apply()
        verify_connection(command, root)
    except BaseException:
        manual = transaction.rollback()
        click.echo(
            "Host configuration recovery attempted; registry/database/cache effects are not rolled back.",
            err=True,
        )
        for path in manual:
            click.echo(f"Concurrent edit preserved; manual recovery: {path}", err=True)
        for backup in transaction.backups:
            click.echo(f"Original backup: {backup}", err=True)
        raise
    click.echo("MCP connection verified locally; reconnect Claude Code to load project configuration.")
    try:
        successful = onboarding(cfg, skills, interactive)
        views = inventory(cfg)
    except BaseException as exc:
        raise click.ClickException(
            "Onboarding/inventory failed or interrupted; verified configuration and "
            "committed registry effects remain."
        ) from exc
    counts = {"approved_non_draft": 0, "draft": 0, "quarantined": 0, "unadmitted": 0}
    for view in views:
        counts["draft"] += view["lifecycle_status"] == "draft"
        counts["quarantined"] += bool(view["quarantined"])
        counts["unadmitted"] += not view["admitted"]
        counts["approved_non_draft"] += bool(
            view["admitted"]
            and not view["quarantined"]
            and view["lifecycle_status"] not in {"draft", "archived"}
        )
    click.echo(json.dumps({"connection": "verified", "skills": counts}))
    click.echo(
        "Admission does not promote drafts. Routing still applies lifecycle, policy and context eligibility."
    )
    if not counts["approved_non_draft"]:
        click.echo(
            "Connected; registration, explicit admission or lifecycle work remains before skill retrieval."
        )
    if not successful:
        raise click.ClickException(
            "Partial import/approval failure; prior committed effects and connection remain."
        )
