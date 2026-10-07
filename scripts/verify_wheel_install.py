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
import hashlib
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
import tomllib
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
import hashlib, importlib.metadata, json, shutil, sys, platform
from pathlib import Path
import magicite
from magicite.config import Config
from magicite.core import registry, router, trust, writer_guard
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db

fixture, project, prefix, expected_file, mode, origins_file = map(Path, sys.argv[1:7])
mode = str(mode)
expected = json.loads(expected_file.read_text())
pkg_file = Path(magicite.__file__).resolve()
prefix = prefix.resolve()
assert pkg_file.is_relative_to(prefix), "installed-package-origin-outside-environment"
pkg = pkg_file.parent
for relative, digest in expected['package_files'].items():
    file = pkg / relative
    assert file.is_file(), 'missing-installed-resource:' + relative
    assert hashlib.sha256(file.read_bytes()).hexdigest() == digest, 'installed-resource-digest:' + relative
metadata = importlib.metadata.distribution('magicite')
assert metadata.version == expected['version'], 'installed-version-mismatch'
assert metadata.metadata['Requires-Python'] == expected['requires_python'], 'python-metadata-mismatch'
entrypoints = {ep.name: ep.value for ep in metadata.entry_points if ep.group == 'console_scripts'}
assert entrypoints == expected['entrypoints'], 'installed-entrypoints-mismatch'
url = json.loads(metadata.read_text('direct_url.json'))
assert url.get('url', '').startswith('file:'), 'artifact-install-must-be-local-explicit'
assert url.get('archive_info', {}).get('hash') == ('sha256=' + expected['artifact_sha256']), (
    'installed-artifact-digest-mismatch')

def origins():
    result = {}
    for name, module in list(sys.modules.items()):
        if name == 'magicite' or name.startswith('magicite.'):
            file = getattr(module, '__file__', None)
            assert file and Path(file).resolve().is_relative_to(prefix), 'exercised-module-origin:' + name
            result[name] = str(Path(file).resolve().relative_to(prefix))
    return result

project = project.resolve()
cfg = Config.load(project, env={'MAGICITE_EMBEDDING_PROVIDER': 'hashing'})
cfg.ensure_dirs()
conn = db.connect(cfg.db_path)
embedder = get_embedder(dim=256)
if mode != 'serve':
    target = project / '.magicite/engrams'
    target.mkdir(parents=True, exist_ok=True)
    for file in fixture.glob('*.egr.md'):
        shutil.copyfile(file, target / file.name)
    try:
        registry.register(cfg, conn, embedder, path='.magicite/engrams')
    except CustodianError as exc:
        custody_error = {'class': type(exc).__name__, 'message': str(exc)}
        assert 'protected custody enrollment required' in str(exc), 'unexpected-custody-error'
    else:
        raise AssertionError('missing-custody-did-not-fail-closed')

registry_id = 'probe-' + hashlib.sha256(str(project).encode()).hexdigest()[:24]
custody = project.parent / 'disposable-custody'
store = CustodianStore.open(custody) if custody.exists() else CustodianStore.create(custody)
if mode != 'serve':
    store.enroll(registry_id, trust.default_policy().to_dict(), actor='probe-operator', reviewed=True)
class DisposableCustody:
    def call(self, operation, **arguments):
        return getattr(store, operation)(registry_id, **arguments)
provider = DisposableCustody()
original_resolve = writer_guard.resolve_custody
def resolve(candidate):
    if candidate.project_root.resolve() == project:
        return registry_id, provider
    return original_resolve(candidate)
writer_guard.resolve_custody = resolve
if mode == 'serve':
    conn.close()
    from magicite.__main__ import cli
    try:
        cli.main(args=['serve', '--project-root', str(project)], prog_name='magicite')
    finally:
        Path(origins_file).write_text(json.dumps(origins()))
        store.close()
    raise SystemExit(0)
