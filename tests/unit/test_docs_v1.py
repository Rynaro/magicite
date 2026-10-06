"""AC-S15-01/02/04: drift, evidence binding and deprecation negative anchors."""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_generated_docs as generated  # noqa: E402
import docs_v1_contracts as contracts  # noqa: E402


@pytest.mark.parametrize("key", ["version", "engram_schemas", "mcp_tools", "cli", "config_defaults"])
def test_runtime_snapshot_drift_rejected(tmp_path: Path, key: str) -> None:
    snapshot = generated.runtime_reference()
    snapshot.pop(key)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot))
    assert any(key in error for error in generated.check(path))


def test_missing_runtime_snapshot_rejected(tmp_path: Path) -> None:
    assert generated.check(tmp_path / "absent.json")


def test_current_generated_snapshot_matches() -> None:
    assert generated.check() == []


@pytest.mark.parametrize("text", [
    "Routing latency is 10 ms.", "99 percent accuracy", "2× faster",
    "Supports 10,000 artifacts", "Accuracy reaches 99%.", "Hit@1 is 0.99",
])
def test_unmanifested_readme_claim_rejected(tmp_path: Path, text: str) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(text)
    (tmp_path / "docs/readme-claims.json").write_text('{"schema":"magicite/readme-claims/1","claims":[]}')
    assert contracts.check_readme_claims(tmp_path)


def _bundle() -> dict:
    # Reuse the independent S01 test's sealed fixture construction, never a release claim.
    ns = runpy.run_path(str(ROOT / "tests/unit/eval/test_manifests.py"))
    corpus = ns["_corpus_from_offline"]()
    experiment = ns["_seal"](ns["_experiment"](
        corpus_sha256=corpus.content_identity_sha256, labels_sha256=corpus.content_identity_sha256))
    predictions = ns["run_predictions"](experiment, corpus)
    result = ns["build_result_manifest"](result_id="docs-fixture/1", experiment=experiment,
                                        predictions=predictions, aggregates={"hit_at_1": 0.5})
    claim = ns["Claim"](claim_id="fixture", text_location="README.md#claim:fixture",
                        metric="hit_at_1", value=0.5, unit="fraction", population_split="final",
                        result_digest=result.digest(), confidence_interval=None,
                        evidence_class="retrieval", status="supported", limitations="test fixture")
    return {"claim": claim.to_dict(), "result": result.to_dict(),
            "predictions": [p.to_dict() for p in predictions],
            "experiment": experiment.to_dict(), "corpus": corpus.to_dict(),
            "current_labels_sha256": experiment.labels_sha256}


@pytest.mark.parametrize("mutation", [None, "value", "metric", "scope", "missing_predictions", "historical"])
def test_readme_claim_uses_full_integrity_chain(tmp_path: Path, mutation: str | None) -> None:
    (tmp_path / "docs").mkdir()
    bundle = _bundle()
    (tmp_path / "README.md").write_text('<!-- claim:fixture --> hit_at_1: 0.5 fraction (final).\n')
    if mutation == "value":
        bundle["claim"]["value"] = 0.99
    elif mutation == "metric":
        bundle["claim"]["metric"] = "made_up_accuracy"
    elif mutation == "scope":
        bundle["claim"]["population_split"] = "everyone"
    elif mutation == "missing_predictions":
        bundle.pop("predictions")
    elif mutation == "historical":
        bundle["claim"]["evidence_class"] = "historical"
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    (tmp_path / "docs/readme-claims.json").write_text(json.dumps(
        {"schema":"magicite/readme-claims/1", "claims":[{"bundle":"bundle.json"}]}))
    errors = contracts.check_readme_claims(tmp_path)
    assert bool(errors) == (mutation is not None), errors


@pytest.mark.parametrize("missing", ["replacement", "support_deadline"])
def test_support_rows_require_replacement_and_deadline(tmp_path: Path, missing: str) -> None:
    (tmp_path / "docs").mkdir()
    policy = json.loads((ROOT / "docs/support-policy.json").read_text())
    del policy["deprecations"][0][missing]
    (tmp_path / "docs/support-policy.json").write_text(json.dumps(policy))
    assert contracts.check_support(tmp_path)


def test_short_deprecation_window_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    policy = json.loads((ROOT / "docs/support-policy.json").read_text())
    policy["deprecation_window"]["minimum_days"] = 89
    (tmp_path / "docs/support-policy.json").write_text(json.dumps(policy))
    assert contracts.check_support(tmp_path)


@pytest.mark.parametrize(
    "order",
    [
        ["bind_inspect", "bind_registry"],
        ["bind_registry", "bind_inspect"],
    ],
)
def test_generated_reference_ignores_fresh_binding_import_order(order):
    import subprocess

    code = (
        "import importlib,sys; "
        + "; ".join(f"importlib.import_module('magicite.mcp.{name}')" for name in order)
        + "; sys.path.insert(0,'scripts'); import check_generated_docs as g; "
        "from magicite.mcp.registry import registered_names; "
        "before=registered_names(); errors=g.check(); "
        "assert registered_names()==before; assert len(before)==len(set(before))==16; "
        "assert errors==[],errors"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field", ["risk_class", "input_schema_sha256", "output_schema_sha256", "name"])
def test_tool_reference_content_drift_remains_rejected(tmp_path, field):
    snapshot = generated.runtime_reference()
    snapshot["mcp_tools"][0][field] = "changed-canary"
    path = tmp_path / "changed-reference.json"
    path.write_text(json.dumps(snapshot))
    assert "runtime reference drift: mcp_tools" in generated.check(path)
