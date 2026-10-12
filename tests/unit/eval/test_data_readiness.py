"""Synthetic fixture controls only; no authentic readiness or model calls."""

from __future__ import annotations

import copy
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from magicite.eval import data_readiness as data
from magicite.eval.digests import sha256_bytes

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/calibration-data"


@pytest.fixture
def packet(tmp_path):
    root = tmp_path / "packet"
    shutil.copytree(FIXTURE, root)
    return root / "packet.json"


def load(p):
    return json.loads(p.read_bytes())


def save(p, value):
    p.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def mutate(packet, field, change):
    p = load(packet)
    ref = p["files"][field]
    path = packet.parent / ref["path"]
    value = load(path)
    change(value)
    save(path, value)
    ref["sha256"] = sha256_bytes(path.read_bytes())
    if isinstance(value, list):
        ref["count"] = len(value)
    save(packet, p)


def evidence(packet, name, change):
    p = load(packet)
    ref = p["evidence"][name]
    path = packet.parent / ref["path"]
    v = load(path)
    change(v)
    save(path, v)
    ref["sha256"] = sha256_bytes(path.read_bytes())
    save(packet, p)


def frozen(packet, tmp_path):
    p = tmp_path / "freeze.json"
    data.freeze_packet(packet, p, source_commit="1" * 40, runner_sha256="2" * 64)
    return p


def test_fixture_checks_never_authenticate_declarations(packet):
    r = data.prepare_packet(packet).report
    assert r["structural_controls"] == "PASS" and r["qualifying"] is False
    assert all(x["status"] == "UNEVALUATED" for x in r["empirical_obligations"].values())
    assert r["counts"] == {"pool": 2, "queries": 3, "final": 1, "no_match": 1}


@pytest.mark.parametrize("kind", ["hash", "count", "traversal", "absolute", "symlink"])
def test_reference_boundaries_fail(packet, tmp_path, kind):
    p = load(packet)
    ref = p["files"]["runtime"]
    if kind == "hash":
        (packet.parent / ref["path"]).write_bytes(b"[]")
    elif kind == "count":
        ref["count"] = True
    elif kind == "traversal":
        ref["path"] = "../outside.json"
    elif kind == "absolute":
        ref["path"] = str(tmp_path / "outside.json")
    else:
        outside = tmp_path / "outside.json"
        outside.write_bytes((packet.parent / ref["path"]).read_bytes())
        (packet.parent / ref["path"]).unlink()
        (packet.parent / ref["path"]).symlink_to(outside)
    save(packet, p)
    with pytest.raises(ValueError):
        data.prepare_packet(packet)


@pytest.mark.parametrize("field", ["runtime", "labels", "partitions", "provenance"])
def test_duplicate_or_missing_members_rejected(packet, field):
    mutate(packet, field, lambda rows: rows.append(copy.deepcopy(rows[0])))
    with pytest.raises(ValueError, match="duplicate"):
        data.prepare_packet(packet)


@pytest.mark.parametrize(
    "fact", ["positive_empty", "no_match_positive", "dangling", "no_rationale", "wrong_pool"]
)
def test_no_match_is_pool_bound_annotation_not_empty_map(packet, fact):
    if fact == "wrong_pool":
        evidence(packet, "judgment1", lambda d: d.update(pool_sha256="0" * 64))
    else:

        def change(rows):
            if fact == "positive_empty":
                rows[0]["relevance"] = {}
            elif fact == "no_match_positive":
                rows[1]["relevance"] = {"egr_fixture1": 1}
            elif fact == "dangling":
                rows[0]["relevance"] = {"absent": 1}
            else:
                rows[1]["rationale"] = ""

        mutate(packet, "labels", change)
    with pytest.raises(ValueError):
        data.prepare_packet(packet)


@pytest.mark.parametrize("kind", ["normalized", "group", "related_alias"])
def test_all_pair_family_leakage(packet, kind):
    if kind == "normalized":
        mutate(packet, "runtime", lambda rows: rows[1].update(query_text="  FIND THE INVENTED FIRST TASK  "))
    elif kind == "related_alias":
        mutate(
            packet, "provenance", lambda rows: rows[1]["source"].update(related_sources=["fixture-source0"])
        )
    else:
        mutate(
            packet, "provenance", lambda rows: rows[1]["source"]["group_keys"].update(task="fixture-task0")
        )
        evidence(packet, "source1", lambda d: d["group_keys"].update(task="fixture-task0"))
    with pytest.raises(ValueError, match="leakage"):
        data.prepare_packet(packet)


