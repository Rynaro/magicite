"""Actual router with deterministic fixture instrumentation: nonqualifying mechanics."""

from __future__ import annotations

import json
import shutil

import pytest
from tests.unit.core.test_router_policy import _baseline_registry
from tests.unit.eval.test_data_readiness import FIXTURE, mutate

from magicite.eval import calibration_consumer as consumer
from magicite.eval import data_readiness as data
from magicite.eval import production


@pytest.fixture
def run(tmp_path, cfg, db_conn, embedder):
    from magicite.embeddings.fastembed_provider import FastEmbedProvider

    cache = tmp_path / "model"
    cache.mkdir()
    for name in (
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ):
        (cache / name).write_bytes(b"fixture bytes; not an ONNX measurement")

    class FixtureProvider(FastEmbedProvider):
        def __init__(self):
            self.model_name, self.dim = embedder.model_name, embedder.dim
            self._cache_dir = str(cache)

        def embed(self, text):
            return embedder.embed(text)

    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = "experimental/sparse-v1"
    root = tmp_path / "packet"
    shutil.copytree(FIXTURE, root)
    packet = root / "packet.json"
    # Different source groups and query texts retained. Make both non-final rows calibration.
    mutate(packet, "partitions", lambda rows: rows[0].update(split="calibration"))
    mutate(
        packet,
        "runtime",
        lambda rows: [row.update(query_text=" ".join(["orchid"] * (i + 1))) for i, row in enumerate(rows)],
    )
    freeze = tmp_path / "freeze.json"
    data.freeze_packet(packet, freeze, source_commit="1" * 40, runner_sha256="2" * 64)
    actual = production.ActualRouter(cfg, db_conn, FixtureProvider())
    manifest = production.freeze_model(cache)
    return freeze, actual, cache, manifest, tmp_path


def fit(run):
    freeze, actual, cache, manifest, root = run
    path = root / "candidate.json"
    value = consumer.fit_calibration(freeze, path, actual, model_cache=cache, model_manifest=manifest)
    return path, value


def test_actual_trace_and_end_to_end_no_activation(run):
    path, candidate = fit(run)
    freeze, actual, cache, _, root = run
    assert len(candidate["candidate"]["calibration_traces"]) == 2
    assert all(t["raw_candidate_ids"] for t in candidate["candidate"]["calibration_traces"])
    assert not (actual.cfg.data_dir / "calibration/active.json").exists()
    report = consumer.evaluate_frozen_calibration(
        freeze, path, root / "result.json", actual, model_cache=cache
    )
    assert report["status"] == "complete"
    assert report["quality"]["ndcg_at_rank_depth"]["denominator"] == 1
    assert report["confidence"] is None and report["ECE"] is None
    assert report["E2"] == report["E3"] == "UNEVALUATED"
    assert report["completed_queries"] == 1
    assert data.access_path(freeze).exists()
    with pytest.raises(FileExistsError):
        consumer.evaluate_frozen_calibration(freeze, path, root / "another.json", actual, model_cache=cache)
    replay = consumer.evaluate_frozen_calibration(
        freeze, path, root / "replay.json", actual, model_cache=cache, replay=True
    )
    assert replay["access"] == "exposed_replay"
    with pytest.raises(ValueError, match="exposed"):
        consumer.fit_calibration(
            freeze, root / "refit.json", actual, model_cache=cache, model_manifest=run[3]
        )


def test_fit_never_decodes_final_labels_or_executes_final(run, monkeypatch):
    monkeypatch.setattr(data, "prepare_packet", lambda *a: pytest.fail("fit prepared final labels"))
    monkeypatch.setattr(data, "open_final_labels", lambda *a, **k: pytest.fail("fit opened final"))
    original_read = data._read

    def read(root, ref, inventory):
        assert "labels" not in ref["path"]
        return original_read(root, ref, inventory)

    monkeypatch.setattr(data, "_read", read)
    original = run[1].predict

    def predict(query, **kw):
        assert query["query_id"] != "fixture-q2"
        return original(query, **kw)

    monkeypatch.setattr(run[1], "predict", predict)
    fit(run)


