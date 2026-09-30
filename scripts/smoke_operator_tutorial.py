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


def run_tutorial(work: Path) -> dict[str, Any]:
    project = work / "project"
    project.mkdir(parents=True)
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

    def command(*args: str, allow_failure: bool = False, input_json: dict | None = None) -> dict[str, Any]:
        run = subprocess.run(
            [str(python), "-m", "magicite", *args, "--project-root", str(project)],
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
            command=str(python), args=["-m", "magicite", "serve", "--project-root", str(project)], env=env
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
    applied = command("migration", "apply", "--operation-id", "tutorial-upgrade")
    command("migration", "status", "--operation-id", applied["operation_id"])
    command("migration", "restore", "--backup-path", str(cfg.data_dir / applied["backup_relpath"]))
    report = {
        "schema": "magicite/operator-tutorial/1",
        "status": "pass",
        "provider": "hashing",
        "independent_operator": "UNEVALUATED",
        "published_install": "UNEVALUATED",
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
