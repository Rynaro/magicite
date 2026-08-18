"""Frozen acceptance-path aliases for registry integrity checks.

The implementation's canonical test module was renamed to
``test_registry_core`` before the 0.3 acceptance contract was frozen.  Keep
the executable contract stable by re-exporting the exact behavioural tests.
"""

from pathlib import Path
from runpy import run_path

_CANONICAL = run_path(str(Path(__file__).with_name("test_registry_core.py")))
test_invalid_authoritative_file_disables_projection = _CANONICAL[
    "test_invalid_authoritative_file_disables_projection"
]
test_sync_reconciles_removed_edges = _CANONICAL["test_sync_reconciles_removed_edges"]

__all__ = [
    "test_invalid_authoritative_file_disables_projection",
    "test_sync_reconciles_removed_edges",
]
