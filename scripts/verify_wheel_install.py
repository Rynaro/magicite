#!/usr/bin/env python3
"""Clean-environment wheel probe used by S13 CI/release scaffold and S11 hooks.

Installs a built wheel into a temporary venv *outside* the source checkout,
asserts packaged schema/migration resources are present, then runs the offline
hashing-provider toy-registry route fixture.

All subprocesses use the isolated work directory as cwd so dependency side
effects (notably onnxruntime writing ``:memory:.ses`` when telemetry cannot
persist a device id) never land in the repository root.

Subprocess environments are scrubbed (``PYTHONPATH=""``, ``PYTHONNOUSERSITE=1``,
no ``PYTHONHOME`` / ``VIRTUAL_ENV``) so ambient checkout paths cannot leak onto
``sys.path`` and make a broken wheel look installed.

Environment:
  MAGICITE_TEST_WHEEL   optional path to an already-built wheel
  MAGICITE_REPO_ROOT    optional override for the checkout root (default: parents[1])
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOY_ENGRAMS = ROOT / "tests" / "fixtures" / "toy-registry" / "engrams"

# Keys that must not leak from the ambient shell into probe/install children.
_DROP_ENV_KEYS = frozenset(
    {
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "__PYVENV_LAUNCHER__",  # macOS framework launcher can reintroduce the parent venv
    }
)

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
expected_prefix = Path(sys.argv[3]).resolve()
pkg_file = Path(magicite.__file__).resolve()
assert str(pkg_file).startswith(str(expected_prefix)), (
    f"magicite resolved outside probe venv: {pkg_file} (expected under {expected_prefix})"
)

registry_dir = project_root / ".magicite" / "engrams"
registry_dir.mkdir(parents=True)
for path in fixture_engrams.glob("*.egr.md"):
    shutil.copy(path, registry_dir / path.name)

import hashlib

from magicite.core import trust, writer_guard
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal

project_root = project_root.resolve()
cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
cfg.ensure_dirs()
conn = db_mod.connect(cfg.db_path)
embedder = get_embedder(dim=256)

# A clean install has no protected enrollment and must fail closed.
try:
    registry_mod.register(cfg, conn, embedder, path=".magicite/engrams")
except CustodianError as exc:
    assert "protected custody enrollment required" in str(exc), exc
else:
    raise AssertionError("clean install registered without protected custody")


class DisposableCustody:
    def __init__(self, store, registry_id):
        self.store, self.registry_id = store, registry_id

    def call(self, operation, **arguments):
        return getattr(self.store, operation)(self.registry_id, **arguments)


# Disposable in-process custodian for this probe registry only; it does not
# qualify installed-channel or distinct-UID deployment custody.
registry_id = "probe-" + hashlib.sha256(str(project_root).encode()).hexdigest()[:24]
import tempfile

store = CustodianStore.create(Path(tempfile.mkdtemp(prefix="magicite-probe-custody-")).resolve() / "private")
store.enroll(registry_id, trust.default_policy().to_dict(), actor="probe-operator", reviewed=True)
provider = DisposableCustody(store, registry_id)
original_resolve = writer_guard.resolve_custody


def resolve(candidate):
    if candidate.project_root.resolve() == project_root:
        return registry_id, provider
    return original_resolve(candidate)


writer_guard.resolve_custody = resolve
TrustJournal(cfg.data_dir / "trust/authority", registry_id, provider).initialize_reviewed_genesis()

register_outcome = registry_mod.register(cfg, conn, embedder, path=".magicite/engrams")
assert register_outcome.ingested >= 1, register_outcome
assert register_outcome.validation_errors == [], register_outcome.validation_errors
for row in conn.execute("SELECT id, content_sha256 FROM engram").fetchall():
    registry_mod.review_approve(
        cfg,
        conn,
        engram_id=row["id"],
        expected_digest=row["content_sha256"],
        actor="probe-explicit-fixture-review",
        reason="disposable clean-install probe registry; no runtime trust transfer",
    )
route_outcome = router_mod.route(
    cfg, conn, embedder, query="rollback proton for a steam game", k=5
)
assert route_outcome.candidates, "expected at least one routable candidate"
top = route_outcome.candidates[0].name
assert top == "proton-ge-proton-downgrade", top
print(
    json.dumps(
        {
            "ok": True,
            "top": top,
            "ingested": register_outcome.ingested,
            "pkg_file": str(pkg_file),
            "fail_closed_without_custody": True,
            "custody": "disposable-simulated",
            "deployment_custody": "UNEVALUATED",
        }
    )
)
"""


def isolated_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env mapping safe for clean-install probe/install children."""
    env = dict(os.environ if base is None else base)
    for key in _DROP_ENV_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _resolve_wheel(explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"wheel not found: {explicit}")
        return path
    env_wheel = os.environ.get("MAGICITE_TEST_WHEEL")
    if env_wheel:
        path = Path(env_wheel).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"MAGICITE_TEST_WHEEL not found: {path}")
        return path
    dist = ROOT / "dist"
    wheels = sorted(dist.glob("magicite-*.whl")) if dist.is_dir() else []
    if not wheels:
        raise FileNotFoundError("no wheel found; pass --wheel, set MAGICITE_TEST_WHEEL, or build into dist/")
    return wheels[-1]


def run_probe(*, wheel: Path, keep_env: Path | None = None) -> dict:
    wheel = Path(wheel).resolve()
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
    child_env = isolated_subprocess_env()
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        cwd=work,
        env=child_env,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", str(wheel)],
        check=True,
        cwd=work,
        env=child_env,
    )
    completed = subprocess.run(
        [str(python), "-c", PROBE, str(TOY_ENGRAMS), str(project_root), str(venv_dir.resolve())],
        check=True,
        cwd=work,
        capture_output=True,
        text=True,
        env=child_env,
    )
    # Last non-empty stdout line is the probe JSON.
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("wheel probe produced no stdout")
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
