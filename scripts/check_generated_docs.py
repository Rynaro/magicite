#!/usr/bin/env python3
"""Generate/check the installed runtime's version, schema, MCP, CLI and config reference."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from magicite.__main__ import cli
from magicite.config import Config
from magicite.mcp import app as _app  # noqa: F401
from magicite.mcp.registry import manifest, registered_names

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/generated/runtime-reference.json"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def runtime_reference() -> dict[str, Any]:
    commands: dict[str, Any] = {}

    def walk(command: click.Command, prefix: str) -> None:
        commands[prefix] = [
            {
                "name": p.name,
                "options": p.opts,
                "required": p.required,
                "default": p.default,
                "type": p.type.name,
                "choices": list(p.type.choices) if isinstance(p.type, click.Choice) else None,
                "path_exists": p.type.exists if isinstance(p.type, click.Path) else None,
            }
            for p in command.params
        ]
        if isinstance(command, click.Group):
            for name, child in sorted(command.commands.items()):
                walk(child, f"{prefix} {name}")

    walk(cli, "magicite")
    cfg = asdict(Config(project_root=Path("/PROJECT")))
    schemas = ROOT / "src/magicite/engram/schema"
    return json.loads(
        json.dumps(
            {
                "schema": "magicite/runtime-reference/1",
                "version": importlib.metadata.version("magicite"),
                "engram_schemas": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(schemas.glob("*.schema.json"))
                },
                "mcp_tools": [
                    {
                        "name": row["name"],
                        "risk_class": row["risk_class"],
                        "input_schema_sha256": _digest(row["input_schema"]),
                        "output_schema_sha256": _digest(row["output_schema"]),
                    }
                    for row in manifest()
                ],
                "cli": commands,
                "config_defaults": cfg,
            },
            default=str,
        )
    )


def check(reference: Path = REFERENCE, *, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        saved = json.loads(reference.read_text())
    except (OSError, ValueError) as exc:
        return [f"runtime reference missing/unreadable: {exc}"]
    actual = runtime_reference()
    if saved != actual:
        for key in sorted(set(actual) | set(saved)):
            if saved.get(key) != actual.get(key):
                errors.append(f"runtime reference drift: {key}")
    runtime = registered_names()
    if len(runtime) != 16 or len(set(runtime)) != 16:
        errors.append("runtime must expose exactly 16 unique MCP tools")
    docs = (root / "docs/05-protocol-and-signals.md").read_text()
    match = re.search(
        r"authoritative manifest returned by `magicite tools`:\n(?P<inventory>.*?)\. CI compares",
        docs,
        re.DOTALL,
    )
    if match is None or set(re.findall(r"`([a-z_]+)`", match.group("inventory"))) != set(runtime):
        errors.append("documented MCP inventory differs from runtime")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate from installed runtime")
    args = parser.parse_args()
    if args.write:
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_text(json.dumps(runtime_reference(), indent=2, sort_keys=True) + "\n")
    errors = check()
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if not errors:
        print("generated version/schema/MCP/CLI/config reference matches installed runtime")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
