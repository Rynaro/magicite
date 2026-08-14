"""AC-024: hot-path modules must never import the durable-write path.

Full P0 enforcement (the SQLite authorizer G1, the lease assertion G2)
lands in M1/M3; this AST-level static check is cheap to prove from M0
onward since ``core/router.py`` already exists, so it is proven here
rather than deferred.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "magicite"

FORBIDDEN_MODULES = {"magicite.storage.durable", "magicite.engram.writer"}

#: spec §6.2: these modules MUST NOT import the durable-write path.
HOT_PATH_MODULES = [
    "mcp/bind_retrieval.py",
    "mcp/bind_signals.py",
    "core/router.py",
    "core/signals.py",
]


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


def test_forbidden_imports() -> None:
    for relpath in HOT_PATH_MODULES:
        path = SRC / relpath
        if not path.is_file():
            continue  # module not built yet at this milestone (e.g. core/signals.py lands in M3)
        imported = _imported_module_names(path)
        offenders = imported & FORBIDDEN_MODULES
        assert not offenders, f"{relpath} imports forbidden module(s): {offenders}"
