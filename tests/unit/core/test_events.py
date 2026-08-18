"""Frozen acceptance-path alias for durable event redaction."""

from pathlib import Path
from runpy import run_path

_CANONICAL = run_path(str(Path(__file__).parents[1] / "obs" / "test_events.py"))
test_adapter_secret_redacted_before_hashing = _CANONICAL["test_adapter_secret_redacted_before_hashing"]

__all__ = ["test_adapter_secret_redacted_before_hashing"]
