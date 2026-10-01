"""Simulated custody and explicit allowlisted review for mechanism tests.

This bypasses OS isolation only by test-owned dependency injection. It does
not qualify an installed channel or distinct-UID deployment. Enrollment
never grants artifact admission; each positive scenario requests review.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

from magicite.core import registry, trust, writer_guard
from magicite.core.trust_custodian import CustodianStore
from magicite.core.trust_journal import TrustJournal


class FixtureCustody:
    def __init__(self, directory: Path, registry_id: str):
        self.store = CustodianStore.create(directory)
        self.registry_id = registry_id
        self.store.enroll(registry_id, trust.default_policy().to_dict(), actor="test-operator", reviewed=True)

    def call(self, operation, **arguments):
        return getattr(self.store, operation)(self.registry_id, **arguments)

    def close(self):
        self.store.close()


def enroll_fixture(cfg, monkeypatch, directory: Path):
    root = cfg.project_root.resolve()
    identity = "test-" + hashlib.sha256(str(root).encode()).hexdigest()[:24]
    provider = FixtureCustody(directory, identity)
    original = writer_guard.resolve_custody

    def resolve(candidate):
        if candidate.project_root.resolve() == root:
            return identity, provider
        return original(candidate)

    monkeypatch.setattr(writer_guard, "resolve_custody", resolve)
    TrustJournal(cfg.data_dir / "trust/authority", identity, provider).initialize_reviewed_genesis()
    return provider


def review_sources(cfg, conn, *, sources: dict[str, bytes]):
    """Review exact source identities explicitly selected by the calling test."""
    from magicite.core import trust_artifacts
    from magicite.engram import parser

    if not sources:
        raise ValueError("an explicit source allowlist is required")
    snapshot = trust.authenticated_snapshot(cfg)
    receipts = []
    for name, raw in sources.items():
        source, _ = parser.parse_artifact(raw.decode(), relpath=name + ".egr.md", admit=True)
        if source.name != name:
            raise ValueError("fixture source name mismatch")
        row = conn.execute("SELECT path FROM engram WHERE id=?", (source.id,)).fetchone()
        if row is None:
            raise ValueError("fixture must be registered before explicit review")
        artifact = trust_artifacts.require_bound_artifact(cfg, cfg.project_root / row["path"])
        source_digest = hashlib.sha256(raw).hexdigest()
        if not any(
            record["kind"] == "artifact_transform"
            and record["payload"]["engram_id"] == source.id
            and record["payload"]["source_digest"] == source_digest
            and record["payload"]["target_digest"] == artifact.content_sha256
            for record in snapshot.records
        ):
            raise ValueError("live target is not the allowlisted source transformation")
        receipts.append(
            registry.review_approve(
                cfg,
                conn,
                engram_id=source.id,
                expected_digest=artifact.content_sha256,
                actor="test-fixture-review",
            )
        )
    return receipts


def review_inserted_source(cfg, conn, *, path: Path, source: bytes) -> str:
    """Bind explicit synthetic SQL fixtures without altering their routing weights."""
    from magicite.core import trust_artifacts
    from magicite.core.trust_journal import _directory_fd, _replace_file

    held = writer_guard.registry_writer_lease(cfg, conn)
    with held.acquire():
        journal, _, _ = writer_guard.bound_journal(cfg)
        marked = trust_artifacts.mark_artifact(
            source, registry_id=journal.registry_id, relpath=str(path), actor="test-fixture-author"
        )
        trust_artifacts.bind_prepared_transform(cfg, marked)
        with _directory_fd(path.parent) as directory:
            _replace_file(directory, path.name, marked.target, held.assert_owned)
        artifact = trust_artifacts.require_bound_artifact(cfg, path)
        cursor = conn.execute(
            "UPDATE engram SET content_sha256=?, body_sha256=?, spec_version='engram/1.0' WHERE id=?",
            (artifact.content_sha256, artifact.body_sha256, artifact.id),
        )
        if cursor.rowcount != 1:
            raise ValueError("explicit SQL fixture row is required")
    review_sources(cfg, conn, sources={artifact.name: source})
    return artifact.content_sha256


def review_toy_sources(cfg, conn, *, names: list[str]):
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures/toy-registry/engrams"
    return review_sources(
        cfg, conn, sources={name: (fixture_root / (name + ".egr.md")).read_bytes() for name in names}
    )


def publish_generated_source(cfg, *, path: Path, source: bytes) -> str:
    """Publish a caller-supplied synthetic source; deliberately leave it unadmitted."""
    from magicite.core import trust_artifacts
    from magicite.core.trust_journal import _directory_fd, _replace_file
    from magicite.storage import db

    conn = db.connect(cfg.db_path)
    try:
        held = writer_guard.registry_writer_lease(cfg, conn)
        with held.acquire():
            journal, _, _ = writer_guard.bound_journal(cfg)
            marked = trust_artifacts.mark_artifact(
                source, registry_id=journal.registry_id, relpath=str(path), actor="test-fixture-author"
            )
            trust_artifacts.bind_prepared_transform(cfg, marked)
            with _directory_fd(path.parent) as directory:
                _replace_file(directory, path.name, marked.target, held.assert_owned)
        return hashlib.sha256(marked.target).hexdigest()
    finally:
        conn.close()


@contextmanager
def attach_fixture(root: Path, directory: Path, registry_id: str):
    """Attach an existing authority in this process; never enroll or create history."""
    provider = object.__new__(FixtureCustody)
    provider.store = CustodianStore.open(directory)
    provider.registry_id = registry_id
    original = writer_guard.resolve_custody

    def resolve(candidate):
        if candidate.project_root.resolve() == root.resolve():
            return registry_id, provider
        return original(candidate)

    writer_guard.resolve_custody = resolve
    try:
        provider.call("read_current")
        yield provider
    finally:
        writer_guard.resolve_custody = original
        provider.close()


def threaded_calls(provider, monkeypatch):
    """Use a fresh existing-store connection per call for explicit threaded tests."""
    directory, registry_id = provider.store.directory, provider.registry_id

    def call(operation, **arguments):
        store = CustodianStore.open(directory)
        try:
            return getattr(store, operation)(registry_id, **arguments)
        finally:
            store.close()

    monkeypatch.setattr(provider, "call", call)


def clone_fixture_timeline(provider, destination: Path):
    """Copy simulated initial authority for independent crash/baseline timelines only."""
    import os
    import sqlite3

    destination.mkdir(mode=0o700)
    for name in ("journal.key", "signing.key"):
        (destination / name).write_bytes((provider.store.directory / name).read_bytes())
        os.chmod(destination / name, 0o600)
    target = sqlite3.connect(destination / "authority.sqlite")
    try:
        provider.store._conn.backup(target)
    finally:
        target.close()
    os.chmod(destination / "authority.sqlite", 0o600)
    return destination
