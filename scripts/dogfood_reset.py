"""Restore the dogfood registry to its authored state after a probe run.

``scripts/dogfood_session.py`` drives real signals through the server, and
a Dream checkpoint legitimately writes durable Tier-A state back into the
``.egr.md`` files: exposure counts move, ``last_checkpoint`` is stamped, and
a ``dream-worker`` entry is appended to the provenance journal. That is the
system working -- but it is *probe* history, not authored content, and
committing it would make the registry non-reproducible (every re-run
increments it further) and misleading to read.

This script strips exactly the checkpoint-written state and leaves the
hand-authored content untouched, so the committed registry is always the
pristine artifact a fresh clone gets, and the probe stays re-runnable.

It edits only fields the checkpoint owns. It never touches intent,
triggers, edges, body prose, or the authored journal entry.

    uv run python scripts/dogfood_reset.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML

#: The plasticity fields a Dream checkpoint writes, and their authored values.
#: ``status`` is deliberately absent: a genuine lifecycle promotion is content
#: worth keeping, and this registry has never had one.
AUTHORED_PLASTICITY: dict[str, object] = {
    "storage_strength": 0.0,
    "exposure_count": 0,
    "excitability": 0.05,
}
#: Journal entries with this author are checkpoint bookkeeping, not authorship.
CHECKPOINT_AUTHOR = "dream-worker"

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096


def _split(raw: str) -> tuple[str, str]:
    if not raw.startswith("---\n"):
        raise ValueError("missing frontmatter fence")
    end = raw.index("\n---\n", 3)
    return raw[4:end + 1], raw[end + 5:]


def reset_one(path: Path, *, check: bool) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    yaml_text, body = _split(raw)
    doc = _yaml.load(yaml_text)
    changes: list[str] = []

    plasticity = doc.get("plasticity") or {}
    for key, want in AUTHORED_PLASTICITY.items():
        if plasticity.get(key) != want:
            changes.append(f"plasticity.{key}: {plasticity.get(key)!r} -> {want!r}")
            plasticity[key] = want
    for key in ("last_applied", "last_checkpoint"):
        if key in plasticity:
            if plasticity.get(key) is not None:
                changes.append(f"plasticity.{key}: cleared")
            plasticity.pop(key, None)
    outcome = plasticity.get("outcome") or {}
    for key in ("success", "failure"):
        if outcome.get(key):
            changes.append(f"plasticity.outcome.{key}: {outcome[key]} -> 0")
            outcome[key] = 0

    # peak_storage_strength is pure checkpoint bookkeeping.
    if "peak_storage_strength" in doc:
        changes.append("peak_storage_strength: dropped")
        doc.pop("peak_storage_strength")

    # A checkpoint materialises the declared needs/inhibits edges into a
    # `synapses:` block. Those rows are re-derived from the composition
    # block on every sync, so carrying them in the authored file is pure
    # duplication. Learned edges are real data and are kept.
    synapses = doc.get("synapses")
    if synapses:
        learned = [s for s in synapses if s.get("provenance") != "declared"]
        if len(learned) != len(synapses):
            changes.append(
                f"synapses: dropped {len(synapses) - len(learned)} re-derivable declared edge(s)"
            )
            if learned:
                doc["synapses"] = learned
            else:
                doc.pop("synapses")

    journal = doc.get("provenance_journal") or []
    kept = [e for e in journal if e.get("author") != CHECKPOINT_AUTHOR]
    if len(kept) != len(journal):
        changes.append(f"provenance_journal: dropped {len(journal) - len(kept)} checkpoint entr(ies)")
        doc["provenance_journal"] = kept

    if changes and not check:
        import io

        buf = io.StringIO()
        _yaml.dump(doc, buf)
        path.write_text(f"---\n{buf.getvalue()}---\n{body.lstrip(chr(10))}", encoding="utf-8")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, default=Path(".magicite/engrams"))
    ap.add_argument("--check", action="store_true", help="report drift, exit non-zero, rewrite nothing")
    args = ap.parse_args()

    paths = sorted(args.registry.glob("*.egr.md"))
    if not paths:
        print(f"no .egr.md files under {args.registry}", file=sys.stderr)
        return 1

    dirty = 0
    for path in paths:
        changes = reset_one(path, check=args.check)
        if not changes:
            continue
        dirty += 1
        verb = "DRIFT" if args.check else "reset"
        print(f"{verb} {path.name}")
        for line in changes:
            print(f"    {line}")

    print(f"\n{len(paths)} engram(s), {dirty} carrying probe state")
    if args.check and dirty:
        print("registry is not in authored state; run without --check", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