TrustJournal(cfg.data_dir / 'trust/authority', registry_id, provider).initialize_reviewed_genesis()
outcome = registry.register(cfg, conn, embedder, path='.magicite/engrams')
assert outcome.ingested > 0 and not outcome.validation_errors, 'fixture-registration-failed'
for row in conn.execute('SELECT id,content_sha256 FROM engram').fetchall():
    registry.review_approve(cfg, conn, engram_id=row['id'],
                            expected_digest=row['content_sha256'], actor='probe-fixture-review')
route = router.route(cfg, conn, embedder, query='rollback proton for a steam game', k=5)
assert route.candidates and route.candidates[0].name == 'proton-ge-proton-downgrade', 'fixture-route-mismatch'

from magicite.embeddings.fastembed_provider import FastEmbedProvider, FastEmbedModelUnavailableError
network = []
def audit(event, args):
    if event == 'socket.connect':
        network.append(event)
        raise AssertionError('offline-model-network-attempt')
sys.addaudithook(audit)
try:
    model = FastEmbedProvider(offline=True, cache_dir=str(project.parent / 'empty-model-cache'))
    model.embed('synthetic cache-miss probe')
except FastEmbedModelUnavailableError as exc:
    model_error = {'class': type(exc).__name__, 'message': str(exc)}
    assert 'magicite fetch-model' in str(exc), 'missing-model-remediation-absent'
else:
    raise AssertionError('empty-offline-cache-unexpectedly-succeeded')
assert not network, 'offline-model-network-attempt'
result = {'ok': True, 'top': route.candidates[0].name, 'ingested': outcome.ingested,
          'pkg_file': str(pkg_file), 'fail_closed_without_custody': True,
          'custody': 'disposable-simulated', 'deployment_custody': 'UNEVALUATED',
          'offline_model_remediation': True, 'offline_network_attempts': len(network),
          'package_inventory_count': len(expected['package_files']), 'module_origins': origins(),
          'missing_custody_error': custody_error, 'offline_model_error': model_error,
          'runtime': {'python': sys.version, 'platform': platform.platform(), 'prefix': str(prefix),
                      'isolated_site_packages': 'include-system-site-packages = false' in
                      (prefix / 'pyvenv.cfg').read_text().lower()}}
conn.close(); store.close()
print(json.dumps(result))
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


def package_expectations() -> dict:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    package = ROOT / "src/magicite"
    files = {
        str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".json", ".sql"}
    }
    reference = json.loads((ROOT / "docs/generated/runtime-reference.json").read_text())
    return {
        "package_files": files,
        "version": project["version"],
        "requires_python": project["requires-python"],
        "entrypoints": project["scripts"],
        "tools": [{k: v for k, v in row.items() if k != "risk_class"} for row in reference["mcp_tools"]],
    }


