#!/usr/bin/env python3
"""Verify the public tool inventory against the registered runtime surface."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from magicite.mcp import app as _app  # noqa: F401  (imports/registers every binding)
from magicite.mcp.registry import registered_names

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    runtime = registered_names()
    errors: list[str] = []
    if len(runtime) != 16 or len(set(runtime)) != 16:
        errors.append(f"runtime has {len(runtime)} tools ({len(set(runtime))} unique), expected 16")

    docs = (ROOT / "docs/05-protocol-and-signals.md").read_text(encoding="utf-8")
    match = re.search(
        r"authoritative manifest returned by `magicite tools`:\n(?P<inventory>.*?)\. CI compares",
        docs,
        flags=re.DOTALL,
    )
    if match is None:
        errors.append("docs/05 has no delimited authoritative tool inventory")
    else:
        documented = re.findall(r"`([a-z_]+)`", match.group("inventory"))
        if len(documented) != 16 or len(set(documented)) != 16:
            errors.append(
                f"documented inventory has {len(documented)} entries "
                f"({len(set(documented))} unique), expected 16"
            )
        if set(documented) != set(runtime):
            errors.append(
                f"documented/runtime tool drift: missing={sorted(set(runtime) - set(documented))}, "
                f"extra={sorted(set(documented) - set(runtime))}"
            )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("generated documentation matches the 16 registered tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
