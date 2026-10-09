"""Evaluation-only candidate allowance. It never changes ordinary configuration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ComparisonBudget:
    per_source_scan_limit: int
    per_source_candidate_limit: int
    total_source_scan_allowance: int
    output_k: int
    refill_limit: int
    reranker_allowance: int = 0
    schema: str = "ComparisonBudget/1"

    def __post_init__(self) -> None:
        for key in (
            "per_source_scan_limit",
            "per_source_candidate_limit",
            "total_source_scan_allowance",
            "output_k",
            "refill_limit",
        ):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError("comparison allowance must use positive integers")
        if (
            self.schema != "ComparisonBudget/1"
            or type(self.reranker_allowance) is not int
            or self.reranker_allowance != 0
            or self.per_source_scan_limit > 1000
            or self.per_source_candidate_limit > self.per_source_scan_limit
            or self.total_source_scan_allowance < 2 * self.per_source_scan_limit
            or self.output_k > self.per_source_candidate_limit
        ):
            raise ValueError("unsupported comparison allowance")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> ComparisonBudget:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("comparison allowance fields differ")
        return cls(**value)

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def comparison_config_digest(cfg) -> str:
    """Normalize only the declared comparison arm; preserve all other route knobs."""
    from dataclasses import replace

    from magicite.core.routing_policy import compute_config_digest

    return compute_config_digest(replace(cfg, routing_policy="dense-v1"))