def validate_wire(transcript: list[dict], expected: dict) -> dict:
    requests = {}
    pairs = []
    positions = {}

    def key(value):
        return type(value).__name__ + ":" + json.dumps(value)

    for position, row in enumerate(transcript):
        value = row["value"]
        identifier = value.get("id")
        if identifier is None:
            continue
        identity = key(identifier)
        if row["direction"] == "request":
            assert identity not in requests, "duplicate installed RPC ID"
            requests[identity] = value
            positions[identity] = position
        else:
            assert identity in requests and "error" not in value, "installed RPC pairing error"
            assert identity not in {key(pair[1]["id"]) for pair in pairs}, "duplicate installed RPC result"
            pairs.append((requests[identity], value, positions[identity], position))
    assert len(pairs) == len(requests) == 5, "complete installed RPC evidence required"
    init, tools, route, good, stale = pairs
    assert init[0]["method"] == "initialize" and init[1]["result"]["protocolVersion"] == "2025-11-25", (
        "installed negotiation evidence"
    )
    assert init[1]["result"]["serverInfo"]["name"] == "magicite", "installed server identity"
    assert tools[0]["method"] == "tools/list", "installed discovery missing"

    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    observed = [
        {
            "name": row["name"],
            "input_schema_sha256": fingerprint(row.get("inputSchema")),
            "output_schema_sha256": fingerprint(row.get("outputSchema")),
        }
        for row in tools[1]["result"]["tools"]
    ]
    assert observed == expected["tools"] and len(observed) == 16, "installed schemas mismatch"

    def payload(pair, name):
        request, response, *_ = pair
        assert request["method"] == "tools/call" and request["params"]["name"] == name, (
            "installed call identity"
        )
        result = response["result"]
        assert not result.get("isError"), "installed call error"
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    routed = payload(route, "route")
    selected = routed["selected_ids"][0]
    assert (
        routed["status"] == "selected"
        and route[0]["params"]["arguments"]["query"] == "rollback proton for a steam game"
    ), "installed route evidence"
    for pair, digest in ((good, routed["selected_content_digests"][selected]), (stale, "0" * 64)):
        args = pair[0]["params"]["arguments"]
        assert (
            args.get("name") == selected
            and args.get("expected_content_digest") == digest
            and args.get("expected_policy_digest") == routed["policy_digest"]
            and args.get("level") == "L2"
        ), "installed digest binding"
    body = payload(good, "load_skill_body")
    refused = payload(stale, "load_skill_body")
    assert (
        body["status"] == "ok"
        and body["name"] == "proton-ge-proton-downgrade"
        and body["procedure"] == expected["procedure"]
    ), "installed body mismatch"
    assert refused["status"] == "stale_decision" and not any(
        refused.get(k) for k in ("procedure", "pitfalls", "examples", "provenance")
    ), "installed stale disclosure"
    assert route[3] < good[2] < good[3] < stale[2], "installed causal ordering"
    return {
        "mode": "legacy-negotiated",
        "version": "2025-11-25",
        "tool_count": 16,
        "route_body": True,
        "stale_refusal": True,
    }


def protocol_probe(
    python: Path, harness: Path, arguments: list[str], work: Path, env: dict, expected: dict
) -> dict:
    stderr = (work / "server-stderr.log").open("w")
    process = subprocess.Popen(
        [str(python), "-I", str(harness), *arguments],
        cwd=work,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        bufsize=1,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    transcript = []

    def request(identifier, method, params):
        value = {"jsonrpc": "2.0", "method": method, "params": params}
        if identifier is not None:
            value["id"] = identifier
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()
        transcript.append({"direction": "request", "value": value})
        if identifier is None:
            return None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if selector.select(max(0, deadline - time.monotonic())):
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("installed-server-exited-before-response")
                response = json.loads(line)
                transcript.append({"direction": "response", "value": response})
                if response.get("id") == identifier:
                    if "error" in response:
                        raise RuntimeError("installed-server-rpc-error")
                    return response["result"]
        raise TimeoutError("installed-server-response-timeout")

    def tool(identifier, name, arguments):
        result = request(identifier, "tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError"), "installed-tool-error"
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    try:
        init = request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "magicite-install-probe", "version": "1.0"},
            },
        )
        assert init["protocolVersion"] == "2025-11-25", "installed-protocol-version-mismatch"
        request(None, "notifications/initialized", {})
        tools = request(2, "tools/list", {})["tools"]

        def digest(value):
            return hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()

        observed = [
            {
                "name": row["name"],
                "input_schema_sha256": digest(row.get("inputSchema")),
                "output_schema_sha256": digest(row.get("outputSchema")),
            }
            for row in tools
        ]
        assert observed == expected["tools"] and len(observed) == 16, "installed-tool-inventory-mismatch"
        route = tool(3, "route", {"query": "rollback proton for a steam game", "k": 5})
        assert route["status"] == "selected", "installed-route-not-selected"
        selected = route["selected_ids"][0]
        args = {
            "name": selected,
            "level": "L2",
            "expected_content_digest": route["selected_content_digests"][selected],
            "expected_policy_digest": route["policy_digest"],
        }
        body = tool(4, "load_skill_body", args)
        assert body["status"] == "ok" and body["name"] == "proton-ge-proton-downgrade", (
            "installed-body-identity-mismatch"
        )
        assert body["procedure"] == expected["procedure"], "installed-body-content-mismatch"
        stale = tool(5, "load_skill_body", {**args, "expected_content_digest": "0" * 64})
        assert stale["status"] == "stale_decision" and not any(
            stale.get(key) for key in ("procedure", "pitfalls", "examples", "provenance")
        ), "installed-stale-body-disclosure"
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        selector.close()
        stderr.close()
        (work / "wire.json").write_text(json.dumps(transcript, indent=2) + "\n")
    validate_wire(transcript, expected)
    assert process.returncode == 0, "installed-server-shutdown-failed"
    origins = json.loads((work / "server-origins.json").read_text())
    assert origins, "installed-server-module-origins-missing"
    return {
        "mode": "legacy-negotiated",
        "version": init["protocolVersion"],
        "tool_count": len(tools),
        "route_body": True,
        "stale_refusal": True,
        "server_module_origins": origins,
        "server_exit": process.returncode,
    }