@pytest.mark.parametrize(
    "kind", ["author", "missing_review", "fixture_relabel", "timezone", "cutoff", "gold_context"]
)
def test_provenance_temporal_and_runtime_rejections(packet, kind):
    if kind == "author":
        mutate(
            packet,
            "provenance",
            lambda r: r[0]["annotation"].update(reviewer_id=r[0]["annotation"]["author_id"]),
        )
    elif kind == "missing_review":
        mutate(packet, "provenance", lambda r: r[0]["annotation"].update(evidence_ref="absent"))
    elif kind == "fixture_relabel":
        mutate(packet, "provenance", lambda r: r[0].update(evidence_class="externally_sourced_observation"))
    elif kind == "gold_context":
        mutate(packet, "runtime", lambda r: r[0]["compatibility_context"].update(gold_ids=["egr_fixture1"]))
    else:
        value = "2026-02-01T00:00:00" if kind == "timezone" else "2025-01-01T00:00:00+00:00"
        mutate(packet, "provenance", lambda r: r[2]["temporal"].update(event_at=value))
        evidence(packet, "time2", lambda d: d.update(event_at=value))
    with pytest.raises(ValueError):
        data.prepare_packet(packet)


def test_offline_freezes_same_identity_and_drift_rejects(packet, tmp_path):
    a = frozen(packet, tmp_path)
    b = tmp_path / "other.json"
    data.freeze_packet(packet, b, source_commit="1" * 40, runner_sha256="2" * 64)
    assert load(a)["identity"] == load(b)["identity"]
    data.verify_freeze(a)
    (packet.parent / "preregistration.json").write_text('{"experiment_id":"renamed"}')
    with pytest.raises(ValueError):
        data.verify_freeze(a)


def test_access_intent_precedes_any_semantic_label_read_and_replay(packet, tmp_path, monkeypatch):
    f = frozen(packet, tmp_path)
    original = data._read
    reads = []

    def read(root, ref, inventory):
        if ref["path"] == "labels.json":
            assert data.access_path(f).exists()
            reads.append("labels-after-intent")
        return original(root, ref, inventory)

    monkeypatch.setattr(data, "_read", read)
    labels = data.open_final_labels(f, purpose="fixture-evaluation")
    assert [x["query_id"] for x in labels] == ["fixture-q2"] and reads
    intent = data.access_path(f).read_bytes()
    with pytest.raises(FileExistsError):
        data.open_final_labels(f, purpose="fixture-evaluation")
    assert data.open_final_labels(f, purpose="fixture-evaluation", replay=True) == labels
    assert data.access_path(f).read_bytes() == intent
    with pytest.raises(ValueError):
        data.open_final_labels(f, purpose="different-purpose", replay=True)


def test_failed_persistence_releases_no_labels(packet, tmp_path, monkeypatch):
    f = frozen(packet, tmp_path)

    def fail(*args):
        raise OSError("owned fixture write failure")

    monkeypatch.setattr(data, "_publish_immutable", fail)
    with pytest.raises(OSError):
        data.open_final_labels(f, purpose="fixture")
    assert not data.access_path(f).exists()


def test_concurrent_fresh_open_exactly_one_winner(packet, tmp_path):
    f = frozen(packet, tmp_path)

    def attempt(_):
        try:
            data.open_final_labels(f, purpose="fixture")
            return "released"
        except FileExistsError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [1, 2]))
    assert sorted(results) == ["denied", "released"]


def test_known_official_revision_renamed_experiment_never_fresh(packet, tmp_path):
    p = load(packet)
    p["dataset"]["revision"] = data.OFFICIAL_REVISION
    save(packet, p)
    mutate(packet, "preregistration", lambda d: d.update(experiment_id="new-experiment"))
    f = frozen(packet, tmp_path)
    with pytest.raises(ValueError, match="known exposed"):
        data.open_final_labels(f, purpose="fixture")
    assert not data.access_path(f).exists()


