"""Internal evaluation integrity binding. Never an operator approval or authority."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from magicite.config import Config
from magicite.core import calibration, routing_policy
from magicite.core.comparison_budget import ComparisonBudget
from magicite.errors import InvalidInputError


@dataclass(frozen=True)
class EvaluationContext:
    policy_id: str
    config_digest: str
    base_policy_digest: str
    rank_depth: int
    budget_json: str | None
    artifact_json: str | None = None

    @classmethod
    def bind(
        cls,
        cfg: Config,
        policy_id: str,
        rank_depth: int,
        budget: ComparisonBudget | None,
        artifact: calibration.CalibrationArtifact | None = None,
    ) -> EvaluationContext:
        selected = replace(cfg, routing_policy=policy_id)
        return cls(
            policy_id,
            routing_policy.compute_config_digest(selected),
            routing_policy.compute_policy_digest(policy_id, selected),
            rank_depth,
            json.dumps(budget.to_dict(), sort_keys=True) if budget else None,
            json.dumps(artifact.to_dict(), sort_keys=True) if artifact else None,
        )

    def validate(
        self, cfg: Config, k: int, budget: ComparisonBudget | None
    ) -> tuple[Config, calibration.CalibrationArtifact | None]:
        selected = replace(cfg, routing_policy=self.policy_id)
        if (
            self.policy_id not in routing_policy.KNOWN_POLICY_IDS
            or type(self.rank_depth) is not int
            or k != self.rank_depth
            or k < 1
            or cfg.reranker_provider
            or not cfg.abstention_enabled
            or self.config_digest != routing_policy.compute_config_digest(selected)
            or self.base_policy_digest != routing_policy.compute_policy_digest(self.policy_id, selected)
            or self.budget_json != (json.dumps(budget.to_dict(), sort_keys=True) if budget else None)
        ):
            raise InvalidInputError("incompatible internal evaluation context")
        if budget and (
            budget.output_k != k
            or self.policy_id not in (*routing_policy.BASELINE_SOURCES, routing_policy.POLICY_DENSE_V1)
        ):
            raise InvalidInputError("unsupported evaluation comparison arm")
        artifact = (
            calibration.CalibrationArtifact.from_dict(json.loads(self.artifact_json))
            if self.artifact_json
            else None
        )
        if artifact is not None and (
            artifact.policy_id != self.policy_id
            or artifact.policy_digest != self.base_policy_digest
            or artifact.config_digest != self.config_digest
            or artifact.evaluation_budget_digest != (budget.digest if budget else None)
            or (artifact.probability_model is not None and artifact.probability_model["rank_depth"] != k)
        ):
            raise InvalidInputError("incompatible evaluation calibration artifact")
        return selected, artifact
