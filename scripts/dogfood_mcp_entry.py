"""Emit (or apply) this project's own ``.mcp.json`` entry for Magicite.

``.mcp.json`` is gitignored machine-local wiring -- it carries absolute
paths and is not a tracked artifact -- so the committed deliverable is this
generator rather than the file. Running it with ``--apply`` merges the entry
into an existing ``.mcp.json`` without disturbing the sibling servers.

Two wirings, and the choice is the tradeoff recorded in this change's spec:

``--mode local`` (default)
    Runs ``uv run magicite serve`` against the working tree. Dogfooding
    exists to make us feel our own defects, and only this binding routes
    through the code currently being edited.

``--mode container``
    Runs the published, digest-pinned image with the hardened flags from
    README.md. Reproducible and identical to what external users run, but
    frozen at the released version, so a regression in ``src/`` is invisible
    to it.

    uv run python scripts/dogfood_mcp_entry.py [--mode local|container] [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: The v0.2.0 published image. Pin a digest, never a moving tag -- a tag
#: silently changes what every downstream project runs.
IMAGE = (
    "ghcr.io/rynaro/magicite@sha256:"
    "486f3c510ad48d7e6a3ca32dfa2e40ba29b06e3572f13da9264e088834c87b67"
)


def local_entry(root: Path) -> dict:
    return {
        "command": "uv",
        "args": ["run", "--project", str(root), "magicite", "serve", "--project-root", str(root)],
        "env": {"MAGICITE_EMBEDDING_OFFLINE": "1"},
    }


def container_entry(root: Path) -> dict:
    return {
        "command": "docker",
        "args": [
            "run", "--rm", "-i",
            # Required, not optional: `magicite serve` calls ensure_dirs() at
            # boot and a bind mount preserves host ownership, so the image's
            # baked-in UID cannot write a host-owned project root.
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--label", "eidolons.project=magicite",
            "-v", f"{root}:{root}:z", "-w", str(root),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            IMAGE, "serve", "--project-root", str(root),
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("local", "container"), default="local")
    ap.add_argument("--project-root", type=Path, default=Path.cwd())
    ap.add_argument("--apply", action="store_true", help="merge into .mcp.json in place")
    args = ap.parse_args()

    root = args.project_root.resolve()
    entry = local_entry(root) if args.mode == "local" else container_entry(root)

    if not args.apply:
        print(json.dumps({"mcpServers": {"magicite": entry}}, indent=2))
        print(
            "\n# not applied. Re-run with --apply to merge into .mcp.json,\n"
            "# or paste the 'magicite' key into the existing mcpServers object.\n"
            "# Set MAGICITE_HOOK_TOKEN in this entry's env to enable Tier-2 hooks\n"
            "# (see docs/adapters/claude-code.md); leave it unset for Tier-1.",
            file=sys.stderr,
        )
        return 0

    path = root / ".mcp.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    servers = config.setdefault("mcpServers", {})
    action = "replaced" if "magicite" in servers else "added"
    servers["magicite"] = entry
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"{action} 'magicite' ({args.mode}) in {path}; {len(servers)} server(s) configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
