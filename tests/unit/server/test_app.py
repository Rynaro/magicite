"""Frozen acceptance-path alias for server idempotency behaviour."""

from pathlib import Path
from runpy import run_path

_CANONICAL = run_path(str(Path(__file__).parents[1] / "mcp" / "test_idempotency.py"))
test_expired_idempotency_key_executes_again = _CANONICAL["test_expired_idempotency_key_executes_again"]

__all__ = ["test_expired_idempotency_key_executes_again"]
