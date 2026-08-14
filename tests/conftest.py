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
    """A fresh project root with the toy registry copied into .spectra/engrams/."""
    registry_dir = tmp_path / ".spectra" / "engrams"
    registry_dir.mkdir(parents=True)
    for f in TOY_ENGRAMS_DIR.glob("*.egr.md"):
        shutil.copy(f, registry_dir / f.name)
    return tmp_path


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    (tmp_path / ".spectra" / "engrams").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def cfg(project_root: Path) -> Config:
    return Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})


@pytest.fixture
def embedder() -> Embedder:
    return get_embedder(dim=256)


@pytest.fixture
def db_conn(cfg: Config) -> sqlite3.Connection:
    cfg.ensure_dirs()
    conn = db_mod.connect(cfg.db_path)
    yield conn
    conn.close()
