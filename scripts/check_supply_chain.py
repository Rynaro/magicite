#!/usr/bin/env python3
"""Fail when workflow/container executable inputs are mutable."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}")


def main() -> int:
    failures: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("uses: "):
                continue
            reference = stripped.split()[1]
            revision = reference.rsplit("@", 1)[-1]
            if SHA.fullmatch(revision) is None:
                failures.append(
                    f"{workflow.relative_to(ROOT)} has non-SHA action input {reference}"
                )

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM ") or stripped.startswith("COPY --from="):
            image = (
                stripped.split()[1]
                if stripped.startswith("FROM ")
                else stripped.split("=", 1)[1].split()[0]
            )
            if image in {"builder", "runtime"}:
                continue
            if "@sha256:" not in image:
                failures.append(f"mutable container input: {stripped}")

    if failures:
        print("\n".join(failures))
        return 1
    print("supply-chain inputs are immutable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