@pytest.mark.parametrize("mutation", ["config", "model", "source", "context", "rank", "fit"])
def test_final_drift_rejected_before_exposure(run, monkeypatch, mutation):
    path, value = fit(run)
    freeze, actual, cache, _, root = run
    if mutation == "config":
        actual.cfg.abstention_score_threshold = 10
    elif mutation == "model":
        (cache / "tokenizer.json").write_bytes(b"changed")
    elif mutation == "source":
        monkeypatch.setattr(consumer, "source_identity", lambda: {"changed": "x"})
    elif mutation == "rank":
        actual.rank_depth += 1
    elif mutation == "context":
        value["candidate"]["binding"]["queries"][0]["compatibility_context"] = {"language": "ruby"}
        value["identity"] = consumer.sha256_json(value["candidate"])
        path.write_text(json.dumps(value))
    else:
        value["candidate"]["artifact"]["score_threshold"] += 1
        value["identity"] = consumer.sha256_json(value["candidate"])
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        consumer.evaluate_frozen_calibration(freeze, path, root / "result.json", actual, model_cache=cache)
    assert not data.access_path(freeze).exists()


def test_failed_final_keeps_intent_and_explicit_replay(run, monkeypatch):
    path, _ = fit(run)
    freeze, actual, cache, _, root = run
    original = actual.predict
    monkeypatch.setattr(
        actual, "predict", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fixture interruption"))
    )
    with pytest.raises(RuntimeError):
        consumer.evaluate_frozen_calibration(freeze, path, root / "failed.json", actual, model_cache=cache)
    failed = json.loads((root / "failed.json").read_bytes())
    assert failed["status"] == "incomplete" and failed["completed_queries"] == 0
    assert data.access_path(freeze).exists()
    monkeypatch.setattr(actual, "predict", original)
    assert (
        consumer.evaluate_frozen_calibration(
            freeze, path, root / "replay.json", actual, model_cache=cache, replay=True
        )["status"]
        == "complete"
    )


def test_raw_scores_above_one_and_existing_threshold_oracle(run, monkeypatch):
    # Explicit score-range instrumentation around a real route; not model evidence.
    original = run[1].predict

    def instrument(query, **kwargs):
        trace = original(query, **kwargs)
        trace["raw_scores"] = [score * 1_000_000 for score in trace["raw_scores"]]
        return trace

    monkeypatch.setattr(run[1], "predict", instrument)
    _, candidate = fit(run)
    body = candidate["candidate"]
    negative = next(t for t in body["calibration_traces"] if t["query_id"] == "fixture-q1")
    assert negative["raw_scores"][0] > 1
    assert body["artifact"]["score_threshold"] == negative["raw_scores"][0]
    assert body["artifact"]["margin_threshold"] == consumer.calibration.score_margin(negative["raw_scores"])


def test_candidate_and_intent_precede_final_decode_and_actual_route(run, monkeypatch):
    path, value = fit(run)
    freeze, actual, cache, _, root = run
    events = []
    original_open, original_predict = data.open_final_labels, actual.predict

    def open_labels(*args, **kw):
        assert path.exists() and (root / "ordered.json.run-intent.json").exists()
        assert consumer.final_owner(data.verify_freeze(freeze)).exists()
        events.append("intent")
        return original_open(*args, **kw)

    def predict(*args, **kw):
        assert data.access_path(freeze).exists()
        events.append("actual")
        return original_predict(*args, **kw)

    monkeypatch.setattr(data, "open_final_labels", open_labels)
    monkeypatch.setattr(actual, "predict", predict)
    consumer.evaluate_frozen_calibration(freeze, path, root / "ordered.json", actual, model_cache=cache)
    assert events == ["intent", "actual"]
    assert value["identity"] == json.loads(path.read_bytes())["identity"]


def test_cli_fit_final_with_actual_fixture_and_model_guard(run, monkeypatch, capsys):
    from magicite.eval.__main__ import main

    freeze, actual, cache, manifest, root = run
    monkeypatch.setattr(consumer, "cli_actual", lambda *args: actual)
    model = root / "model.json"
    model.write_text(json.dumps(manifest))
    shared = [
        "--freeze",
        str(freeze),
        "--project-root",
        str(actual.cfg.project_root),
        "--model-cache",
        str(cache),
        "--model-manifest",
        str(model),
    ]

    # CLI owns connection cleanup: keep fixture's shared connection open for its paired invocation.
    class ConnectionOwner:
        def __getattr__(self, name):
            return getattr(actual.conn_original, name)

        def close(self):
            pass

    actual.conn_original = actual.conn
    actual.conn = ConnectionOwner()
    assert main(["fit-calibration", *shared, "--output", str(root / "cli-fit.json")]) == 0
    assert (
        main(
            [
                "evaluate-frozen-calibration",
                *shared,
                "--candidate",
                str(root / "cli-fit.json"),
                "--output",
                str(root / "cli-result.json"),
            ]
        )
        == 0
    )
    assert "UNEVALUATED" in capsys.readouterr().out
    actual.conn = actual.conn_original


