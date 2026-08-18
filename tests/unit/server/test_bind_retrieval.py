"""Frozen acceptance-path alias for body-cursor retrieval."""

from pathlib import Path
from runpy import run_path

_CANONICAL = run_path(str(Path(__file__).parents[1] / "mcp" / "test_bind_retrieval.py"))
test_load_skill_body_cursor_round_trip = _CANONICAL["test_load_skill_body_cursor_round_trip"]

__all__ = ["test_load_skill_body_cursor_round_trip"]
