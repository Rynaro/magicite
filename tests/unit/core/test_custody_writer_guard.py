"""Existing local lease and independent generation must be acquired in order."""

from __future__ import annotations

import pytest

from magicite.config import Config
from magicite.core.trust_custodian import CustodianError
from magicite.errors import BusyError
from magicite.storage import db, lease


class Coordinator:
    registry_id = "r"

    def __init__(self, cfg, connection, fail=False):
        self.cfg, self.connection, self.fail = cfg, connection, fail
        self.events = []

    def capture(self):
        assert self.connection.execute("SELECT COUNT(*) FROM writer_lease").fetchone()[0] == 0
        self.events.append("capture")

    def register(self, holder, local_token):
        assert self.connection.execute("SELECT COUNT(*) FROM writer_lease").fetchone()[0] == 1
        self.events.append("register")
        if self.fail:
            raise CustodianError("stale predecessor")


def test_custody_predecessor_capture_precedes_local_lease_and_nested_reuses(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    coordinator = Coordinator(cfg, connection)
    try:
        outer = lease.CrossProcessLease(lock_path=cfg.dream_lock_path, conn=connection, custody=coordinator)
        with outer.acquire():
            nested = Coordinator(cfg, connection)
            with lease.CrossProcessLease(
                lock_path=cfg.dream_lock_path, conn=connection, custody=nested
            ).acquire():
                assert nested.events == []
        assert coordinator.events == ["capture", "register"]
    finally:
        connection.close()


def test_failed_custody_registration_releases_existing_local_lease(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    coordinator = Coordinator(cfg, connection, fail=True)
    try:
        with pytest.raises(CustodianError):
            with lease.CrossProcessLease(
                lock_path=cfg.dream_lock_path, conn=connection, custody=coordinator
            ).acquire():
                pytest.fail("cannot enter mutation scope")
        assert connection.execute("SELECT COUNT(*) FROM writer_lease").fetchone()[0] == 0
        assert not lease.cross_process_lease_held()
        with lease.CrossProcessLease(lock_path=cfg.dream_lock_path, conn=connection).acquire():
            pass
    finally:
        connection.close()


def test_nested_custody_cannot_attach_to_already_acquired_bare_lease(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    try:
        with lease.CrossProcessLease(lock_path=cfg.dream_lock_path, conn=connection).acquire():
            with pytest.raises(BusyError, match="custody"):
                with lease.CrossProcessLease(
                    lock_path=cfg.dream_lock_path, conn=connection, custody=Coordinator(cfg, connection)
                ).acquire():
                    pytest.fail("cannot lazily acquire custody")
    finally:
        connection.close()


def test_real_factory_requires_protected_enrollment_before_any_acquisition(tmp_path):
    from magicite.core.writer_guard import registry_writer_lease

    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    try:
        with pytest.raises(CustodianError):
            registry_writer_lease(cfg, connection)
        assert not cfg.dream_lock_path.exists()
        assert connection.execute("SELECT COUNT(*) FROM writer_lease").fetchone()[0] == 0
    finally:
        connection.close()


def test_explicit_factory_adapter_binds_real_custodian_generation(tmp_path, monkeypatch):
    from magicite.core import writer_guard
    from magicite.core.trust import default_policy
    from magicite.core.trust_custodian import CustodianStore

    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)

    class Adapter:
        def call(self, operation, **arguments):
            return getattr(store, operation)("r", **arguments)

    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", Adapter()))
    try:
        with writer_guard.registry_writer_lease(cfg, connection).acquire():
            _, outer, first = writer_guard.bound_journal(cfg)
            with writer_guard.registry_writer_lease(cfg, connection).acquire():
                _, nested, second = writer_guard.bound_journal(cfg)
                assert nested is outer
                assert second == first
                assert store.read_current("r")["fence_generation"] == 1
    finally:
        store.close()
        connection.close()
