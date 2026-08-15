"""AC-024: hot-path modules must never import the durable-write path.

Full P0 enforcement (the SQLite authorizer G1, the lease assertion G2)
lands in M1/M3; this AST-level static check is cheap to prove from M0
onward since ``core/router.py`` already exists, so it is proven here
rather than deferred.

Also AC-040 (DECLARED-EDGES-AMENDED, 2026-08-15, spec §3.3.1): no module
outside ``core.edge_weight`` may derive an edge routing weight from
``edge.storage_strength`` without going through
``core.edge_weight.effective_strength`` -- the guard that keeps the
three-independent-workarounds pattern this amendment fixed (router.py's
hub-penalty comment, registry.py's since-deleted
``_COMMUNITY_WEIGHT_FLOOR``, bench.py baseline (c)) from recurring.
"""

from __future__ import annotations

import ast
import re
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


# ── AC-040: the edge-weight helper is the only weighting site ───────────────

_EDGE_WEIGHT_MODULE = "core/edge_weight.py"

#: decisions/DECLARED-EDGES-AMENDED.md §4.2/§4.4: modules that legitimately
#: read or write ``storage_strength`` WITHOUT deriving an edge *routing*
#: weight from it -- either because they operate on the NODE-level
#: ``engram.storage_strength`` (a distinct Hebbian value, spec §4.3's node
#: plasticity, not the edge channel §3.3.1 is about) or because they ARE
#: the one legitimate writer/reporter of the edge's LEARNED channel:
#: Dream's potentiate/prune/renormalise phases ("NOT CHANGED: they read
#: the learned column, which is exactly what they should read", §3.3.1
#: call-site table), the durable-write layer, the read-model that reports
#: both channels verbatim (never derives a weight from them, AC-041), and
#: the file-format layer that round-trips the same raw value.
_EDGE_WEIGHT_ALLOWLIST = {
    "core/dream.py",
    "core/decay.py",
    "core/decay_math.py",
    "core/lifecycle.py",
    "core/fitness.py",
    "core/plasticity.py",
    "obs/kpi.py",
    "storage/durable.py",
    "storage/queries.py",
    "engram/model.py",
    "engram/writer.py",
}

#: word-boundary match that does not also match ``peak_storage_strength``
#: (a *different*, node-level column that happens to contain this
#: substring) -- there is no `\b` between `_` and `s`, so a plain
#: `\bstorage_strength\b` search already excludes it correctly; kept
#: explicit here for clarity, not because it is load-bearing.
_STORAGE_STRENGTH_RE = re.compile(r"(?<!peak_)\bstorage_strength\b")


def _mentions_storage_strength(text: str) -> bool:
    return bool(_STORAGE_STRENGTH_RE.search(text))


def _routes_through_edge_weight_helper(text: str) -> bool:
    # covers both effective_strength() and effective_strength_no_learned().
    return "effective_strength" in text


def test_edge_weight_helper_is_the_only_weighting_site() -> None:
    """AC-040: THEN no module under ``src/magicite/`` outside
    ``magicite.core.edge_weight`` SHALL derive an edge routing weight from
    ``edge.storage_strength`` without calling ``effective_strength``.

    Mechanical AST check, same shape as :func:`test_forbidden_imports`:
    flags a multiplication combining ``storage_strength`` with
    ``type_gain`` (the ``W_ij = S_edge * type_gain[type]`` pattern this
    amendment replaced) or a ``max()``/``sum()`` aggregate over
    ``storage_strength`` (the ``_COMMUNITY_WEIGHT_FLOOR`` /
    ``mean(S_edge)`` patterns it deleted), in any file not in the
    allowlist above, unless the same expression also calls into the
    ``effective_strength`` helper family.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relpath = str(path.relative_to(SRC))
        if relpath == _EDGE_WEIGHT_MODULE or relpath in _EDGE_WEIGHT_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                text = ast.unparse(node)
                if (
                    _mentions_storage_strength(text)
                    and "type_gain" in text
                    and not _routes_through_edge_weight_helper(text)
                ):
                    offenders.append(f"{relpath}: {text}")
            elif isinstance(node, ast.Call):
                func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if func_name in ("max", "sum"):
                    text = ast.unparse(node)
                    if _mentions_storage_strength(text) and not _routes_through_edge_weight_helper(text):
                        offenders.append(f"{relpath}: {text}")
    message = "edge routing weight derived from storage_strength outside core.edge_weight: "
    assert not offenders, message + "; ".join(offenders)