@pytest.mark.parametrize(
    "field", ["generation", "registry", "policy", "trust", "semantics", "libraries", "root"]
)
def test_binding_families_preflight_before_actual_final(run, monkeypatch, field):
    from dataclasses import replace

    path, value = fit(run)
    freeze, actual, cache, _, root = run
    if field == "generation":
        original = consumer.router.pin_index_identity

        def changed(conn):
            pins = original(conn)
            return ("changed-generation", *pins[1:])

        monkeypatch.setattr(consumer.router, "pin_index_identity", changed)
    elif field == "registry":
        actual.conn.execute("UPDATE engram SET name='altered' WHERE id='egr_aa01cc11'")
        actual.conn.commit()
    elif field == "policy":
        original = consumer.router._resolve_active_policy

        def changed(cfg):
            result = list(original(cfg))
            result[1] = "changed-policy-digest"
            return tuple(result)

        monkeypatch.setattr(consumer.router, "_resolve_active_policy", changed)
    elif field == "trust":
        original = consumer.router._trust_decisions_by_engram

        def changed(cfg):
            decisions, policy, snapshot = original(cfg)
            return decisions, policy, replace(snapshot, head={**snapshot.head, "changed": True})

        monkeypatch.setattr(consumer.router, "_trust_decisions_by_engram", changed)
    elif field == "semantics":
        monkeypatch.setattr(consumer, "SEMANTICS", "changed-score-units")
    elif field == "libraries":
        original = consumer.freeze_model

        def changed(cache):
            manifest = original(cache)
            manifest["libraries"]["numpy"] = "changed-library"
            return manifest

        monkeypatch.setattr(consumer, "freeze_model", changed)
    else:
        value["candidate"]["binding"]["input_root"] = str(root / "elsewhere")
        value["identity"] = consumer.sha256_json(value["candidate"])
        path.write_text(json.dumps(value))
    monkeypatch.setattr(actual, "predict", lambda *a, **k: pytest.fail("drift executed final"))
    with pytest.raises(ValueError):
        consumer.evaluate_frozen_calibration(freeze, path, root / "changed.json", actual, model_cache=cache)
    assert not data.access_path(freeze).exists()


def test_actual_raw_trace_survives_threshold_denial_and_empty(run):
    _, actual, _, _, _ = run
    query = {"query_id": "trace", "query_text": "orchid", "compatibility_context": {}}
    ordinary = actual.predict(query, raw_trace=True)
    actual.cfg.abstention_score_threshold = 100
    denied = actual.predict(query, raw_trace=True)
    assert denied["status"] == "abstained" and denied["candidate_ids"] == []
    assert denied["raw_candidate_ids"] == ordinary["raw_candidate_ids"]
    assert denied["raw_scores"] == ordinary["raw_scores"]
    empty = actual.predict({**query, "query_text": "neverappearingtoken"}, raw_trace=True)
    assert empty["raw_candidate_ids"] == []


def test_nonfinite_and_foreign_trace_rejected_without_candidate(run, monkeypatch):
    original = run[1].predict

    def bad(query, **kw):
        trace = original(query, **kw)
        trace["raw_scores"][0] = float("inf")
        return trace

    monkeypatch.setattr(run[1], "predict", bad)
    with pytest.raises(ValueError, match="nonfinite"):
        fit(run)

    def foreign(query, **kw):
        trace = original(query, **kw)
        trace["query_id"] = "foreign"
        return trace

    monkeypatch.setattr(run[1], "predict", foreign)
    with pytest.raises(ValueError, match="query identity"):
        fit(run)
    assert not (run[4] / "candidate.json").exists()


