"""Structural composition evaluation harness (evaluation.md E4 / S08).

Evaluates ``compose()`` Plan/1 against independently authored structural
labels. Never treats structural validity as end-task usefulness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magicite.core.composition import Plan


@dataclass(frozen=True)
class StructuralCaseOutcome:
    case_id: str
    expected_status: str
    actual_status: str
    order_accepted: bool | None
    codes_match: bool | None
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "expected_status": self.expected_status,
            "actual_status": self.actual_status,
            "order_accepted": self.order_accepted,
            "codes_match": self.codes_match,
            "details": self.details,
        }


@dataclass(frozen=True)
class StructuralEvalReport:
    corpus_id: str
    outcomes: tuple[StructuralCaseOutcome, ...]
    n_pass: int
    n_fail: int
    evidence_class: str = "structural"

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "evidence_class": self.evidence_class,
            "structural_efficacy_claim_allowed": False,
        }


def load_composition_v1_corpus(path: Path | str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("composition corpus must be an object")
    schema = data.get("schema")
    if schema not in {
        "magicite/composition-v1-corpus/1",
        "magicite-composition-corpus/1",
        "magicite-structural-gold/1",
    }:
        raise ValueError(f"unsupported composition corpus schema {schema!r}")
    return data


def evaluate_structural_case(
    case: dict[str, Any],
    plan: Plan,
) -> StructuralCaseOutcome:
    case_id = str(case["id"])
    expect_status = str(case.get("expect_status") or "valid")
    actual_status = plan.status
    order_accepted: bool | None = None
    codes_match: bool | None = None
    details = ""

    if expect_status == "valid":
        accepted = case.get("accepted_orders") or []
        if plan.status != "valid":
            return StructuralCaseOutcome(
                case_id=case_id,
                expected_status=expect_status,
                actual_status=actual_status,
                order_accepted=False,
                codes_match=None,
                details="expected valid plan",
            )
        order = list(plan.topological_order)
        order_accepted = any(order == list(candidate) for candidate in accepted)
        if case.get("expect_empty_order") is True:
            order_accepted = len(order) == 0
        if not order_accepted:
            details = f"order {order!r} not in accepted_orders"
    else:
        expect_codes = set(case.get("expect_codes") or [])
        actual_codes = {d.code for d in plan.diagnostics}
        codes_match = expect_codes.issubset(actual_codes) if expect_codes else True
        if case.get("expect_empty_order") is True and plan.topological_order:
            codes_match = False
            details = "expected empty order for invalid plan"
        if plan.status != "invalid":
            codes_match = False
            details = "expected invalid status"

    passed = (
        (expect_status == "valid" and order_accepted is True)
        or (expect_status != "valid" and codes_match is True and actual_status == "invalid")
    )
    if not passed and not details:
        details = "structural mismatch"
    return StructuralCaseOutcome(
        case_id=case_id,
        expected_status=expect_status,
        actual_status=actual_status,
        order_accepted=order_accepted,
        codes_match=codes_match,
        details=details if not passed else "",
    )


def evaluate_composition_corpus(
    corpus: dict[str, Any],
    plans_by_case_id: dict[str, Plan],
) -> StructuralEvalReport:
    outcomes: list[StructuralCaseOutcome] = []
    for case in corpus.get("cases") or []:
        case_id = str(case["id"])
        plan = plans_by_case_id.get(case_id)
        if plan is None:
            outcomes.append(
                StructuralCaseOutcome(
                    case_id=case_id,
                    expected_status=str(case.get("expect_status") or "valid"),
                    actual_status="missing",
                    order_accepted=False,
                    codes_match=False,
                    details="no plan supplied for case",
                )
            )
            continue
        outcomes.append(evaluate_structural_case(case, plan))
    n_pass = sum(
        1
        for o in outcomes
        if (o.expected_status == "valid" and o.order_accepted is True)
        or (o.expected_status != "valid" and o.codes_match is True and o.actual_status == "invalid")
    )
    return StructuralEvalReport(
        corpus_id=str(corpus.get("corpus_id") or "unknown"),
        outcomes=tuple(outcomes),
        n_pass=n_pass,
        n_fail=len(outcomes) - n_pass,
    )


__all__ = [
    "StructuralCaseOutcome",
    "StructuralEvalReport",
    "evaluate_composition_corpus",
    "evaluate_structural_case",
    "load_composition_v1_corpus",
]
