#!/usr/bin/env python3
"""Executable authority and semantic-parity checks for current documentation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from magicite.config import Config

ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = [
    ROOT / f"docs/{number:02d}-{name}.md"
    for number, name in (
        (2, "architecture"),
        (3, "learning-model"),
        (4, "engram-format"),
        (5, "protocol-and-signals"),
        (6, "trust-governance-lifecycle"),
        (7, "evaluation-and-observability"),
    )
]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _database_contract() -> list[str]:
    errors: list[str] = []
    authority = _text("docs/AUTHORITY.md")
    ignore = _text(".gitignore")
    governance = _text("docs/06-trust-governance-lifecycle.md")
    if "`skill-graph.db` is local, rebuildable state and is ignored by default" not in authority:
        errors.append("authority manifest does not define the DB as local/rebuildable/ignored")
    for pattern in ("skill-graph.db\n", "skill-graph.db-wal\n", "skill-graph.db-shm\n"):
        if pattern not in ignore:
            errors.append(f".gitignore is missing {pattern.strip()}")
    if not all(
        marker in governance for marker in ("`skill-graph.db` is local, rebuildable", "ignored by\n  default")
    ):
        errors.append("docs/06 still lacks the current database version-control contract")
    if "All `.egr.md` files and `skill-graph.db`" in governance:
        errors.append("docs/06 still claims the rebuildable DB is committed with engrams")
    return errors


def _append_only_contract() -> list[str]:
    errors: list[str] = []
    process = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", ".spectra/changes/archive"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        errors.append(f"git archive diff check failed: {process.stderr.strip()}")
    elif process.stdout.strip():
        errors.append(f"archived evidence was modified: {process.stdout.strip()}")
    result = json.loads(_text("docs/evaluation/v0.3-results.json"))
    if result.get("status") != "superseding":
        errors.append("evaluation correction is not published as superseding evidence")
    if any("retained" not in item.get("disposition", "") for item in result.get("supersedes", [])):
        errors.append("superseded evidence is not explicitly retained")
    return errors


def _authority_contract() -> list[str]:
    errors: list[str] = []
    authority = _text("docs/AUTHORITY.md")
    if "## Authority order" not in authority or "Current documents `02` through `07`" not in authority:
        errors.append("docs/AUTHORITY.md does not declare the v0.3 authority order")
    redirects = json.loads(_text("docs/archive-redirects.json"))
    if redirects.get("schema") != "magicite/archive-redirects/1":
        errors.append("archive redirect manifest schema is missing")
    for source, target in redirects.get("redirects", {}).items():
        if (ROOT / source).exists():
            errors.append(f"redirect source is unexpectedly live: {source}")
        if not (ROOT / target).is_file():
            errors.append(f"dead archive redirect target: {source} -> {target}")
    for path in DOC_PATHS:
        if not path.is_file():
            errors.append(f"missing current normative document {path.relative_to(ROOT)}")
    return errors


def _semantic_contract() -> list[str]:
    errors: list[str] = []
    docs = {path.name: path.read_text(encoding="utf-8") for path in DOC_PATHS}
    cfg = Config()
    required = {
        "02-architecture.md": [
            "FastEmbed/ONNX by default",
            "Ollama optional",
            "register, sync, sharpen, and lifecycle operations",
        ],
        "03-learning-model.md": [
            "w_retrieval=0.05",
            "Durable weight changes (to S) happen ONLY in the offline Dream cycle",
        ],
        "04-engram-format.md": [
            "immutable hash of identity+routing blocks",
            "content_sha256",
            "`yields` is metadata-only in 0.3",
        ],
        "05-protocol-and-signals.md": [
            "runtime exposes exactly 16 tools",
            "`w_retrieval`=0.05",
        ],
        "06-trust-governance-lifecycle.md": [
            "lifecycle_status=nascent",
            "verification_status=pending",
            "decide → resume",
            "audit_log",
        ],
        "07-evaluation-and-observability.md": [
            "docs/evaluation/v0.3-results.json",
            "SUPPORTED (structural only)",
            "End-to-end decomposition, winner retrieval, and task success remain untested",
        ],
    }
    for name, needles in required.items():
        for needle in needles:
            if needle not in docs[name]:
                errors.append(f"docs/{name} is missing semantic marker {needle!r}")
    forbidden = {
        "02-architecture.md": [
            "all state mutations come from one batch worker",
            "Only the Dream worker writes",
        ],
        "04-engram-format.md": ["Only the offline Dream cycle checkpoints DB → file"],
        "06-trust-governance-lifecycle.md": ["All `.egr.md` files and `skill-graph.db`"],
        "07-evaluation-and-observability.md": ["Zero compositional queries run"],
    }
    for name, needles in forbidden.items():
        for needle in needles:
            if needle in docs[name]:
                errors.append(f"docs/{name} retains stale claim {needle!r}")
    if cfg.embedding_provider != "fastembed" or cfg.w_retrieval != 0.05:
        errors.append("runtime provider/retrieval defaults changed without documentation parity updates")
    return errors


CONTRACTS = {
    "database-local-rebuildable": _database_contract,
    "append-only-errata": _append_only_contract,
    "v0.3-semantic-parity": _semantic_contract,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", choices=sorted(CONTRACTS))
    args = parser.parse_args(argv)
    errors = _authority_contract()
    if args.contract:
        errors.extend(CONTRACTS[args.contract]())
    else:
        for check in CONTRACTS.values():
            errors.extend(check())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"documentation contract {args.contract or 'all'} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
