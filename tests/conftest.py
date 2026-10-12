"""Shared fixtures: tmp registry factory, frozen clock, hashing embedder.

Every test in this repo runs against ``MAGICITE_EMBEDDING_PROVIDER=hashing``
(CR-6) -- no model download, fully deterministic.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.embeddings.base import Embedder
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db as db_mod

TOY_REGISTRY_DIR = Path(__file__).parent / "fixtures" / "toy-registry"
TOY_ENGRAMS_DIR = TOY_REGISTRY_DIR / "engrams"
TOY_SKILLS_DIR = TOY_REGISTRY_DIR / "skills"
TOY_QUERIES_PATH = TOY_REGISTRY_DIR / "queries.jsonl"

TOY_ENGRAM_NAMES = sorted(p.stem.removesuffix(".egr") for p in TOY_ENGRAMS_DIR.glob("*.egr.md"))


@pytest.fixture
def toy_registry_dir() -> Path:
    return TOY_REGISTRY_DIR


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A fresh project root with the toy registry copied into .magicite/engrams/."""
    registry_dir = tmp_path / ".magicite" / "engrams"
    registry_dir.mkdir(parents=True)
    for f in TOY_ENGRAMS_DIR.glob("*.egr.md"):
        shutil.copy(f, registry_dir / f.name)
    return tmp_path


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    (tmp_path / ".magicite" / "engrams").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def cfg(project_root: Path, monkeypatch, tmp_path_factory):
    from tests.support.custody_adapter import enroll_fixture

    config = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    provider = enroll_fixture(config, monkeypatch, tmp_path_factory.mktemp("fixture-custody") / "private")
    yield config
    provider.close()


@pytest.fixture
def review_fixture_artifacts(cfg, db_conn):
    from tests.support.custody_adapter import review_sources

    def review(*names: str):
        if not names:
            raise ValueError("select fixture names explicitly")
        return review_sources(
            cfg,
            db_conn,
            sources={name: (TOY_ENGRAMS_DIR / (name + ".egr.md")).read_bytes() for name in names},
        )

    return review


@pytest.fixture
def embedder() -> Embedder:
    return get_embedder(dim=256)


@pytest.fixture
def db_conn(cfg: Config) -> sqlite3.Connection:
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    yield conn
    conn.close()


@pytest.fixture
def custody_for(monkeypatch, tmp_path_factory):
    """Explicit enrollment factory for tests owning independent Config instances."""
    from tests.support.custody_adapter import enroll_fixture

    providers = {}

    def enroll(config):
        root = config.project_root.resolve()
        if root not in providers:
            providers[root] = enroll_fixture(
                config, monkeypatch, tmp_path_factory.mktemp("explicit-custody") / "private"
            )
        return providers[root]

    yield enroll
    for provider in providers.values():
        provider.close()


@pytest.fixture
def legacy_migration_case(monkeypatch, tmp_path_factory):
    """Explicit unsigned legacy tree plus independent simulated enrollment; no grant."""
    import json

    from magicite.core import trust, trust_legacy, writer_guard
    from tests.support.custody_adapter import FixtureCustody

    providers = []

    def prepare(root: Path, *, create_backup: bool = True):
        config = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        config.ensure_dirs()
        for source in TOY_ENGRAMS_DIR.glob("*.egr.md"):
            shutil.copy(source, config.registry_dir / source.name)
        conn = db_mod.connect(config.db_path)
        conn.close()
        (config.data_dir / "trust").mkdir(exist_ok=True)
        (config.data_dir / "trust/policy.json").write_text(json.dumps(trust.default_policy().to_dict()))
        external = tmp_path_factory.mktemp("legacy-case-custody")
        provider = FixtureCustody(external / "private", "legacy-fixture")
        providers.append(provider)
        previous = writer_guard.resolve_custody

        def resolve(candidate):
            if candidate.project_root.resolve() == config.project_root.resolve():
                return provider.registry_id, provider
            return previous(candidate)

        monkeypatch.setattr(writer_guard, "resolve_custody", resolve)
        plan = trust_legacy.preview(config, registry_id=provider.registry_id, actor="test-operator")
        backup = external / "backup"
        if create_backup:
            trust_legacy.backup_reviewed(
                config, plan=plan, reviewed_sha256=trust_legacy.digest(plan), destination=backup
            )
        return config, plan, backup, provider

    yield prepare
    for provider in providers:
        provider.close()