def test_frozen_grouped_consumer_budget_marker_and_ece_controls(run, tmp_path):
    from tests.unit.eval.test_grouped_evaluation import protocol

    from magicite.core import calibration_admission
    from magicite.core.comparison_budget import ComparisonBudget
    from magicite.errors import InvalidInputError

    freeze, actual, cache, manifest, root = run
    p = protocol()
    b = ComparisonBudget(3, 3, 6, 3, 1)
    p["comparison_budget"] = b.to_dict()
    mutate(
        root / "packet" / "packet.json", "preregistration", lambda value: value.update(statistical_protocol=p)
    )
    nextfreeze = tmp_path / "grouped-freeze.json"
    data.freeze_packet(
        root / "packet" / "packet.json", nextfreeze, source_commit="1" * 40, runner_sha256="2" * 64
    )
    actual.rank_depth = 3
    actual.comparison_budget = b
    path = root / "budget-fit.json"
    candidate = consumer.fit_calibration(nextfreeze, path, actual, model_cache=cache, model_manifest=manifest)
    artifact = candidate["candidate"]["artifact"]
    assert artifact["schema_version"] == "CalibrationArtifact/2"
    assert artifact["evaluation_budget_digest"] == b.digest
    assert artifact["probability_model"] is None
    assert candidate["candidate"]["probability_reason"] == "empty-or-one-class-top1-calibration"
    report = consumer.evaluate_frozen_calibration(
        nextfreeze, path, root / "budget-result.json", actual, model_cache=cache
    )
    assert report["schema"] == "magicite/grouped-calibration-evaluation/1"
    assert report["ECE"]["ECE"] is None and report["ECE"]["excluded"] == 1
    assert report["abstention_bounds"]["coverage"]["support"]["status"] == "inconclusive"
    assert report["qualifying"] is False and report["E2"] == "UNEVALUATED"
    with pytest.raises(InvalidInputError, match="evaluation-budget"):
        calibration_admission.validate(artifact, {})
    assert data.access_path(nextfreeze).exists()
    actual.comparison_budget = ComparisonBudget(2, 2, 4, 2, 1)
    with pytest.raises(ValueError, match="drift"):
        consumer.evaluate_frozen_calibration(
            nextfreeze, path, root / "different.json", actual, model_cache=cache, replay=True
        )


def test_grouped_no_match_operational_error_never_true_negative(run, tmp_path, monkeypatch):
    from tests.unit.eval.test_data_readiness import evidence
    from tests.unit.eval.test_grouped_evaluation import protocol

    freeze, actual, cache, manifest, root = run
    packet = root / "packet" / "packet.json"
    mutate(packet, "labels", lambda rows: rows[-1].update(kind="no_match", relevance={}))
    evidence(packet, "judgment2", lambda doc: doc.update(kind="no_match"))
    mutate(packet, "preregistration", lambda value: value.update(statistical_protocol=protocol()))
    frozen = tmp_path / "errors-freeze.json"
    data.freeze_packet(packet, frozen, source_commit="1" * 40, runner_sha256="2" * 64)
    path = root / "errors-fit.json"
    consumer.fit_calibration(frozen, path, actual, model_cache=cache, model_manifest=manifest)
    original = actual.predict

    def error_final(query, **kwargs):
        trace = original(query, **kwargs)
        if query["query_id"] == "fixture-q2":
            trace.update(status="error", operational_error="synthetic-timeout", selected_ids=[])
        return trace

    monkeypatch.setattr(actual, "predict", error_final)
    result = consumer.evaluate_frozen_calibration(
        frozen, path, root / "errors-result.json", actual, model_cache=cache
    )
    bound = result["abstention_bounds"]["false_selection"]
    assert result["status"] == "complete_with_errors"
    assert bound["point"] == 1 and bound["bound"] == 1
    assert bound["operational_errors"] == 1 and bound["status"] == "inconclusive"
    assert bound["threshold"] == 0.05
    assert result["rows"][0]["proposed_top1_probability"] is None


