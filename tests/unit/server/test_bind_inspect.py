"""Frozen acceptance-path alias for consolidation introspection."""

from pathlib import Path
from runpy import run_path

_CANONICAL = run_path(str(Path(__file__).parents[1] / "mcp" / "test_bind_inspect.py"))
test_introspect_consolidation = _CANONICAL["test_introspect_consolidation"]

__all__ = ["test_introspect_consolidation"]