def validate_cli_output(arguments: list[str], exit_code: int, stdout: str, expected: dict) -> None:
    if arguments == ["--version"]:
        assert exit_code == 0 and stdout.strip() == "magicite, version " + expected["version"], (
            "installed-cli-version-mismatch"
        )
    elif arguments == ["--help"]:
        assert exit_code == 0 and all(
            word in stdout for word in ("Usage:", "serve", "doctor", "fetch-model")
        ), "installed-cli-help-mismatch"
    else:
        report = json.loads(stdout)
        assert report.get("kind") == "doctor/1" and report.get("healthy") is False, (
            "installed-doctor-contract-mismatch"
        )
        custody = next(row for row in report["checks"] if row["id"] == "trust.custody")
        assert custody["status"] == "not_applicable" and custody["evidence"]["state"] == "unconfigured", (
            "installed-doctor-custody-mismatch"
        )
        assert exit_code == 1 and "protected custody" in custody["remediation"], (
            "installed-doctor-remediation-mismatch"
        )


def run_probe(*, wheel: Path, keep_env: Path | None = None, dependency_cache: Path | None = None) -> dict:
    wheel = Path(wheel).resolve()
    if not TOY_ENGRAMS.is_dir():
        raise FileNotFoundError(f"toy registry fixtures missing: {TOY_ENGRAMS}")
    work = Path(keep_env) if keep_env is not None else Path(tempfile.mkdtemp(prefix="magicite-wheel-"))
    work = work.resolve()
    if work.is_relative_to(ROOT.resolve()):
        raise ValueError("install environment must be outside checkout")
    if work.exists() and any(work.iterdir()):
        raise ValueError("fresh external working directory required")
    work.mkdir(parents=True, exist_ok=True)
    venv_dir = work / "venv"
    if venv_dir.exists():
        raise ValueError("fresh install environment required")
    venv.create(venv_dir, with_pip=True)
    python = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
    env = isolated_subprocess_env()
    for key in list(env):
        if key.startswith("MAGICITE_") or key in {
            "PIP_EXTRA_INDEX_URL",
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
        }:
            env.pop(key)
    (work / "home").mkdir()
    (work / "project").mkdir()
    env.update(
        HOME=str(work / "home"),
        XDG_CACHE_HOME=str(work / "cache"),
        HF_HOME=str(work / "hf"),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        MAGICITE_EMBEDDING_PROVIDER="hashing",
        MAGICITE_EMBEDDING_OFFLINE="1",
        PIP_CONFIG_FILE=os.devnull,
        PIP_INDEX_URL="https://pypi.org/simple",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    if dependency_cache is not None:
        env["PIP_CACHE_DIR"] = str(dependency_cache.resolve())
    command = [str(python), "-m", "pip", "install", str(wheel)]
    with (work / "install.log").open("w") as log:
        subprocess.run(command, check=True, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
    pip_check = subprocess.run(
        [str(python), "-m", "pip", "check"], check=True, cwd=work, env=env, capture_output=True
    )
    inventory = subprocess.check_output(
        [str(python), "-m", "pip", "list", "--format=json"], cwd=work, env=env, text=True
    )
    expected = package_expectations()
    expected["artifact_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    fixture = work / "fixture-data"
    fixture.mkdir()
    for path in TOY_ENGRAMS.glob("*.egr.md"):
        shutil.copyfile(path, fixture / path.name)
    text = (fixture / "proton-ge-proton-downgrade.egr.md").read_text()
    expected["procedure"] = text.split("## Procedure\n", 1)[1].split("## Pitfalls", 1)[0].strip()
    (work / "expected.json").write_text(json.dumps(expected))
    harness = work / "installed-probe.py"
    harness.write_text(PROBE)
    arguments = [str(fixture), str(work / "project"), str(venv_dir.resolve()), str(work / "expected.json")]
    cli = venv_dir / ("Scripts/magicite.exe" if os.name == "nt" else "bin/magicite")
    commands = []
    for args, allowed in [
        (["--version"], {0}),
        (["--help"], {0}),
        (["doctor", "--project-root", str(work / "project")], {0, 1}),
    ]:
        done = subprocess.run([str(cli), *args], cwd=work, env=env, capture_output=True, text=True)
        label = "version" if args == ["--version"] else "help" if args == ["--help"] else "doctor"
        (work / f"cli-{label}.json").write_text(
            json.dumps(
                {
                    "argv": [str(cli), *args],
                    "exit": done.returncode,
                    "stdout": done.stdout,
                    "stderr": done.stderr,
                },
                indent=2,
            )
            + "\n"
        )
        assert done.returncode in allowed, "installed-cli-command-failed"
        validate_cli_output(args, done.returncode, done.stdout, expected)
        commands.append({"argv": [str(cli), *args], "exit": done.returncode, "stdout": done.stdout})
    done = subprocess.run(
        [str(python), "-I", str(harness), *arguments, "prepare", str(work / "unused-origins.json")],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    (work / "prepare-stdout.log").write_text(done.stdout)
    (work / "prepare-stderr.log").write_text(done.stderr)
    done.check_returncode()
    result = json.loads(done.stdout.splitlines()[-1])
    result["protocol"] = protocol_probe(
        python, harness, [*arguments, "serve", str(work / "server-origins.json")], work, env, expected
    )
    result["command_records"] = {
        "install": {"argv": command, "exit": 0},
        "pip_check": {"argv": [str(python), "-m", "pip", "check"], "exit": pip_check.returncode},
        "dependencies": {"argv": [str(python), "-m", "pip", "list", "--format=json"], "exit": 0},
        "prepare": {
            "argv": [
                str(python),
                "-I",
                str(harness),
                *arguments,
                "prepare",
                str(work / "unused-origins.json"),
            ],
            "exit": done.returncode,
        },
        "server": {
            "argv": [str(python), "-I", str(harness), *arguments, "serve", str(work / "server-origins.json")],
            "exit": result["protocol"]["server_exit"],
        },
    }
    result["scrubbed_environment"] = {
        key: env.get(key)
        for key in ("PYTHONPATH", "PYTHONNOUSERSITE", "HF_HUB_OFFLINE", "MAGICITE_EMBEDDING_PROVIDER")
    }
    result["dependency_inventory_sha256"] = hashlib.sha256(
        json.dumps(json.loads(inventory), sort_keys=True).encode()
    ).hexdigest()
    result.update(
        artifact_sha256=expected["artifact_sha256"],
        dependency_inventory=json.loads(inventory),
        cli_commands=commands,
        python=str(python),
        install_command=command,
        fixture_hashes={
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture.glob("*.egr.md")
        },
    )
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


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
