"""Paired host-task evaluation (evaluation.md E4 / AC-S14-04).

Compares no-skill / selected-skill / composed-plan arms on the same tasks
with deterministic host verifiers. Structural corpus validity is never
substituted for verified end-task pass rates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from magicite.eval.metrics import BootstrapInterval, paired_bootstrap_ci
from magicite.eval.verdicts import usefulness_verdict

ArmName = Literal["no_skill", "selected_skill", "composed_plan"]
ArmOutcome = Literal["pass", "fail", "unevaluated", "timeout"]


@dataclass(frozen=True)
class HostTaskArmResult:
    task_id: str
    group_id: str
    arm: ArmName
    outcome: ArmOutcome
    verifier_id: str
    verifier_artifact_digest: str
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "group_id": self.group_id,
            "arm": self.arm,
            "outcome": self.outcome,
            "verifier_id": self.verifier_id,
            "verifier_artifact_digest": self.verifier_artifact_digest,
            "details": self.details,
        }

    @property
    def scored_pass(self) -> float | None:
        """Intention-to-test: unevaluated/timeout stay null (not silent remove)."""
        if self.outcome == "pass":
            return 1.0
        if self.outcome == "fail":
            return 0.0
        return None


@dataclass(frozen=True)
class PairedHostTaskReport:
    arms: tuple[HostTaskArmResult, ...]
    usefulness_delta_interval: BootstrapInterval | None
    usefulness_status: str
    evidence_class: str = "host-task"

    def to_dict(self) -> dict[str, Any]:
        return {
            "arms": [a.to_dict() for a in self.arms],
            "usefulness_delta_interval": (
                None
                if self.usefulness_delta_interval is None
                else self.usefulness_delta_interval.to_dict()
            ),
            "usefulness_status": self.usefulness_status,
            "evidence_class": self.evidence_class,
            "has_paired_host_verifier_outcomes": True,
        }


def _pass_rate_by_group(
    arms: list[HostTaskArmResult],
    arm: ArmName,
) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for row in arms:
        if row.arm != arm:
            continue
        score = row.scored_pass
        if score is None:
            continue
        buckets.setdefault(row.group_id, []).append(score)
    return {gid: sum(vals) / len(vals) for gid, vals in buckets.items() if vals}


def paired_usefulness_delta(
    arms: list[HostTaskArmResult],
    *,
    treatment: ArmName = "composed_plan",
    control: ArmName = "no_skill",
    n_resamples: int = 10_000,
    seed: int = 0,
) -> BootstrapInterval | None:
    """Paired group bootstrap of treatment−control pass-rate delta."""
    treatment_rates = _pass_rate_by_group(arms, treatment)
    control_rates = _pass_rate_by_group(arms, control)
    shared = sorted(set(treatment_rates) & set(control_rates))
    if not shared:
        return None
    group_ids = list(shared)
    candidate = [treatment_rates[g] for g in group_ids]
    incumbent = [control_rates[g] for g in group_ids]
    return paired_bootstrap_ci(
        group_ids,
        candidate,
        incumbent,
        n_resamples=n_resamples,
        seed=seed,
    )


def evaluate_paired_host_tasks(
    arms: list[HostTaskArmResult],
    *,
    n_resamples: int = 10_000,
    seed: int = 0,
    min_groups: int = 30,
) -> PairedHostTaskReport:
    """Build the paired usefulness report; never fabricates PASS from structure."""
    interval = paired_usefulness_delta(arms, n_resamples=n_resamples, seed=seed)
    if interval is None:
        status = "unevaluated"
    else:
        status = usefulness_verdict(interval, min_groups=min_groups).status
    return PairedHostTaskReport(
        arms=tuple(arms),
        usefulness_delta_interval=interval,
        usefulness_status=status,
    )


__all__ = [
    "ArmName",
    "ArmOutcome",
    "HostTaskArmResult",
    "PairedHostTaskReport",
    "evaluate_paired_host_tasks",
    "paired_usefulness_delta",
]