def test_consumer_probability_targets_actual_top1_on_calibration_only(run, tmp_path, monkeypatch):
    from tests.unit.eval.test_data_readiness import evidence
    from tests.unit.eval.test_grouped_evaluation import protocol

    freeze, actual, cache, manifest, root = run
    packet = root / "packet" / "packet.json"
    proposed = actual.predict(
        {"query_id": "authoring", "query_text": "orchid", "compatibility_context": {}}, raw_trace=True
    )["raw_candidate_ids"][0]
    mutate(packet, "pool", lambda rows: rows[0].update(candidate_id=proposed))
    pool_digest = json.loads(packet.read_bytes())["files"]["pool"]["sha256"]
    for name in ("judgment1", "judgment2"):
        evidence(packet, name, lambda doc: doc.update(pool_sha256=pool_digest))

    def labels(rows):
        for row in rows:
            if "egr_fixture1" in row["relevance"]:
                row["relevance"][proposed] = row["relevance"].pop("egr_fixture1")

    mutate(packet, "labels", labels)
    mutate(packet, "preregistration", lambda value: value.update(statistical_protocol=protocol()))
    frozen = tmp_path / "probability-freeze.json"
    data.freeze_packet(packet, frozen, source_commit="1" * 40, runner_sha256="2" * 64)
    real_read = data._read

    def no_final_decode(root, ref, inventory):
        if ref["path"] == json.loads(packet.read_bytes())["files"]["labels"]["path"]:
            pytest.fail("fit decoded final labels instead of preparation projection")
        return real_read(root, ref, inventory)

    monkeypatch.setattr(data, "_read", no_final_decode)
    path = root / "probability-fit.json"
    candidate = consumer.fit_calibration(frozen, path, actual, model_cache=cache, model_manifest=manifest)
    model = candidate["candidate"]["artifact"]["probability_model"]
    assert model is not None and model["target"] == "raw-top1-correctness" and model["n_examples"] == 2
    monkeypatch.setattr(data, "_read", real_read)
    report = consumer.evaluate_frozen_calibration(
        frozen, path, root / "probability-result.json", actual, model_cache=cache
    )
    assert report["ECE"]["n"] == 1 and report["ECE"]["ECE"] is not None
    assert report["rows"][0]["proposed_top1_probability"] is not None
    assert "No probability fit" not in candidate["candidate"]["limitations"]
    assert report["qualifying"] is False


