"""Structural checks over the dogfood registry (AC-T2, AC-T3).

Two independent checks, both mechanical:

``--edges``   every declared ``needs``/``composes``/``inhibits`` target
              resolves to a registered engram, and the declared-edge graph
              is *connected across tranches* -- tranche-2 (codebase) engrams
              are not a disjoint component from tranche-1 (operational)
              ones.

``--symbols`` every code reference an engram makes actually exists in the
              tree. Engrams describing ``src/magicite/**`` are only useful if
              their factual claims stay true, and prose drifts silently while
              code moves. This turns "the engram says ``core/edge_weight.py``
              has ``effective_strength``" into a failing check the moment it
              stops being so.

Tranche membership is read from each engram's own provenance journal note
rather than from a hardcoded list, so a new engram joins the right tranche by
saying which change authored it.

    uv run python scripts/dogfood_graph_check.py [--edges] [--symbols]

With no flag, runs both. Exit code 0 iff every requested check passes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from magicite.engram.parser import parse_file

#: Frontmatter composition keys that become declared edges in the index.
EDGE_KEYS = ("needs", "composes", "inhibits")
#: Substring of the authoring journal note that marks the codebase tranche.
TRANCHE2_MARKER = "Codebase tranche"

#: A backticked token that looks like a repo-relative Python path.
_PATH_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
#: A backticked ``module::symbol`` or bare ``symbol()`` reference.
_SYMBOL_RE = re.compile(r"`(?:[A-Za-z0-9_./-]+\.py)?::?([A-Za-z_][A-Za-z0-9_]*)\(?\)?`")
#: A plain backticked identifier. The underscore requirement is the heuristic
#: that separates code identifiers from ordinary backticked prose -- it admits
#: ``effective_strength`` and ``TIER_WEIGHT`` while rejecting ``route`` or
#: ``verified``, which are English words in this domain as often as symbols.
_BARE_IDENT_RE = re.compile(r"`([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)`")
#: Identifiers too generic to be worth checking (they appear in prose).
_SKIP_SYMBOLS = frozenset({
    "id", "name", "type", "status", "target", "provenance", "needs", "composes",
    "inhibits", "yields", "intent", "triggers", "plasticity", "trust", "synapses",
    "spec", "version", "does", "use_when", "not_when", "positive", "negative",
    "eph_", "declared", "learned", "derived", "distilled", "authored", "imported",
    "nascent", "draft", "probation", "consolidated", "promoted", "archived",
    "pending", "verified", "quarantined", "skill_ids", "valence", "salience",
    "session_id", "adapter_token", "min_status", "out_dir", "request_id",
})


def _engrams(registry: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(registry.glob("*.egr.md")):
        engram = parse_file(path, registry_root=registry).engram
        fm = engram.frontmatter
        note = " ".join((e.note or "") for e in fm.provenance_journal)
        out[fm.name] = {
            "path": path,
            "fm": fm,
            "tranche": 2 if TRANCHE2_MARKER in note else 1,
            "raw": path.read_text(encoding="utf-8"),
        }
    return out


def check_edges(engrams: dict[str, dict]) -> list[str]:
    failures: list[str] = []
    names = set(engrams)
    cross = 0
    total = 0

    for name, rec in sorted(engrams.items()):
        fm = rec["fm"]
        for key in EDGE_KEYS:
            for target in getattr(fm, key, []) or []:
                total += 1
                if target not in names:
                    failures.append(f"dangling {key}: {name} -> {target}")
                    continue
                if engrams[target]["tranche"] != rec["tranche"]:
                    cross += 1

    t1 = sum(1 for r in engrams.values() if r["tranche"] == 1)
    t2 = sum(1 for r in engrams.values() if r["tranche"] == 2)
    print(f"  engrams: {len(engrams)} ({t1} operational, {t2} codebase)")
    print(f"  declared edges: {total}, dangling: {len([f for f in failures if 'dangling' in f])}")
    print(f"  cross-tranche edges: {cross}")
    if cross < 3:
        failures.append(f"tranches are under-connected: {cross} cross-tranche edge(s), want >= 3")
    return failures


def check_symbols(engrams: dict[str, dict], repo_root: Path) -> list[str]:
    failures: list[str] = []
    # One pass over the tree; symbol lookup is then a substring test. `tests/`
    # is in scope deliberately: an engram about an enforced invariant names the
    # test that enforces it, and those symbols must stay real too.
    sources = {
        p: p.read_text(encoding="utf-8", errors="replace")
        for root in ("src", "tests")
        for p in (repo_root / root).rglob("*.py")
    }
    haystack = "\n".join(sources.values())
    checked_paths = 0
    checked_symbols = 0

    for name, rec in sorted(engrams.items()):
        body = rec["raw"]
        for rel in sorted(set(_PATH_RE.findall(body))):
            checked_paths += 1
            candidates = [
                repo_root / rel,
                repo_root / "src" / "magicite" / rel,
                repo_root / "src" / rel,
            ]
            if not any(c.exists() for c in candidates):
                failures.append(f"{name}: names a file that does not exist: {rel}")
        found = set(_SYMBOL_RE.findall(body)) | set(_BARE_IDENT_RE.findall(body))
        for sym in sorted(found):
            if sym in _SKIP_SYMBOLS or len(sym) < 4:
                continue
            checked_symbols += 1
            if sym not in haystack:
                failures.append(f"{name}: names a symbol absent from src/ and tests/: {sym}")

    print(f"  source files scanned: {len(sources)} (src/ + tests/)")
    print(f"  path references checked: {checked_paths}")
    print(f"  symbol references checked: {checked_symbols}")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, default=Path(".spectra/engrams"))
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--edges", action="store_true")
    ap.add_argument("--symbols", action="store_true")
    args = ap.parse_args()

    run_all = not (args.edges or args.symbols)
    engrams = _engrams(args.registry)
    if not engrams:
        print(f"no .egr.md files under {args.registry}", file=sys.stderr)
        return 1

    failures: list[str] = []
    if args.edges or run_all:
        print("declared-edge graph:")
        failures += check_edges(engrams)
    if args.symbols or run_all:
        print("code references:")
        failures += check_symbols(engrams, args.repo_root)

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
