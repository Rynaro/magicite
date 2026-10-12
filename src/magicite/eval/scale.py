"""Process RSS and percentile helpers for scale measurements (evaluation.md E6)."""

from __future__ import annotations

import os
from typing import Any

import numpy as np


def process_rss_gib() -> float:
    """Best-effort RSS in GiB (POSIX ``ru_maxrss`` / Linux smaps)."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports KiB.
        if os.uname().sysname == "Darwin":
            return float(usage) / (1024.0**3)
        return float(usage) * 1024.0 / (1024.0**3)
    except Exception:  # noqa: BLE001 — measurement optional in CI smoke
        return 0.0


def latency_percentiles_ms(durations_s: list[float]) -> dict[str, float]:
    if not durations_s:
        return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    arr = np.asarray(durations_s, dtype=np.float64) * 1000.0
    return {
        "n": int(arr.size),
        "p50_ms": round(float(np.percentile(arr, 50)), 3),
        "p95_ms": round(float(np.percentile(arr, 95)), 3),
        "p99_ms": round(float(np.percentile(arr, 99)), 3),
        "max_ms": round(float(np.max(arr)), 3),
    }


def path_size_gib(path: Any) -> float:
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return 0.0
    if p.is_file():
        return p.stat().st_size / (1024.0**3)
    total = 0
    for child in p.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total / (1024.0**3)


__all__ = ["latency_percentiles_ms", "path_size_gib", "process_rss_gib"]
