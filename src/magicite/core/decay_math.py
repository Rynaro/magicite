"""Pure decay math (spec §6.1), deliberately dependency-free.

Split out of ``core/decay.py`` so that ``core/router.py`` (a hot-path,
G1/AC-024-governed module) can decay-adjust R **at read time** -- spec
§6.1: "R ... Evaluated lazily at read time" is a statement about every
read, not just Dream's Phase-3 materialisation -- without transitively
importing ``magicite.engram.writer``/``magicite.storage.durable``
(``core/decay.py`` itself imports both, for
``archive_below_floor``'s file-move; pulling that into router.py's import
graph would defeat the point of ``tests/unit/test_p0_enforcement.py``'s
static check, even though the *runtime* G1 boundary -- the authorizer on
the connection object -- would still hold). This module has exactly one
stdlib dependency (``math``/``datetime``) and no I/O.
"""

from __future__ import annotations

import math
from datetime import datetime


def effective_value(v0: float, decayed_at: str | None, now: str, lambda_per_day: float) -> float:
    """spec §6.1: ``V(t) = V0 * e^(-lambda * dt)``, ``dt`` in days.
    ``decayed_at=None`` (never anchored) is treated as "no time has
    passed" -- the honest value for a row that has never been decayed."""
    if decayed_at is None:
        return v0
    dt_days = (datetime.fromisoformat(now) - datetime.fromisoformat(decayed_at)).total_seconds() / 86400.0
    if dt_days <= 0:
        return v0
    return v0 * math.exp(-lambda_per_day * dt_days)
