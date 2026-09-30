"""Explicit operator custody maintenance. Never provisions OS accounts or /etc."""

from __future__ import annotations

import hashlib
import json
import os
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
        except (CustodianError, OSError, ValueError, KeyError) as exc:
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
        public = store.signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
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
    profile = CustodyProfile.load(profile_path, expected_owner_uid=os.getuid())
    with _store(directory) as store:
        if store.read_current(profile.registry_id)["epoch"] != profile.epoch:
            raise CustodianError("profile epoch does not match authority")
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
    resolve_custody(cfg)  # prerequisites BEFORE any local creation
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    try:
        with registry_writer_lease(cfg, conn).acquire():
            journal, held, _ = bound_journal(cfg)
            journal.initialize_reviewed_genesis()
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
    resolve_custody(cfg)
    conn = db.connect(cfg.db_path)
    try:
        with registry_writer_lease(cfg, conn).acquire():
            journal, held, fence = bound_journal(cfg)
            snapshot = journal.reconcile(fence=fence, assert_owned=held.assert_owned)
            _emit(snapshot.head)
    finally:
        conn.close()
