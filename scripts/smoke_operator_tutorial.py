#!/usr/bin/env python3
"""Execute the documented disposable operator path; fixture evidence, not external validation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_tutorial(work: Path) -> dict[str, Any]:
    from tests.support.custody_adapter import SERVE_LAUNCHER, FixtureCustody, attach_fixture

    work = work.resolve()
    project = work / "project"
    project.mkdir(parents=True)
    # Disposable simulated custody stands in for the separately provisioned
    # custodian account; deployment custody stays UNEVALUATED.
    custody_dir = work / "custody"
    registry_id = "tutorial-" + hashlib.sha256(str(project).encode()).hexdigest()[:24]
    FixtureCustody(custody_dir, registry_id).close()
    launcher = [str(SERVE_LAUNCHER), str(project), str(custody_dir), registry_id]
    with attach_fixture(project, custody_dir, registry_id) as provider:
        return _run_tutorial(work, project, provider, launcher)


def _run_tutorial(work: Path, project: Path, provider: Any, launcher: list[str]) -> dict[str, Any]:
    from magicite.config import Config
    from magicite.core.trust_journal import TrustJournal

    authority = Config(project_root=project).data_dir / "trust/authority"
    TrustJournal(authority, provider.registry_id, provider).initialize_reviewed_genesis()
    environment = work / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "bin/python"
    site = next((environment / "lib").glob("python*/site-packages"))
    # Reuse already installed dependency distributions offline; install THIS project separately.
    dependencies = [p for p in sys.path if p.endswith("site-packages") and Path(p).is_dir()]
    (site / "tutorial-dependencies.pth").write_text("\n".join(dependencies) + "\n")
    installer = [shutil.which("uv")] if shutil.which("uv") else [sys.executable, "-m", "uv"]
    install = subprocess.run(
        [*installer, "pip", "install", "--python", str(python), "--no-deps", "--editable", str(ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "UV_CACHE_DIR": str(work / "uv-cache")},
    )
    if install.returncode:
        raise RuntimeError(f"tutorial installation failed: {install.stderr}")
    env = {**os.environ, "MAGICITE_EMBEDDING_PROVIDER": "hashing", "MAGICITE_EMBEDDING_OFFLINE": "1"}
    steps: list[dict[str, Any]] = [
        {
            "step": "install-local-source",
            "status": "pass",
            "dependencies": "reused installed pinned environment",
        }
    ]

    def command(
        *args: str,
        allow_failure: bool = False,
        input_json: dict | None = None,
        target: tuple[Path, list[str]] | None = None,
    ) -> dict[str, Any]:
        root, attached = target or (project, launcher)
        run = subprocess.run(
            [str(python), *attached, *args, "--project-root", str(root)],
            env=env,
            input=json.dumps(input_json) if input_json is not None else None,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if run.returncode and not allow_failure:
            raise RuntimeError(f"tutorial command {args} failed: {run.stdout} {run.stderr}")
        try:
            payload = json.loads(run.stdout)
        except ValueError:
            payload = {"output": run.stdout.strip()}
        steps.append({"step": " ".join(args[:2]), "exit_code": run.returncode, "output": payload})
        return payload

    incoming = project / "incoming"
    incoming.mkdir()
    shutil.copyfile(
        ROOT / "tests/fixtures/toy-registry/engrams/nvidia-prime-render-offload.egr.md",
        incoming / "nvidia-prime-render-offload.egr.md",
    )

    async def host() -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=str(python), args=[*launcher, "serve", "--project-root", str(project)], env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                if len(tools.tools) != 16:
                    raise RuntimeError("tutorial tool inventory drift")
                steps.append(
                    {
                        "step": "generic-stdio-handshake",
                        "tools": 16,
                        "protocol": getattr(init, "protocol_version", None)
                        or getattr(init, "protocolVersion", None),
                    }
                )
                imported = await session.call_tool(
                    "register", arguments={"path": "incoming", "request_id": "tutorial-import"}
                )
                if imported.is_error:
                    raise RuntimeError(str(imported))
                data = imported.structured_content or json.loads(imported.content[0].text)
                steps.append({"step": "external-import", "output": data})
                entry = data["registered"][0]
                review = command("trust", "review", "--engram-id", entry["id"])
                command(
                    "trust",
                    "approve",
                    "--engram-id",
                    entry["id"],
                    "--expected-digest",
                    review["content_digest"],
                    "--actor",
                    "tutorial-operator",
                )
                routed = await session.call_tool(
                    "route", arguments={"query": "force game onto nvidia gpu", "k": 1}
                )
                data = routed.structured_content or json.loads(routed.content[0].text)
                if routed.is_error or not data.get("candidates"):
                    raise RuntimeError(f"tutorial route failed: {data}")
                top = data["candidates"][0]
                body_args = {
                    "name": top["name"],
                    "level": "L2",
                    "expected_content_digest": data["selected_content_digests"][top["id"]],
                    "expected_policy_digest": data["policy_digest"],
                }
                body = await session.call_tool("load_skill_body", arguments=body_args)
                payload = body.structured_content or json.loads(body.content[0].text)
                if body.is_error or payload.get("status") != "ok" or not payload.get("procedure"):
                    raise RuntimeError(f"tutorial body failed: {payload}")
                steps.append(
                    {
                        "step": "route-explain-body",
                        "status": "pass",
                        "route": data,
                        "body_status": payload["status"],
                    }
                )
                # Deliberate stale digest: rerouting is the documented remediation.
                stale = await session.call_tool(
                    "load_skill_body", arguments={**body_args, "expected_content_digest": "0" * 64}
                )
                stale_data = stale.structured_content or json.loads(stale.content[0].text)
                if stale_data.get("status") != "stale_decision":
                    raise RuntimeError("stale body digest was not denied")
                steps.append({"step": "stale-body-denied", "status": stale_data["status"]})
                again = await session.call_tool(
                    "route", arguments={"query": "force game onto nvidia gpu", "k": 1}
                )
                if again.is_error:
                    raise RuntimeError("reroute remediation failed")
                steps.append({"step": "reroute-remediation", "status": "pass"})

    asyncio.run(host())
    route_request = {"query": "force game onto nvidia gpu", "k": 1}
    checkpoint_args = ("evidence", "checkpoint", "--route-request", "-", "--event-id", "tutorial-decision")
    original = command(*checkpoint_args, input_json=route_request)
    replay = command(*checkpoint_args, input_json=route_request)
    if (
        not original["selected_ids"]
        or not replay["checkpoint"]["replayed"]
        or original["decision_id"] != replay["decision_id"]
    ):
        raise RuntimeError("durable route checkpoint/replay did not preserve the original decision")
    feedback_args = (
        "evidence",
        "checkpoint",
        "--decision-event-id",
        "tutorial-decision",
        "--event-id",
        "tutorial-outcome",
        "--outcome",
        "success",
    )
    command(*feedback_args)
    feedback_replay = command(*feedback_args)
    if not feedback_replay["replayed"]:
        raise RuntimeError("linked feedback replay failed")
    steps.append(
        {"step": "durable-route-and-feedback", "status": "pass", "decision_id": original["decision_id"]}
    )
    command(
        "doctor", allow_failure=True
    )  # Fixture registry may report missing production model/scale evidence.
    snapshot = command("backup", "create", "--dest", str(work / "snapshot"))
    command("backup", "status", "--backup-path", str(work / "snapshot"))
    # Current overlay/anchor are exported through the domain API; no fictional custody CLI.
    from magicite.config import Config
    from magicite.core import backup, fingerprint_key

    cfg = Config(project_root=project)
    key = fingerprint_key.load_or_create_fingerprint_key(cfg)
    overlay = backup.build_recovery_overlay(
        cfg,
        operator_provenance="tutorial-operator",
        control_sequence=max(snapshot["recovery_point_sequences"].values(), default=0) + 1,
        key=key,
    )
    anchor = backup.issue_sequence_anchor(overlay, key=key)
    overlay_path, anchor_path = work / "overlay.json", work / "anchor.json"
    overlay_path.write_text(json.dumps(overlay.to_dict()))
    anchor_path.write_text(json.dumps(anchor.to_dict()))
    restored = command(
        "backup",
        "restore",
        "--backup-path",
        str(work / "snapshot"),
        "--overlay",
        str(overlay_path),
        "--anchor",
        str(anchor_path),
    )
    if restored.get("reconciliation_required"):
        raise RuntimeError(f"tutorial authenticated restore remained gated: {restored}")
    command("migration", "preview")
    _legacy_upgrade(work, command)
    report = {
        "schema": "magicite/operator-tutorial/1",
        "status": "pass",
        "provider": "hashing",
        "independent_operator": "UNEVALUATED",
        "published_install": "UNEVALUATED",
        "custody": "disposable-simulated",
        "deployment_custody": "UNEVALUATED",
        "version": importlib.metadata.version("magicite"),
        "python": sys.version.split()[0],
        "mcp_version": importlib.metadata.version("mcp"),
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "steps": steps,
    }
    # Fixture-only transcript; hide local filesystem identities.
    return json.loads(
        json.dumps(report, default=str).replace(str(work), "<WORK>").replace(str(ROOT), "<SOURCE>")
    )


def _legacy_upgrade(work: Path, command: Any) -> None:
    """Reviewed upgrade of a disposable pre-custody project through the public CLI."""
    from tests.support.custody_adapter import SERVE_LAUNCHER, FixtureCustody

    from magicite.config import Config
    from magicite.core import trust
    from magicite.storage import db

    legacy = work / "legacy-project"
    cfg = Config(project_root=legacy)
    cfg.ensure_dirs()
    for source in (ROOT / "tests/fixtures/toy-registry/engrams").glob("*.egr.md"):
        shutil.copyfile(source, cfg.registry_dir / source.name)
    db.connect(cfg.db_path).close()
    (cfg.data_dir / "trust").mkdir(exist_ok=True)
    (cfg.data_dir / "trust/policy.json").write_text(json.dumps(trust.default_policy().to_dict()))
    original = {p.name: p.read_bytes() for p in cfg.registry_dir.glob("*.egr.md")}

    custody_dir = work / "legacy-custody"
    registry_id = "tutorial-legacy-" + hashlib.sha256(str(legacy).encode()).hexdigest()[:24]
    FixtureCustody(custody_dir, registry_id).close()
    target = (legacy, [str(SERVE_LAUNCHER), str(legacy), str(custody_dir), registry_id])

    preview = command(
        "custody",
        "legacy-preview",
        "--registry-id",
        registry_id,
        "--actor",
        "tutorial-operator",
        target=target,
    )
    digest = preview["reviewed_sha256"]
    plan_path, backup_path = work / "legacy-plan.json", work / "legacy-backup"
    plan_path.write_text(json.dumps(preview["plan"]))
    command(
        "custody",
        "legacy-backup",
        "--plan",
        str(plan_path),
        "--reviewed-sha256",
        digest,
        "--destination",
        str(backup_path),
        target=target,
    )
    command("migration", "preview", target=target)
    reviewed = ("--reviewed-sha256", digest, "--backup-path", str(backup_path))
    applied = command("migration", "apply", *reviewed, target=target)
    if applied.get("state") != "completed":
        raise RuntimeError(f"reviewed legacy upgrade did not complete: {applied}")
    status = command("migration", "status", "--operation-id", applied["operation_id"], target=target)
    if status.get("state") != "completed":
        raise RuntimeError(f"legacy upgrade status mismatch: {status}")
    stage = work / "legacy-stage"
    staged = command("migration", "restore", *reviewed, "--staging-path", str(stage), target=target)
    if staged.get("state") != "reconciliation_required":
        raise RuntimeError(f"legacy restore was not restricted to inactive staging: {staged}")
    if {p.name: p.read_bytes() for p in (stage / "files/engrams").glob("*.egr.md")} != original:
        raise RuntimeError("legacy restore staging did not preserve original source bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="magicite-tutorial-") as temp:
        report = run_tutorial(Path(temp))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("operator tutorial fixture passed; independent operator and published install remain UNEVALUATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