@pytest.mark.parametrize("field", ["pool", "runtime", "labels", "partitions", "provenance"])
def test_each_frozen_input_drift_denies_before_intent(packet, tmp_path, field):
    f = frozen(packet, tmp_path)
    path = packet.parent / load(packet)["files"][field]["path"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        data.open_final_labels(f, purpose="fixture")
    assert not data.access_path(f).exists()


def test_missing_provenance_is_explicitly_unavailable(packet):
    def change(rows):
        rows[0]["source"].update(group_keys=None, unavailable_reason="Fixture grouping unavailable")
        rows[0]["annotation"] = {
            "author_id": "fixture-author",
            "independent_review": False,
            "unavailable_reason": "Fixture independent review unavailable",
        }
        rows[0]["temporal"] = {"unavailable_reason": "No authentic event history supplied"}

    mutate(packet, "provenance", change)
    evidence(packet, "source0", lambda doc: doc.update(group_keys=None))
    report = data.prepare_packet(packet).report
    assert all(row["status"] == "UNEVALUATED" for row in report["empirical_obligations"].values())
    assert any("grouping unavailable" in s for s in report["limitations"])


def test_typed_receipt_context_never_qualifies_fixture(packet, tmp_path):
    from magicite.eval.validate import validate_experiment_corpus_seal

    f = frozen(packet, tmp_path)
    experiment = {"label_provenance": {"final_labels_opened": True}}
    corpus = {"queries": [{"split": "final"}]}
    assert validate_experiment_corpus_seal(experiment, corpus)
    context = data.FinalAccessContext(f, "fixture")
    assert any(
        "access failed" in e
        for e in validate_experiment_corpus_seal(experiment, corpus, data_context=context)
    )
    data.open_final_labels(f, purpose="fixture")
    errors = validate_experiment_corpus_seal(experiment, corpus, data_context=context)
    assert errors == ["verified data declarations are nonqualifying; authentic readiness UNEVALUATED"]


def test_known_exposure_constants_match_actual_committed_archive():
    root = Path(__file__).resolve().parents[3] / "docs/qualification/evidence/official-quality"
    freeze = load(root / "run-freeze.json")
    exposure = load(root / "first-heldout-exposure.json")
    assert exposure["source_commit"] == "389834606e6b9d4158f128515517ce5d5ad97126"
    assert data.OFFICIAL_REVISION in json.dumps(load(root / "scoring-derivation.json"))
    assert sha256_bytes((root / "test-scoring-projection.json.gz").read_bytes()) in data.EXPOSED_DIGESTS
    assert {
        "463c4e0999f48e9bd19561dc28acaab0b48942b2599160e4922ed42190174ffd",
        "3e2f84de80bee5fa9bb05d1a1c235afb356f376ef6edbc6ef293ceb0995d03a7",
    } <= data.EXPOSED_DIGESTS
    assert all(
        value in json.dumps(freeze)
        for value in data.EXPOSED_DIGESTS
        - {sha256_bytes((root / "test-scoring-projection.json.gz").read_bytes())}
    )


@pytest.mark.parametrize("hidden", ["keys", "alias"])
def test_unavailable_declaration_cannot_hide_known_bound_groups(packet, hidden):
    if hidden == "keys":
        evidence(packet, "source1", lambda doc: doc["group_keys"].update(repository="fixture-repository0"))
        mutate(
            packet,
            "provenance",
            lambda rows: rows[1]["source"].update(
                group_keys=None, unavailable_reason="Declared unknown despite bound evidence"
            ),
        )
    else:
        evidence(packet, "source1", lambda doc: doc.update(related_sources=["fixture-source0"]))
        mutate(packet, "provenance", lambda rows: rows[1]["source"].update(related_sources=[]))
    with pytest.raises(ValueError, match="known referenced source"):
        data.prepare_packet(packet)


def test_known_bound_alias_remains_part_of_family_separation(packet):
    evidence(packet, "source1", lambda doc: doc.update(related_sources=["fixture-source0"]))
    mutate(packet, "provenance", lambda rows: rows[1]["source"].update(related_sources=["fixture-source0"]))
    with pytest.raises(ValueError, match="group leakage"):
        data.prepare_packet(packet)


@pytest.mark.parametrize("mixed", ["event_at", "collected_at", "annotated_at", "evidence_ref"])
def test_unavailable_temporal_cannot_hide_present_history(packet, mixed):
    def change(rows):
        prior = rows[2]["temporal"]
        rows[2]["temporal"] = {"unavailable_reason": "Declared unknown", mixed: prior[mixed]}

    mutate(packet, "provenance", change)
    with pytest.raises(ValueError, match="unavailable temporal declaration"):
        data.prepare_packet(packet)


def test_unavailable_temporal_cannot_hide_bound_cutoff_violation(packet):
    value = "2025-01-01T00:00:00+00:00"
    evidence(packet, "time2", lambda doc: doc.update(event_at=value))
    mutate(
        packet,
        "provenance",
        lambda rows: rows[2]["temporal"].update(
            event_at=value, unavailable_reason="Declared unknown despite known history"
        ),
    )
    with pytest.raises(ValueError, match="unavailable temporal declaration"):
        data.prepare_packet(packet)
