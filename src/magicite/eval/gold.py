"""Independent gold / structural label loading (evaluation.md E1, E4).

Expected plans and selection labels originate only from authored corpus
annotations. This module MUST NOT call production ``composition.expand``
(or any planner) while loading gold — that circular path is the defect
AC-S01-04 closes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StructuralGoldCase:
    case_id: str
    winner: str
    expected_plan: tuple[str, ...]
    expected_confidence: float | None
    accepted_plans: tuple[tuple[str, ...], ...]
    label_provenance: dict[str, Any]


@dataclass(frozen=True)
class StructuralGold:
    corpus_id: str
    label_policy: dict[str, Any]
    cases: tuple[StructuralGoldCase, ...]

    def expected_plans_by_winner(self) -> dict[str, list[str]]:
        """Map winner name -> first authored expected plan.

        Multiple cases may share a winner; the first annotation wins and
        collisions with differing plans raise at load time.
        """
        out: dict[str, list[str]] = {}
        for case in self.cases:
            plan = list(case.expected_plan)
            prior = out.get(case.winner)
            if prior is not None and prior != plan:
                raise ValueError(f"conflicting expected_plan annotations for winner {case.winner!r}")
            out[case.winner] = plan
        return out

    def expected_plans_by_case_id(self) -> dict[str, list[str]]:
        return {case.case_id: list(case.expected_plan) for case in self.cases}


def _require_independent_provenance(provenance: Any, locus: str, errors: list[str]) -> None:
    if not isinstance(provenance, dict):
        errors.append(f"{locus} must be an object")
        return
    if provenance.get("production_expansion_used") is not False:
        errors.append(f"{locus}.production_expansion_used must be false")
    if provenance.get("reviewed") is not True:
        errors.append(f"{locus}.reviewed must be true")


def load_structural_gold(path: Path | str) -> StructuralGold:
    """Load independently authored structural expected plans from a corpus.

    Reads only JSON annotation fields. Never imports or calls
    ``magicite.core.composition``.
    """
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ValueError("structural corpus must be an object")
    schema = data.get("schema")
    if schema not in {"magicite-composition-corpus/1", "magicite-structural-gold/1"}:
        raise ValueError(
            "structural corpus schema must be magicite-composition-corpus/1 or magicite-structural-gold/1"
        )
    policy = data.get("label_policy")
    if not isinstance(policy, dict):
        raise ValueError("label_policy must be an object")
    if policy.get("production_expansion_used") is not False:
        raise ValueError("label_policy.production_expansion_used must be false")

    cases_raw = data.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError("cases must be a non-empty array")

    cases: list[StructuralGoldCase] = []
    for index, case in enumerate(cases_raw):
        locus = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{locus} must be an object")
            continue
        case_id = case.get("id")
        winner = case.get("winner")
        expected = case.get("expected_plan")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{locus}.id must be a non-empty string")
            continue
        if not isinstance(winner, str) or not winner:
            errors.append(f"{locus}.winner must be a non-empty string")
            continue
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(name, str) and name for name in expected)
        ):
            errors.append(f"{locus}.expected_plan must be a non-empty string array")
            continue
        provenance = case.get("label_provenance")
        _require_independent_provenance(provenance, f"{locus}.label_provenance", errors)
        accepted = case.get("accepted_plans") or [expected]
        if not isinstance(accepted, list) or not accepted:
            errors.append(f"{locus}.accepted_plans must be a non-empty array when present")
            continue
        accepted_plans = tuple(tuple(str(name) for name in plan) for plan in accepted)
        conf = case.get("expected_confidence")
        cases.append(
            StructuralGoldCase(
                case_id=case_id,
                winner=winner,
                expected_plan=tuple(str(name) for name in expected),
                expected_confidence=float(conf) if isinstance(conf, (int, float)) else None,
                accepted_plans=accepted_plans,
                label_provenance=dict(provenance) if isinstance(provenance, dict) else {},
            )
        )

    if errors:
        raise ValueError("; ".join(errors))

    corpus_id = str(data.get("corpus_id") or Path(path).stem)
    return StructuralGold(
        corpus_id=corpus_id,
        label_policy=dict(policy),
        cases=tuple(cases),
    )


def load_selection_labels(path: Path | str) -> dict[str, str]:
    """Load query_id -> expected selection labels from a JSONL or corpus file.

    JSONL rows: ``{"query_id"|"query", "expected_top1"|"selection"}``.
    Does not invoke any planner.
    """
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if text.startswith("{"):
        data = json.loads(text)
        if isinstance(data, dict) and "queries" in data:
            out: dict[str, str] = {}
            for item in data["queries"]:
                qid = str(item.get("query_id") or item.get("query"))
                label = item.get("expected_top1") or item.get("selection")
                if not qid or not isinstance(label, str) or not label:
                    raise ValueError(f"invalid selection label row: {item!r}")
                if qid in out and out[qid] != label:
                    raise ValueError(f"conflicting selection labels for {qid!r}")
                out[qid] = label
            return out
    out = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        qid = str(row.get("query_id") or row.get("query") or f"line-{line_no}")
        label = row.get("expected_top1") or row.get("selection")
        if not isinstance(label, str) or not label:
            raise ValueError(f"line {line_no}: missing expected_top1/selection")
        if qid in out and out[qid] != label:
            raise ValueError(f"conflicting selection labels for {qid!r}")
        out[qid] = label
    return out
