#!/usr/bin/env python3
"""Offline data controls only: validate, freeze, verify and guarded label access."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from magicite.eval import data_readiness as data  # noqa: E402
from magicite.eval.digests import sha256_bytes  # noqa: E402


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def source_inputs() -> dict[str, str]:
    paths = git("ls-files", "src", "scripts", "tests/fixtures/calibration-data", "pyproject.toml", "uv.lock")
    return {name: sha256_bytes((ROOT / name).read_bytes()) for name in paths.splitlines()}


def clean_candidate(commit: str) -> dict[str, str]:
    if git("rev-parse", "HEAD") != commit or git("status", "--porcelain"):
        raise ValueError("freeze requires exact clean source candidate")
    return source_inputs()


def verify(path: Path) -> dict:
    frozen = data.verify_freeze(path)
    binding = frozen["binding"]
    if (
        binding["source_commit"] != git("rev-parse", "HEAD")
        or binding.get("source_inputs") != source_inputs()
        or binding["runner_sha256"] != sha256_bytes(Path(__file__).read_bytes())
    ):
        raise ValueError("runner/source candidate binding changed")
    return frozen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    validate = commands.add_parser("validate", help="Preparation authors inspect labels; no final seal")
    validate.add_argument("packet", type=Path)
    freeze = commands.add_parser("freeze", help="Bind complete inputs on a clean source candidate")
    freeze.add_argument("packet", type=Path)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--source-commit", required=True)
    for name in ("verify", "open"):
        command = commands.add_parser(name)
        command.add_argument("freeze", type=Path)
        if name == "open":
            command.add_argument("--purpose", required=True)
            command.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    result: dict[str, Any]
    try:
        if args.action == "validate":
            prepared = data.prepare_packet(args.packet)
            result = {"report": prepared.report, "inputs": prepared.inputs}
        elif args.action == "freeze":
            inputs = clean_candidate(args.source_commit)
            result = data.freeze_packet(
                args.packet,
                args.output,
                source_commit=args.source_commit,
                runner_sha256=sha256_bytes(Path(__file__).read_bytes()),
                source_inputs=inputs,
            )
        else:
            result = verify(args.freeze)
            if args.action == "open":
                labels = data.open_final_labels(args.freeze, purpose=args.purpose, replay=args.replay)
                result = {
                    "freeze_identity": result["identity"],
                    "final_labels": labels,
                    "access_intent": json.loads(data.access_path(args.freeze).read_bytes()),
                    "qualifying": False,
                    "authentic_readiness": "UNEVALUATED",
                }
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
