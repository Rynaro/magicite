"""Fill the ``id:`` field of hand-authored engrams with their real content hash.

An engram's ``id`` is the first eight hex digits of a canonical-JSON SHA-256
over its identity+routing blocks (``magicite.engram.ids.new_engram_id``,
CR-8). Hand-authoring one is pointless, so the dogfood registry is written
with the ``egr_00000000`` placeholder and this script stamps the real value
in — using Magicite's own id module, not a reimplementation of it.

Idempotent: re-running after an unrelated body edit is a no-op, because the
body is not part of the identity payload. Re-running after an intent or
trigger edit rewrites the id, which is the correct behaviour *before* first
registration and a CR-8 violation *after* it -- so run this while authoring,
never against a registry that is already live.

    uv run python scripts/dogfood_ids.py [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from magicite.engram.ids import identity_routing_payload, new_engram_id
from magicite.engram.parser import parse_file

PLACEHOLDER = "egr_00000000"
_ID_LINE = re.compile(r"^id:\s*\S+\s*$", re.MULTILINE)


def _expected_id(path: Path, registry_root: Path) -> str:
    parsed = parse_file(path, registry_root=registry_root)
    fm = parsed.engram.frontmatter
    payload = identity_routing_payload(
        name=fm.name,
        intent_does=fm.intent.does,
        intent_use_when=fm.intent.use_when,
        intent_not_when=fm.intent.not_when,
        triggers_positive=list(fm.triggers.positive),
        triggers_negative=list(fm.triggers.negative),
    )
    return new_engram_id(payload)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--registry",
        type=Path,
        default=Path(".spectra/engrams"),
        help="directory of .egr.md files (default: .spectra/engrams)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero instead of rewriting",
    )
    args = ap.parse_args()

    paths = sorted(args.registry.glob("*.egr.md"))
    if not paths:
        print(f"no .egr.md files under {args.registry}", file=sys.stderr)
        return 1

    drifted = 0
    for path in paths:
        want = _expected_id(path, args.registry)
        raw = path.read_text(encoding="utf-8")
        current = parse_file(path, registry_root=args.registry).engram.frontmatter.id
        if current == want:
            continue
        drifted += 1
        if args.check:
            note = "placeholder" if current == PLACEHOLDER else f"stale ({current})"
            print(f"DRIFT {path.name}: {note} -> {want}")
            continue
        path.write_text(_ID_LINE.sub(f"id: {want}", raw, count=1), encoding="utf-8")
        print(f"stamped {path.name}: {current} -> {want}")

    if args.check and drifted:
        print(f"\n{drifted} engram id(s) out of date; run without --check", file=sys.stderr)
        return 1
    print(f"\n{len(paths)} engram(s), {drifted} updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
