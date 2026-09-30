"""S16 frozen gate anchors: absent or unsubstantiated evidence never authorizes GA."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_validator", ROOT / "scripts/check_release_manifest.py")
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def artifact(root, name, payload):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def bind_report(root, row, schema):
    payload = {key: value for key, value in row.items() if key != "artifact"}
    payload["schema"] = schema
    row["artifact"] = artifact(root, row["artifact"]["path"], payload)


@pytest.fixture
def candidate(tmp_path):
    """Synthetic complete release; this fixture is not product qualification."""
    source = "a" * 40
    lock = artifact(tmp_path, "uv.lock", {"synthetic": True})
    gates = []
    for gate, obligations in validator.OBLIGATIONS.items():
        evidence = []
        for obligation in obligations:
            evidence.append(
                artifact(
                    tmp_path,
                    f"reports/{gate}-{obligation}.json",
                    {
                        "schema": "magicite/release-witness/1",
                        "gate": gate,
                        "obligation": obligation,
                        "status": "PASS",
                        "source_commit": source,
                        "reviewer": "independent-reviewer",
                        "producer": "builder",
                        "command": "synthetic test fixture",
                        "environment": {"synthetic": True},
                        "lock": lock,
                        "artifacts": [lock],
                    },
                )
            )
        gates.append({"id": gate, "status": "PASS", "reason": "synthetic", "evidence": evidence})
    matrix = list(validator.MATRIX)
    rcs = [
        {
            "source_dirty": False,
            "id": f"rc{n}",
            "builder": f"builder{n}",
            "source_commit": source,
            "artifact": artifact(tmp_path, f"rc{n}.json", {"rc": n}),
            "fingerprints": {"api": "b" * 64, "schema": "c" * 64},
            "matrix": {row: "PASS" for row in matrix},
        }
        for n in (1, 2)
    ]
    for row in rcs:
        row["build_artifact"] = artifact(tmp_path, row["id"] + "-build.json", {"build": row["id"]})
        bind_report(tmp_path, row, "magicite/rc-build-report/1")
    external = {
        "path": "reproduction",
        "reports": [
            {
                "status": "PASS",
                "independent": True,
                "operator": "external-person",
                "reviewer": "independence-reviewer",
                "authors": ["builder", "label-author"],
                "source_commit": source,
                "artifact": artifact(tmp_path, "external.json", {"immutable": True}),
                "checks": {key: True for key in validator.REPRODUCTION_CHECKS},
            }
        ],
    }
    bind_report(tmp_path, external["reports"][0], "magicite/external-reproduction/1")
    result = {
        "schema": "magicite/release-manifest/1",
        "source_dirty": False,
        "source_commit": source,
        "gates": gates,
        "release_candidates": rcs,
        "external": external,
        "maintainer_signoff": {
            "identity": "maintainer",
            "source_commit": source,
            "approved": True,
            "artifact": artifact(tmp_path, "signoff.json", {"approved": True}),
        },
    }

    bind_report(tmp_path, result["maintainer_signoff"], "magicite/maintainer-signoff/1")
    return result


def test_complete_synthetic_candidate(candidate, tmp_path):
    assert validator.validate(candidate, tmp_path)["eligible"] is True


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "unknown", "waived", "unsupported", "lowercase"]
)
def test_gate_set_fail_closed(candidate, tmp_path, mutation):
    if mutation == "missing":
        candidate["gates"].pop()
    elif mutation == "duplicate":
        candidate["gates"].append(copy.deepcopy(candidate["gates"][0]))
    elif mutation == "unknown":
        candidate["gates"][0]["id"] = "INVENTED"
    elif mutation == "unsupported":
        candidate["gates"][0]["evidence"] = []
    else:
        candidate["gates"][0]["status"] = "WAIVED" if mutation == "waived" else "pass"
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize(
    "mutation", ["bytes", "missing", "escape", "wrong_obligation", "self_review", "wrong_head"]
)
def test_evidence_fail_closed(candidate, tmp_path, mutation):
    ref = candidate["gates"][0]["evidence"][0]
    path = tmp_path / ref["path"]
    if mutation == "bytes":
        path.write_text("changed")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "escape":
        ref["path"] = "../outside.json"
    else:
        report = json.loads(path.read_text())
        if mutation == "wrong_obligation":
            report["obligation"] = "invented"
        elif mutation == "self_review":
            report["reviewer"] = report["producer"]
        else:
            report["source_commit"] = "d" * 40
        ref.update(artifact(tmp_path, ref["path"], report))
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize(
    "mutation", ["fingerprint", "same_artifact", "same_builder", "missing_matrix", "failed_matrix"]
)
def test_rc_pair_requires_fresh_compatible_independent_builds(candidate, tmp_path, mutation):
    first, second = candidate["release_candidates"]
    if mutation == "fingerprint":
        second["fingerprints"]["schema"] = "d" * 64
    elif mutation == "same_artifact":
        second["artifact"] = first["artifact"]
    elif mutation == "same_builder":
        second["builder"] = first["builder"]
    elif mutation == "missing_matrix":
        second["matrix"].pop(next(iter(second["matrix"])))
    else:
        second["matrix"][next(iter(second["matrix"]))] = "FAIL"
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize("mutation", ["author", "self_review", "missing_check", "one_pilot", "same_operator"])
def test_external_independence_not_agent_code_review(candidate, tmp_path, mutation):
    external = candidate["external"]
    report = external["reports"][0]
    if mutation == "author":
        report["operator"] = report["authors"][0]
    elif mutation == "self_review":
        report["reviewer"] = report["operator"]
    elif mutation == "missing_check":
        report["checks"].pop(next(iter(report["checks"])))
    else:
        external["path"] = "pilots"
        report["repository"] = "repo-one"
        report["checks"] = {key: True for key in validator.PILOT_CHECKS}
        if mutation == "same_operator":
            external["reports"].append(copy.deepcopy(report))
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize("mutation", ["absent", "unapproved", "wrong_head"])
def test_explicit_maintainer_signoff_required(candidate, tmp_path, mutation):
    if mutation == "absent":
        candidate.pop("maintainer_signoff")
    elif mutation == "unapproved":
        candidate["maintainer_signoff"]["approved"] = False
    else:
        candidate["maintainer_signoff"]["source_commit"] = "d" * 40
    assert not validator.validate(candidate, tmp_path)["eligible"]


def test_unevaluated_never_eligible(candidate, tmp_path):
    candidate["gates"][0]["status"] = "UNEVALUATED"
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize(
    "value", [None, [], {}, {"gates": [None]}, {"schema": "magicite/release-manifest/1", "gates": 3}]
)
def test_malformed_input_returns_closed_result(value, tmp_path):
    assert not validator.validate(value, tmp_path)["eligible"]


def test_two_independent_pilots_qualify(candidate, tmp_path):
    external = candidate["external"]
    external["path"] = "pilots"
    first = external["reports"][0]
    first["repository"] = "repo-one"
    first["checks"] = {key: True for key in validator.PILOT_CHECKS}
    second = copy.deepcopy(first)
    second["operator"] = "second-external-operator"
    second["repository"] = "repo-two"
    second["artifact"] = artifact(tmp_path, "pilot-two.json", {"pilot": 2})
    external["reports"].append(second)
    for row in external["reports"]:
        bind_report(tmp_path, row, "magicite/external-pilot/1")
    assert validator.validate(candidate, tmp_path)["eligible"]


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"gates": [], "gates": []}')
    with pytest.raises(ValueError, match="duplicate"):
        validator.read_json(path)


def test_rc_latest_source_must_match_candidate(candidate, tmp_path):
    candidate["release_candidates"][1]["source_commit"] = "e" * 40
    assert not validator.validate(candidate, tmp_path)["eligible"]


def test_all_obligations_required(candidate, tmp_path):
    candidate["gates"][0]["evidence"].pop()
    assert not validator.validate(candidate, tmp_path)["eligible"]


def test_lock_bytes_are_verified(candidate, tmp_path):
    (tmp_path / "uv.lock").write_text("changed")
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("signoff", "approved", False),
        ("signoff", "source_commit", "d" * 40),
        ("signoff", "identity", "somebody-else"),
        ("external", "status", "UNEVALUATED"),
        ("external", "independent", False),
        ("rc", "fingerprints", {"api": "d" * 64, "schema": "e" * 64}),
        ("rc", "matrix", {"python-3.11": "FAIL"}),
    ],
)
def test_hash_valid_report_contradictions_rejected(candidate, tmp_path, target, field, value):
    row = {
        "signoff": candidate["maintainer_signoff"],
        "external": candidate["external"]["reports"][0],
        "rc": candidate["release_candidates"][0],
    }[target]
    ref = row["artifact"]
    payload = json.loads((tmp_path / ref["path"]).read_text())
    payload[field] = value
    row["artifact"] = artifact(tmp_path, ref["path"], payload)
    assert not validator.validate(candidate, tmp_path)["eligible"]


def test_distinct_rc_reports_cannot_reuse_one_build(candidate, tmp_path):
    left, right = candidate["release_candidates"]
    right["build_artifact"] = left["build_artifact"]
    bind_report(tmp_path, right, "magicite/rc-build-report/1")
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize("target", ["signoff", "external"])
def test_immutable_report_requires_json_booleans(candidate, tmp_path, target):
    row = candidate["maintainer_signoff"] if target == "signoff" else candidate["external"]["reports"][0]
    ref = row["artifact"]
    payload = json.loads((tmp_path / ref["path"]).read_text())
    if target == "signoff":
        payload["approved"] = 1
    else:
        payload["independent"] = 1
        payload["checks"] = dict.fromkeys(payload["checks"], 1)
    row["artifact"] = artifact(tmp_path, ref["path"], payload)
    assert not validator.validate(candidate, tmp_path)["eligible"]


@pytest.mark.parametrize("target", ["candidate", "rc"])
@pytest.mark.parametrize("value", [None, True, 0])
def test_release_sources_require_explicit_clean_boolean(candidate, tmp_path, target, value):
    row = candidate if target == "candidate" else candidate["release_candidates"][1]
    if value is None:
        row.pop("source_dirty")
    else:
        row["source_dirty"] = value
    if target == "rc":
        bind_report(tmp_path, row, "magicite/rc-build-report/1")
    assert not validator.validate(candidate, tmp_path)["eligible"]


def test_rc_immutable_report_cannot_hide_dirty_source(candidate, tmp_path):
    row = candidate["release_candidates"][1]
    ref = row["artifact"]
    payload = json.loads((tmp_path / ref["path"]).read_text())
    payload["source_dirty"] = True
    row["artifact"] = artifact(tmp_path, ref["path"], payload)
    assert not validator.validate(candidate, tmp_path)["eligible"]
