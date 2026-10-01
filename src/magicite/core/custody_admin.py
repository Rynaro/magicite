"""Explicit operator custody maintenance. Never provisions OS accounts or /etc."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import click
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from magicite.config import Config
from magicite.core.trust_custodian import CustodianError, CustodianStore, _bytes
from magicite.core.trust_custodian_transport import CustodianService, CustodyProfile, protected_path


class CustodyCommands(click.Group):
    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except (CustodianError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
            raise click.ClickException(
                "reconciliation_required: verify protected custody and reviewed inputs"
            ) from exc


@contextmanager
def _store(directory: Path) -> Iterator[CustodianStore]:
    protected_path(directory, os.getuid(), directory=True)
    for name in ("journal.key", "signing.key", "authority.sqlite"):
        protected_path(directory / name, os.getuid())
    store = CustodianStore.open(directory)
    try:
        yield store
    finally:
        store.close()


def _emit(value: object) -> None:
    click.echo(json.dumps(value, sort_keys=True, indent=2))


@click.group(name="custody", cls=CustodyCommands)
def custody_cli() -> None:
    """Maintain independently provisioned trust custody (no MCP tools)."""


@custody_cli.command(name="init")
@click.option("--directory", required=True, type=click.Path(path_type=Path))
def initialize_store(directory: Path) -> None:
    """Create private keys/state as the pre-provisioned custodian OS account."""
    protected_path(directory.parent, os.getuid(), directory=True)
    store = CustodianStore.create(directory)
    store.close()
    _emit({"state": "CREATED", "registries_enrolled": 0})


@custody_cli.command(name="enroll")
@click.option("--directory", required=True, type=click.Path(path_type=Path))
@click.option("--registry-id", required=True)
@click.option("--policy", required=True, type=click.Path(path_type=Path, exists=True))
@click.option("--actor", required=True)
@click.option("--reviewed-sha256", required=True)
def enroll(directory: Path, registry_id: str, policy: Path, actor: str, reviewed_sha256: str) -> None:
    """Explicitly enroll reviewed policy bytes; never resets existing identity."""
    raw = policy.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reviewed_sha256:
        raise CustodianError("reviewed policy changed")
    value = json.loads(raw)
    with _store(directory) as store:
        store.enroll(registry_id, value, actor=actor, reviewed=True)
        _emit(store.read_current(registry_id))


@custody_cli.command(name="profile")
@click.option("--directory", required=True, type=click.Path(path_type=Path))
@click.option("--registry-id", required=True)
@click.option("--project-root", required=True, type=click.Path(path_type=Path))
@click.option("--client-uid", required=True, type=int)
@click.option("--socket-path", required=True, type=click.Path(path_type=Path))
@click.option("--profile-path", required=True, type=click.Path(path_type=Path))
def create_profile(
    directory: Path,
    registry_id: str,
    project_root: Path,
    client_uid: int,
    socket_path: Path,
    profile_path: Path,
) -> None:
    """Create protected profile; print descriptor for explicit root installation.

    Install stdout at /etc/magicite/registries/<SHA256(canonical project root)>.json
    with root-owned, non-writable ancestry. This command never writes that path.
    """
    protected_path(profile_path.parent, os.getuid(), directory=True)
    protected_path(socket_path.parent, os.getuid(), directory=True)
    with _store(directory) as store:
        head = store.read_current(registry_id)
        public = store.signer_for(registry_id).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        profile = CustodyProfile(registry_id, head["epoch"], socket_path, os.getuid(), client_uid, public)
        value = {
            "state": "ACTIVE",
            "registry_id": registry_id,
            "epoch": profile.epoch,
            "minimum_epoch": profile.epoch,
            "custodian_uid": profile.custodian_uid,
            "client_uid": client_uid,
            "public_key": public,
            "socket_path": str(socket_path),
        }
        # Protected parent checked above; exclusive creation prevents overwrite,
        # symlink following and accidental reset of pending rotation state.
        fd = os.open(profile_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        parent = os.open(profile_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    _emit(
        {
            "schema": "CustodyEnrollment/1",
            "project_root": str(project_root.resolve()),
            "registry_id": registry_id,
            "custodian_uid": os.getuid(),
            "client_uid": client_uid,
            "profile_path": str(profile_path),
        }
    )


@custody_cli.command(name="serve")
@click.option("--directory", required=True, type=click.Path(path_type=Path))
@click.option("--profile-path", required=True, type=click.Path(path_type=Path))
def serve_custody(directory: Path, profile_path: Path) -> None:
    """Run the bounded Unix-socket service as the custodian account."""
    profile = CustodyProfile.load(profile_path, expected_owner_uid=os.getuid(), maintenance=True)
    with _store(directory) as store:
        try:
            head = store.read_current(profile.registry_id)
            if head["epoch"] != profile.epoch:
                raise CustodianError("profile epoch does not match authority")
        except CustodianError:
            status = store.rotation_status(profile.registry_id)
            body = status["transition"]["body"]
            if (profile.epoch, profile.public_key) not in {
                (body["old_epoch"], body["old_public_key"]),
                (body["new_epoch"], body["new_public_key"]),
            }:
                raise CustodianError("profile rotation pin mismatch") from None
        CustodianService(store, profile).serve()


@custody_cli.command(name="status")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
def status(project_root: Path) -> None:
    """Read fresh authenticated status without creating local state."""
    from magicite.core.writer_guard import resolve_custody

    _, client = resolve_custody(Config.load(project_root))
    _emit(client.call("read_current"))


@custody_cli.command(name="initialize-journal")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
def initialize_journal(project_root: Path) -> None:
    """Initialize only an explicitly enrolled genesis; not legacy migration."""
    from magicite.core.writer_guard import bound_journal, registry_writer_lease, resolve_custody
    from magicite.storage import db

    cfg = Config.load(project_root)
    _, client = resolve_custody(cfg)
    client.call("read_current")  # authenticated liveness BEFORE any local creation
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    try:
        with registry_writer_lease(cfg, conn).acquire():
            journal, held, _ = bound_journal(cfg)
            journal.initialize_reviewed_genesis(assert_owned=held.assert_owned)
            held.assert_owned()
            _emit({"state": "INITIALIZED"})
    finally:
        conn.close()


@custody_cli.command(name="reconcile")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
def reconcile(project_root: Path) -> None:
    """Recover only exact retained authenticated history under a fresh fence."""
    from magicite.core.writer_guard import bound_journal, registry_writer_lease, resolve_custody
    from magicite.storage import db

    cfg = Config.load(project_root)
    _, client = resolve_custody(cfg)
    client.call("read_current")
    conn = db.connect(cfg.db_path)
    try:
        with registry_writer_lease(cfg, conn).acquire():
            journal, held, fence = bound_journal(cfg)
            snapshot = journal.reconcile(fence=fence, assert_owned=held.assert_owned)
            _emit(snapshot.head)
    finally:
        conn.close()


@custody_cli.command(name="rotate")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
@click.option("--transition-id", required=True)
@click.option("--actor", required=True)
def rotate(project_root: Path, transition_id: str, actor: str) -> None:
    """Rotate or resume the same explicit key epoch under the registry lease."""
    from magicite.core.trust_custodian_transport import CustodianClient
    from magicite.core.trust_rotation_client import rotate_registry
    from magicite.core.writer_guard import protected_profile_path
    from magicite.storage import db

    cfg = Config.load(project_root)
    profile = CustodyProfile.from_enrollment(
        protected_profile_path(cfg), project_root=cfg.project_root, maintenance=True
    )
    client = CustodianClient(profile, maintenance=True)
    try:
        client.call("rotation_status", transition_id=transition_id)
    except CustodianError:
        client.call("read_current")  # must authenticate BEFORE touching local DB
    if not cfg.db_path.is_file():
        raise CustodianError("rotation requires an existing initialized registry")
    conn = db.connect(cfg.db_path)
    try:
        _emit(
            rotate_registry(
                cfg, conn, client, registry_id=profile.registry_id, transition_id=transition_id, actor=actor
            )
        )
    finally:
        conn.close()


@custody_cli.command(name="legacy-preview")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
@click.option("--registry-id", required=True)
@click.option("--actor", required=True)
def legacy_preview(project_root: Path, registry_id: str, actor: str) -> None:
    """Print zero-write unsigned legacy inventory for explicit operator review."""
    from magicite.core import trust_legacy

    plan = trust_legacy.preview(Config.load(project_root), registry_id=registry_id, actor=actor)
    _emit({"reviewed_sha256": trust_legacy.digest(plan), "plan": plan})


@custody_cli.command(name="legacy-backup")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
@click.option("--plan", "plan_path", required=True, type=click.Path(path_type=Path, exists=True))
@click.option("--reviewed-sha256", required=True)
@click.option("--destination", required=True, type=click.Path(path_type=Path))
@click.option("--encrypted-custody-reference", type=click.Path(path_type=Path, exists=True))
def legacy_backup(
    project_root: Path,
    plan_path: Path,
    reviewed_sha256: str,
    destination: Path,
    encrypted_custody_reference: Path | None,
) -> None:
    """Back up exact reviewed legacy state before any migration changes."""
    from magicite.core import trust_legacy

    plan = json.loads(plan_path.read_bytes())
    reference = json.loads(encrypted_custody_reference.read_bytes()) if encrypted_custody_reference else None
    _emit(
        trust_legacy.backup_reviewed(
            Config.load(project_root),
            plan=plan,
            reviewed_sha256=reviewed_sha256,
            destination=destination,
            encrypted_custody=reference,
        )
    )


@custody_cli.command(name="legacy-apply")
@click.option("--project-root", default=".", type=click.Path(path_type=Path))
@click.option("--backup", "backup_path", required=True, type=click.Path(path_type=Path, exists=True))
@click.option("--reviewed-sha256", required=True)
def legacy_apply(project_root: Path, backup_path: Path, reviewed_sha256: str) -> None:
    """Apply or resume an exact reviewed backup; every target still needs review."""
    from magicite.core import trust_legacy

    _emit(
        trust_legacy.apply_reviewed(
            Config.load(project_root), backup_path=backup_path, reviewed_sha256=reviewed_sha256
        )
    )
