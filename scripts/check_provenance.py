#!/usr/bin/env python3
"""Validate the prospective agent provenance contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = {
    "schema",
    "change_id",
    "model",
    "host",
    "role",
    "commit_range",
    "checker",
    "approval",
    "historical_attribution",
}


def main() -> int:
    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED - payload.keys())
    if missing:
        print(f"missing provenance fields: {', '.join(missing)}")
        return 1
    if payload["historical_attribution"].lower().startswith("fable attributed"):
        print("historical provenance may not be guessed")
        return 1
    print("provenance contract valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
