#!/usr/bin/env python3
"""Clean-environment wheel probe used by S13 CI/release scaffold and S11 hooks.

Installs a built wheel into a temporary venv *outside* the source checkout,
asserts packaged schema/migration resources are present, then runs the offline
hashing-provider toy-registry route fixture.

All subprocesses use the isolated work directory as cwd so dependency side
effects (notably onnxruntime writing ``:memory:.ses`` when telemetry cannot
persist a device id) never land in the repository root.

Environment:
  MAGICITE_TEST_WHEEL   optional path to an already-built wheel
  MAGICITE_REPO_ROOT    optional override for the checkout root (default: parents[1])
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOY_ENGRAMS = ROOT / "tests" / "fixtures" / "toy-registry" / "engrams"

PROBE = r"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import magicite
from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.core import router as router_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db as db_mod

pkg = Path(magicite.__file__).resolve().parent
schema = pkg / "engram" / "schema" / "engram-0.2.schema.json"
migration = pkg / "storage" / "migrations" / "001_init.sql"
assert schema.is_file(), f"missing packaged schema: {schema}"
assert migration.is_file(), f"missing packaged migration: {migration}"

fixture_engrams = Path(sys.argv[1])
project_root = Path(sys.argv[2])
registry_dir = project_root / ".magicite" / "engrams"
registry_dir.mkdir(parents=True)
for path in fixture_engrams.glob("*.egr.md"):
    shutil.copy(path, registry_dir / path.name)

cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
cfg.ensure_dirs()
conn = db_mod.connect(cfg.db_path)
embedder = get_embedder(dim=256)
register_outcome = registry_mod.register(cfg, conn, embedder, path=".magicite/engrams")
assert register_outcome.ingested >= 1, register_outcome
route_outcome = router_mod.route(
    cfg, conn, embedder, query="rollback proton for a steam game", k=5
)
assert route_outcome.candidates, "expected at least one routable candidate"
top = route_outcome.candidates[0].name
assert top == "proton-ge-proton-downgrade", top
print(json.dumps({"ok": True, "top": top, "ingested": register_outcome.ingested}))
"""


def _resolve_wheel(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"wheel not found: {explicit}")
        return explicit
    env_wheel = os.environ.get("MAGICITE_TEST_WHEEL")
    if env_wheel:
        path = Path(env_wheel)
        if not path.is_file():
            raise FileNotFoundError(f"MAGICITE_TEST_WHEEL not found: {path}")
        return path
    dist = ROOT / "dist"
    wheels = sorted(dist.glob("magicite-*.whl")) if dist.is_dir() else []
    if not wheels:
        raise FileNotFoundError("no wheel found; pass --wheel, set MAGICITE_TEST_WHEEL, or build into dist/")
    return wheels[-1]


def run_probe(*, wheel: Path, keep_env: Path | None = None) -> dict:
    if not TOY_ENGRAMS.is_dir():
        raise FileNotFoundError(f"toy registry fixtures missing: {TOY_ENGRAMS}")

    work = Path(keep_env) if keep_env is not None else Path(tempfile.mkdtemp(prefix="magicite-wheel-"))
    work.mkdir(parents=True, exist_ok=True)
    venv_dir = work / "venv"
    project_root = work / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv.create(venv_dir, with_pip=True, clear=True)
    python = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        cwd=work,
    )
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True, cwd=work)
    completed = subprocess.run(
        [str(python), "-c", PROBE, str(TOY_ENGRAMS), str(project_root)],
        check=True,
        cwd=work,
        capture_output=True,
        text=True,
    )
    # Last non-empty stdout line is the probe JSON.
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("wheel probe produced no stdout")
    import json

    return json.loads(lines[-1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=None)
    parser.add_argument(
        "--keep-env",
        type=Path,
        default=None,
        help="Optional directory to retain the clean venv (default: ephemeral tempdir)",
    )
    args = parser.parse_args(argv)
    wheel = _resolve_wheel(args.wheel)
    result = run_probe(wheel=wheel, keep_env=args.keep_env)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