def test_concurrent_actual_final_claim_has_one_owner(run, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from magicite.storage.db import connect

    path, candidate = fit(run)
    freeze, actual, cache, manifest, root = run
    from tests.support.custody_adapter import threaded_calls

    from magicite.core import writer_guard

    _, provider = writer_guard.resolve_custody(actual.cfg)
    threaded_calls(provider, monkeypatch)
    barrier = Barrier(2)

    def attempt(index):
        conn = connect(actual.cfg.db_path, migrate=False)
        local = production.ActualRouter(actual.cfg, conn, actual.embedder, rank_depth=actual.rank_depth)
        barrier.wait(timeout=10)
        try:
            return consumer.evaluate_frozen_calibration(
                freeze, path, root / ("concurrent-" + str(index) + ".json"), local, model_cache=cache
            )
        except FileExistsError:
            return "lost-owner-claim"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(isinstance(r, dict) and r["status"] == "complete" for r in results) == 1
    assert results.count("lost-owner-claim") == 1
    assert data.access_path(freeze).exists()
    assert (
        json.loads(consumer.final_owner(data.verify_freeze(freeze)).read_bytes())["candidate_identity"]
        == candidate["identity"]
    )


@pytest.fixture
def power_run(run, tmp_path):
    from tests.unit.eval.test_grouped_evaluation import protocol

    freeze, actual, cache, manifest, root = run
    packet = root / "packet" / "packet.json"
    mutate(packet, "partitions", lambda rows: rows[0].update(split="development"))
    mutate(
        packet,
        "preregistration",
        lambda prereg: prereg.update(
            statistical_protocol=protocol(),
            power_assumptions={"development_representative": True, "evidence_digests": ["a" * 64]},
        ),
    )
    frozen = tmp_path / "power-freeze.json"
    data.freeze_packet(packet, frozen, source_commit="1" * 40, runner_sha256="2" * 64)
    q = consumer.inputs(frozen)[1][0]
    candidate = consumer.checked_trace(actual, q)
    actual.cfg.routing_policy = "dense-v1"
    incumbent = consumer.checked_trace(actual, q)
    actual.cfg.routing_policy = "experimental/sparse-v1"
    envelope = consumer.prepare_power_input(
        frozen,
        actual,
        {"candidate": [candidate], "incumbent": [incumbent]},
        model_cache=cache,
        model_manifest=manifest,
    )
    return frozen, actual, cache, manifest, root, envelope


def test_power_input_live_workflow_before_fit(power_run):
    from magicite.eval.grouped_evaluation import plan_grouped_power, validate_development_power_envelope

    frozen, actual, cache, manifest, root, envelope = power_run
    validate_development_power_envelope(envelope)
    assert (
        consumer.validate_power_input(envelope, frozen, actual, model_cache=cache, model_manifest=manifest)
        == envelope
    )
    assert envelope["input"]["development"][0]["family"] == "development"
    assert not data.access_path(frozen).exists()
    # One authentic original fixture group cannot manufacture the30-group floor.
    with pytest.raises(ValueError, match="grouping/model support"):
        plan_grouped_power(
            envelope["input"]["development"],
            group_floors=[30],
            seed=1,
            repetitions=200,
            identities=envelope["input"]["identities"],
            assumptions={
                "independent_original_groups": True,
                "development_representative": True,
                "evidence_digests": ["a" * 64],
            },
            bound_input=envelope,
        )
    consumer.fit_calibration(
        frozen, root / "after-power-fit.json", actual, model_cache=cache, model_manifest=manifest
    )
    with pytest.raises(ValueError, match="before calibration"):
        consumer.validate_power_input(envelope, frozen, actual, model_cache=cache, model_manifest=manifest)


@pytest.mark.parametrize("mutation", ["projection", "trace", "source", "model", "budget"])
def test_power_live_binding_substitutions(power_run, mutation):
    from copy import deepcopy

    from magicite.eval.digests import sha256_json

    frozen, actual, cache, manifest, root, envelope = power_run
    altered = deepcopy(envelope)
    if mutation == "projection":
        altered["input"]["development_groups"][0]["group_id"] = "d" * 64
    else:
        key = {
            "trace": "query_fingerprint",
            "source": "source_digest",
            "model": "model_manifest_digest",
            "budget": "comparison_budget_digest",
        }[mutation]
        altered["input"]["predictions"]["candidate"][0][key] = "d" * 64
    altered["identity"] = sha256_json(altered["input"])
    with pytest.raises(ValueError):
        consumer.validate_power_input(altered, frozen, actual, model_cache=cache, model_manifest=manifest)


def test_power_model_bytes_live_drift(power_run):
    frozen, actual, cache, manifest, root, envelope = power_run
    (cache / "model_optimized.onnx").write_bytes(b"changed synthetic model bytes")
    with pytest.raises(ValueError):
        consumer.validate_power_input(envelope, frozen, actual, model_cache=cache, model_manifest=manifest)


def power_cli_arguments(power_run, source, output):
    frozen, actual, cache, manifest, root, envelope = power_run
    model = root / "power-model.json"
    model.write_text(json.dumps(manifest))
    source.write_text(json.dumps(envelope))
    return [
        "plan-grouped-power",
        "--input",
        str(source),
        "--freeze",
        str(frozen),
        "--project-root",
        str(actual.cfg.project_root),
        "--model-cache",
        str(cache),
        "--model-manifest",
        str(model),
        "--group-floors",
        "30,40",
        "--repetitions",
        "200",
        "--seed",
        "7",
        "--output",
        str(output),
    ]


def test_power_cli_real_bound_workflow_cannot_invent_groups(power_run, monkeypatch, capsys):
    from magicite.eval.__main__ import main

    frozen, actual, cache, manifest, root, envelope = power_run
    source, output = root / "power-input.json", root / "power-output.json"
    args = power_cli_arguments(power_run, source, output)
    monkeypatch.setattr(consumer, "cli_actual", lambda *args: actual)
    assert main(args) != 0
    assert "development grouping/model support missing" in capsys.readouterr().err
    assert not output.exists() and not data.access_path(frozen).exists()


@pytest.mark.parametrize("mutation", ["model", "input"])
def test_power_cli_rechecks_after_simulation(power_run, monkeypatch, mutation, capsys):
    from magicite.eval import grouped_evaluation
    from magicite.eval.__main__ import main

    frozen, actual, cache, manifest, root, envelope = power_run
    source, output = root / "power-input-drift.json", root / "power-output-drift.json"
    args = power_cli_arguments(power_run, source, output)
    monkeypatch.setattr(consumer, "cli_actual", lambda *args: actual)

    def simulated_control(*args, **kwargs):
        if mutation == "model":
            (cache / "model_optimized.onnx").write_bytes(b"changed during numerical control")
        else:
            source.write_text("{}")
        return {"schema": "synthetic-control/1", "status": "inconclusive", "qualifying": False}

    monkeypatch.setattr(grouped_evaluation, "plan_grouped_power", simulated_control)
    assert main(args) != 0
    assert not output.exists()
    assert not data.access_path(frozen).exists()
