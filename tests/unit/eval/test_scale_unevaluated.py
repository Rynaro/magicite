"""S14 scale / UNEVALUATED catalog / composition structural harness."""

from __future__ import annotations

from pathlib import Path

from magicite.core.composition import Plan, PlanDiagnostic
from magicite.eval.composition_eval import (
    evaluate_composition_corpus,
    evaluate_structural_case,
    load_composition_v1_corpus,
)
from magicite.eval.external import official_skillret_status
from magicite.eval.unevaluated import UNEVALUATED_CATALOG, unevaluated_catalog


def _plan(
    *,
    status: str,
    order: tuple[str, ...] = (),
    diagnostics: tuple[PlanDiagnostic, ...] = (),
    executable: bool = False,
) -> Plan:
    return Plan(
        status=status,  # type: ignore[arg-type]
        nodes=(),
        edges=(),
        topological_order=order,
        supplied_capabilities=(),
        missing_capabilities=(),
        diagnostics=diagnostics,
        snapshot_id="s",
        policy_id="dense-v1",
        policy_digest="d",
        executable=executable,
    )


def test_unevaluated_catalog_has_operator_commands() -> None:
    catalog = unevaluated_catalog()
    assert len(catalog) == len(UNEVALUATED_CATALOG)
    for item in catalog:
        assert item["status"] == "UNEVALUATED"
        assert item["operator_command"]
        assert item["manifest_or_digest"]
        # Never fabricate a pass flag.
        assert item.get("passed") is not True


def test_official_skillret_status_is_unevaluated() -> None:
    status = official_skillret_status()
    assert status["status"] == "UNEVALUATED"
    assert "arxiv" in status["source_url"]
    assert status["operator_command"]


def test_composition_v1_structural_loader() -> None:
    path = Path("tests/fixtures/composition-v1/structural-corpus.json")
    corpus = load_composition_v1_corpus(path)
    assert corpus["corpus_id"] == "composition-v1-structural"
    assert len(corpus["cases"]) >= 5


def test_structural_case_valid_and_invalid() -> None:
    valid_plan = _plan(
        status="valid",
        order=("egr_a0000001", "egr_b0000001"),
        executable=True,
    )
    case = {
        "id": "producer-before-consumer",
        "accepted_orders": [["egr_a0000001", "egr_b0000001"]],
    }
    outcome = evaluate_structural_case(case, valid_plan)
    assert outcome.order_accepted is True

    invalid_plan = _plan(
        status="invalid",
        diagnostics=(PlanDiagnostic(code="cycle", message="cycle"),),
    )
    invalid_case = {
        "id": "cycle-no-executable-prefix",
        "expect_status": "invalid",
        "expect_codes": ["cycle"],
        "expect_empty_order": True,
    }
    inv = evaluate_structural_case(invalid_case, invalid_plan)
    assert inv.codes_match is True
    assert inv.actual_status == "invalid"


def test_structural_report_forbids_efficacy_claim() -> None:
    corpus = {
        "corpus_id": "toy",
        "cases": [{"id": "c1", "accepted_orders": [["a"]]}],
    }
    report = evaluate_composition_corpus(corpus, {"c1": _plan(status="valid", order=("a",), executable=True)})
    assert report.evidence_class == "structural"
    assert report.to_dict()["structural_efficacy_claim_allowed"] is False
    assert report.n_pass == 1
