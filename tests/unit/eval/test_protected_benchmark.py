"""Synthetic actual-runtime lifecycle mechanics, never authentic benchmark evidence."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from tests.unit.eval import test_calibration_consumer as fixture_consumer
from tests.unit.eval.test_data_readiness import evidence, mutate
from tests.unit.eval.test_grouped_evaluation import protocol

from magicite.core import calibration, routing_policy
from magicite.core.comparison_budget import ComparisonBudget
from magicite.core.evaluation_context import EvaluationContext
from magicite.errors import InvalidInputError
from magicite.eval import calibration_consumer as consumer
from magicite.eval import data_readiness as data
from magicite.eval import grouped_evaluation as grouped
from magicite.eval import protected_benchmark as benchmark
from magicite.eval.digests import sha256_bytes, sha256_json


@pytest.fixture
def run(tmp_path, cfg, db_conn, embedder):
    return fixture_consumer.run.__wrapped__(tmp_path, cfg, db_conn, embedder)


def prepare(run, *, adequate=False):
    _, actual, cache, manifest, root = run
    actual.rank_depth = 3
    actual.comparison_budget = ComparisonBudget(3, 3, 6, 3, 1)
    packet = root / "packet" / "packet.json"
    if adequate:
        # Actual arms determine contrasting synthetic targets only while authoring.
        query = {
            "query_id": "authoring",
            "query_text": "orchid orchid orchid orchid",
            "compatibility_context": {},
        }
        previews = {
            p: benchmark._arm(actual, p, actual.comparison_budget).predict(query, raw_trace=True)
            for p in benchmark.ARMS
        }
        hybrid = previews[benchmark.ARMS[-1]]["selected_ids"][0]
        other = next(
            previews[p]["selected_ids"][0]
            for p in benchmark.SIMPLE
            if previews[p]["selected_ids"][0] != hybrid
        )
        real_ids = [r[0] for r in actual.conn.execute("SELECT id FROM engram ORDER BY id")]
        original_pool = json.loads((packet.parent / "pool.json").read_bytes())
        (packet.parent / "third.txt").write_text("third synthetic body")
        original_pool.append(
            {
                **original_pool[-1],
                "artifact": {
                    "path": "third.txt",
                    "sha256": sha256_bytes((packet.parent / "third.txt").read_bytes()),
                },
            }
        )
        for row, cid in zip(original_pool, real_ids, strict=True):
            row["candidate_id"] = cid
        mutate(packet, "pool", lambda rows: rows.__setitem__(slice(None), original_pool))
        doc = json.loads(packet.read_bytes())
        doc["dataset"]["pool_count"] = 3
        packet.write_text(json.dumps(doc))
        pool_digest = doc["files"]["pool"]["sha256"]
        for name in ("judgment1", "judgment2"):
            evidence(packet, name, lambda row: row.update(pool_sha256=pool_digest))

        def real_labels(rows):
            for row in rows:
                row["relevance"] = {
                    real_ids[int(cid[-1]) - 1]: value for cid, value in row["relevance"].items()
                }

        mutate(packet, "labels", real_labels)
        # Preserve original calibration/final rows, materialize 30 independently DECLARED fixture groups.
        for field in ("runtime", "labels", "partitions", "provenance"):
            rows = json.loads((packet.parent / (field + ".json")).read_bytes())
            new = []
            for i in range(30):
                qid = "development-" + str(i)
                if field == "runtime":
                    row = {
                        "query_id": qid,
                        "query_text": "orchid orchid orchid orchid",
                        "compatibility_context": {},
                    }
                elif field == "labels":
                    row = {"query_id": qid, "kind": "positive", "relevance": {other if i < 22 else hybrid: 1}}
                elif field == "partitions":
                    row = {"query_id": qid, "split": "development"}
                else:
                    row = copy.deepcopy(rows[0])
                    row["query_id"] = qid
                    source = row["source"]
                    source["source_id"] = "fixture-development-" + str(i)
                    source["group_keys"] = {k: k + "-development-" + str(i) for k in source["group_keys"]}
                    source["evidence_ref"] = "development-source-" + str(i)
                    source_doc = json.loads((packet.parent / "source0.json").read_bytes())
                    source_doc.update(source_id=source["source_id"], group_keys=source["group_keys"])
                    source_path = packet.parent / (source["evidence_ref"] + ".json")
                    source_path.write_text(json.dumps(source_doc))
                    p = json.loads(packet.read_bytes())
                    p["evidence"][source["evidence_ref"]] = {
                        "path": source_path.name,
                        "sha256": sha256_bytes(source_path.read_bytes()),
                    }
                    packet.write_text(json.dumps(p))
                new.append(row)
            mutate(packet, field, lambda rows, new=new: rows.extend(new))
    else:
        mutate(packet, "partitions", lambda rows: rows[0].update(split="development"))
    p = protocol()
    p.update(comparison_budget=actual.comparison_budget.to_dict(), paired_policy_ids=benchmark.ARMS)
    mutate(
        packet,
        "preregistration",
        lambda row: row.update(
            statistical_protocol=p,
            power_assumptions={"development_representative": True, "evidence_digests": ["a" * 64]},
        ),
    )
    freeze = root / "benchmark-freeze.json"
    data.freeze_packet(packet, freeze, source_commit="1" * 40, runner_sha256="2" * 64)
    plan = benchmark.make_plan(
        freeze, actual, manifest, experiment_id=p["experiment_id"], budget=actual.comparison_budget
    )
    plan_path = root / "benchmark-plan.json"
    plan_path.write_text(json.dumps(plan))
    return freeze, plan_path, actual, cache, manifest, root


def test_internal_context_uses_frozen_actual_finalizer_and_rejects_bad_bindings(run):
    _, actual, _, _, _ = run
    actual.rank_depth = 3
    budget = ComparisonBudget(3, 3, 6, 3, 1)
    cfg = replace(actual.cfg, routing_policy="dense-v1")
    artifact = calibration.fit_abstention(
        [calibration.CalibrationExample("synthetic", 0.0, 0.0, True)],
        cfg=cfg,
        policy_id="dense-v1",
        policy_digest=routing_policy.compute_policy_digest("dense-v1", cfg),
        config_digest=routing_policy.compute_config_digest(cfg),
        rejection_queries=(),
    )
    model, reason = calibration.fit_probability(
        [0.0, 1.0], [False, True], calibration_input_digest="a" * 64, rank_depth=3
    )
    assert reason is None
    artifact = calibration.with_probability(artifact, model, evaluation_budget_digest=budget.digest)
    arm = benchmark._arm(actual, "dense-v1", budget, artifact)
    q = {"query_id": "fixture", "query_text": "orchid", "compatibility_context": {}}
    row = arm.predict(q, raw_trace=True)
    assert row["calibration_digest"] == artifact.digest and row["evaluation_only"] is True
    assert "no operator authority" in row["confidence_reason"]
    assert actual.cfg.routing_policy == "experimental/sparse-v1"
    bad = replace(arm.evaluation_context, config_digest="b" * 64)
    arm.evaluation_context = bad
    with pytest.raises(InvalidInputError, match="context"):
        arm.predict(q)
    arm.evaluation_context = {"artifact": artifact.to_dict()}
    with pytest.raises(InvalidInputError, match="internal"):
        arm.predict(q)
    # Public compatibility input cannot override thresholds or select the internal seam.
    row = actual.predict(
        {**q, "compatibility_context": {"_evaluation_context": artifact.to_dict()}}, raw_trace=True
    )
    assert row["calibration_digest"] is None and row["evaluation_only"] is False


@pytest.mark.parametrize("mutation", ["unknown", "arms", "seed", "depth", "research", "digest"])
def test_strict_plan_rejects_before_development_scoring(run, monkeypatch, mutation):
    freeze, path, actual, cache, model, root = prepare(run)
    plan = json.loads(path.read_bytes())
    if mutation == "unknown":
        plan["pass"] = True
    elif mutation == "arms":
        plan["arms"] = plan["arms"] + [plan["arms"][0]]
    elif mutation == "seed":
        plan["seed"] = True
    elif mutation == "depth":
        plan["rank_depth"] += 1
    elif mutation == "research":
        plan["research_arm"]["comparative"] = True
    else:
        plan["packet_freeze_digest"] = "f" * 64
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(actual, "predict", lambda *a, **k: pytest.fail("invalid plan executed actual scores"))
    with pytest.raises(ValueError):
        benchmark.develop(
            freeze,
            path,
            root / "invalid.json",
            actual,
            model_cache=cache,
            model_manifest=model,
            group_floors=[30],
            repetitions=1,
        )
    assert not data.access_path(freeze).exists()


def test_inadequate_actual_development_records_power_failure_and_denies_fit(run):
    freeze, plan, actual, cache, model, root = prepare(run)
    path = root / "development.json"
    receipt = benchmark.develop(
        freeze, plan, path, actual, model_cache=cache, model_manifest=model, group_floors=[30], repetitions=1
    )
    assert receipt["body"]["status"] == "incomplete_power"
    assert "grouping/model support" in receipt["body"]["power_error"]
    assert set(receipt["body"]["arms"]) == set(benchmark.ARMS)
    assert receipt["body"]["research"]["comparative"] is False
    assert receipt["body"]["research"]["rows"][0]["policy_id"] == benchmark.RESEARCH["policy_id"]
    with pytest.raises(ValueError, match="power report required"):
        benchmark.fit(freeze, path, root / "fit.json", actual, model_cache=cache, model_manifest=model)
    assert not consumer.final_owner(data.verify_freeze(freeze)).exists()


def test_actual_whole_workflow_power_linked_freeze_fit_final_replay_and_drift(run, monkeypatch):
    freeze, plan, actual, cache, model, root = prepare(run, adequate=True)
    development = root / "development.json"
    result = benchmark.develop(
        freeze,
        plan,
        development,
        actual,
        model_cache=cache,
        model_manifest=model,
        group_floors=[30],
        repetitions=1,
    )
    assert result["body"]["power_error"] is None
    power = result["body"]["power_report"]
    assert power["diagnostic"] is True and power["status"] == "inconclusive"
    assert power["n_original_development_groups"] == 30
    commitment = root / "commitment.json"
    fitted = benchmark.fit(freeze, development, commitment, actual, model_cache=cache, model_manifest=model)
    successor = data.verify_freeze(data.Path(fitted["body"]["successor_freeze"]))
    assert successor["input_root"] == data.verify_freeze(freeze)["input_root"]
    assert successor["binding"]["statistical_projection"]["protocol"]["power_plan"]["report"] == power
    assert all(
        fitted["body"]["fits"][p]["candidate"]["artifact"]["evaluation_budget_digest"]
        == actual.comparison_budget.digest
        for p in benchmark.ARMS
    )
    assert not data.access_path(freeze).exists()
    original = data.open_final_labels

    def interrupted(*args, **kwargs):
        assert consumer.final_owner(data.verify_freeze(freeze)).exists()
        assert benchmark._stage(data.verify_freeze(freeze), "final-intent", fitted["identity"]).exists()
        original(*args, **kwargs)
        raise RuntimeError("synthetic interrupted after durable decode intent")

    monkeypatch.setattr(data, "open_final_labels", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        benchmark.final(commitment, root / "interrupted.json", actual, model_cache=cache)
    assert (root / "interrupted.json.failure.json").exists()
    assert data.access_path(data.Path(fitted["body"]["successor_freeze"])).exists()
    monkeypatch.setattr(data, "open_final_labels", original)
    report = benchmark.final(commitment, root / "final.json", actual, model_cache=cache, replay=True)
    assert report["access"] == "exposed_replay" and report["qualifying"] is False
    assert report["GA"] == report["E6"] == "UNEVALUATED"
    assert all(len(rows) == 1 for rows in report["arms"].values())
    for policy in benchmark.ARMS:
        assert (
            report["arms"][policy][0]["calibration_digest"]
            == fitted["body"]["fits"][policy]["candidate"]["artifact"]["digest"]
        )
    assert report["research"]["comparative"] is False
    assert report["profile"]["envelope_check"]["ok"] is False
    assert report["profile"]["measurements"] == {}
    assert report["profile"]["process_id"] > 0
    again = benchmark.final(commitment, root / "replay.json", actual, model_cache=cache, replay=True)
    assert again["access"] == "exposed_replay"
    assert not (actual.cfg.data_dir / "policy-store" / "state.json").exists()
    with pytest.raises(ValueError, match="exposed"):
        benchmark.fit(
            freeze, development, root / "newfit.json", actual, model_cache=cache, model_manifest=model
        )
    actual.cfg.abstention_margin_threshold = 99
    with pytest.raises(ValueError, match="drift"):
        benchmark.final(commitment, root / "drift.json", actual, model_cache=cache, replay=True)


def test_selection_rehashed_foreign_labels_not_bound_to_power(run):
    freeze, plan, actual, cache, model, root = prepare(run)
    receipt = benchmark.develop(
        freeze,
        plan,
        root / "development.json",
        actual,
        model_cache=cache,
        model_manifest=model,
        group_floors=[30],
        repetitions=1,
    )
    envelope = copy.deepcopy(receipt["body"]["power_input"])
    selection = envelope["input"]["incumbent_selection"]
    selection["labels"][0]["relevance"] = {"foreign": 1}
    selection["identity"] = sha256_json({k: v for k, v in selection.items() if k != "identity"})
    envelope["identity"] = sha256_json(envelope["input"])
    with pytest.raises(ValueError):
        grouped.validate_development_power_envelope(envelope)
    with pytest.raises(ValueError):
        consumer.validate_power_input(envelope, freeze, actual, model_cache=cache, model_manifest=model)


def test_production_witness_rejects_eval_and_missing_approved_calibration(run):
    _, actual, cache, model, root = run
    q = [{"query_id": "witness", "query_text": "orchid", "compatibility_context": {}}]
    with pytest.raises(ValueError, match="approved"):
        benchmark.production_witness(
            actual, q, root / "absent.json", model_cache=cache, model_manifest=model, profile_id="ci-smoke"
        )
    actual.evaluation_context = EvaluationContext.bind(actual.cfg, "dense-v1", 5, None)
    with pytest.raises(ValueError, match="ordinary"):
        benchmark.production_witness(
            actual, q, root / "eval.json", model_cache=cache, model_manifest=model, profile_id="ci-smoke"
        )


@pytest.fixture
def complete(run):
    freeze, plan, actual, cache, model, root = prepare(run, adequate=True)
    development = root / "development.json"
    benchmark.develop(
        freeze,
        plan,
        development,
        actual,
        model_cache=cache,
        model_manifest=model,
        group_floors=[30],
        repetitions=1,
    )
    commitment = root / "commitment.json"
    fitted = benchmark.fit(freeze, development, commitment, actual, model_cache=cache, model_manifest=model)
    return freeze, actual, cache, root, commitment, fitted


def test_concurrent_benchmark_and_legacy_owner_share_packet_root(complete, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tests.support.custody_adapter import threaded_calls

    from magicite.core import writer_guard
    from magicite.eval.production import ActualRouter
    from magicite.storage.db import connect

    freeze, actual, cache, root, commitment, fitted = complete
    _, provider = writer_guard.resolve_custody(actual.cfg)
    threaded_calls(provider, monkeypatch)
    barrier = Barrier(2)

    def attempt(index):
        conn = connect(actual.cfg.db_path, migrate=False)
        local = ActualRouter(
            actual.cfg,
            conn,
            actual.embedder,
            rank_depth=actual.rank_depth,
            comparison_budget=actual.comparison_budget,
        )
        barrier.wait(timeout=10)
        try:
            return benchmark.final(
                commitment, root / ("concurrent-" + str(index) + ".json"), local, model_cache=cache
            )
        except FileExistsError:
            return "lost-owner"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(isinstance(r, dict) and r["status"] == "complete" for r in results) == 1
    assert results.count("lost-owner") == 1
    owner = consumer.final_owner(data.verify_freeze(freeze))
    assert json.loads(owner.read_bytes())["benchmark_commitment"] == fitted["identity"]
    # Existing legacy single-arm evaluator cannot claim fresh ownership on this same final partition.
    fit_path = benchmark._stage(
        data.verify_freeze(freeze), "fit-experimental-sparse-v1", fitted["body"]["development_identity"]
    )
    arm = benchmark._arm(actual, "experimental/sparse-v1", actual.comparison_budget)
    with pytest.raises(FileExistsError):
        consumer.evaluate_frozen_calibration(
            data.Path(fitted["body"]["successor_freeze"]),
            fit_path,
            root / "legacy-final.json",
            arm,
            model_cache=cache,
        )


def test_rehashed_completed_trace_and_fit_substitution_rejected(complete):
    freeze, actual, cache, root, commitment, fitted = complete
    benchmark.final(commitment, root / "result.json", actual, model_cache=cache)
    stored = benchmark._stage(data.verify_freeze(freeze), "result", fitted["identity"])
    before = stored.read_bytes()
    report = json.loads(before)
    report["arms"]["dense-v1"][0]["confidence"] = 0.777
    report["trace_digest"] = sha256_json(report["arms"])
    stored.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="digest changed"):
        benchmark.final(commitment, root / "tampered.json", actual, model_cache=cache, replay=True)
    stored.write_bytes(before)
    fit_entry = fitted["body"]["fits"]["dense-v1"]
    fit_register = consumer.fit_commitment(
        data.verify_freeze(data.Path(fitted["body"]["successor_freeze"])), fit_entry["identity"]
    )
    fit_register.write_text("{}")
    with pytest.raises(ValueError, match="fit commitment"):
        benchmark.final(commitment, root / "fit-tampered.json", actual, model_cache=cache, replay=True)


def test_final_errors_full_denominator_and_failure_exposure(complete, monkeypatch):
    freeze, actual, cache, root, commitment, fitted = complete
    from magicite.eval.production import ActualRouter

    original = ActualRouter.predict

    def operational(self, query, **kwargs):
        row = original(self, query, **kwargs)
        if query["query_id"] == "fixture-q2":
            row.update(
                status="error", operational_error="synthetic-timeout", selected_ids=[], confidence=None
            )
        return row

    monkeypatch.setattr(ActualRouter, "predict", operational)
    report = benchmark.final(commitment, root / "errors.json", actual, model_cache=cache)
    assert report["status"] == "complete_with_errors"
    for summary in report["summaries"].values():
        assert summary["all_query_hit1"] == {"numerator": 0, "denominator": 1, "value": 0}
        assert summary["error_count"] == 1 and summary["selected_ECE"]["ECE"] is None
    assert report["qualifying"] is False


def test_eval_warm_revocation_and_pending_authority_not_bypassed(run, monkeypatch):
    from magicite.core import registry, router

    _, actual, _, _, _ = run
    budget = ComparisonBudget(3, 3, 6, 3, 1)
    actual.rank_depth = 3
    arm = benchmark._arm(actual, "dense-v1", budget)
    query = {"query_id": "warm", "query_text": "orchid", "compatibility_context": {}}
    top = arm.predict(query, raw_trace=True)["raw_candidate_ids"][0]
    registry.review_revoke(
        actual.cfg,
        actual.conn,
        engram_id=top,
        expected_digest=actual.conn.execute(
            "SELECT content_sha256 FROM engram WHERE id=?", (top,)
        ).fetchone()[0],
        actor="synthetic-test",
    )
    assert top not in arm.predict(query, raw_trace=True)["raw_candidate_ids"]
    original = router._resolve_active_policy

    def corrupt(cfg):
        policy, digest, family, _, source, reasons = original(cfg)
        return policy, digest, family, "policy_store_corrupt", source, reasons

    monkeypatch.setattr(router, "_resolve_active_policy", corrupt)
    with pytest.raises(ValueError):
        arm.predict(query)


def test_readonly_approved_probability_witness_and_eval_arm_preserve_pointer(
    cfg, db_conn, embedder, tmp_path, monkeypatch
):
    from tests.unit.core import test_calibration_admission as fixture

    from magicite.core import calibration_admission as admission
    from magicite.core import policy_store
    from magicite.embeddings.fastembed_provider import FastEmbedProvider
    from magicite.eval.production import ActualRouter, freeze_model

    artifact, evidence_doc = fixture.synthetic_bundle.__wrapped__(cfg, db_conn, embedder)
    probability, _ = calibration.fit_probability(
        [0.01, 0.9], [False, True], calibration_input_digest="a" * 64, rank_depth=3
    )
    cal = calibration.with_probability(calibration.CalibrationArtifact.from_dict(artifact), probability)
    artifact = cal.to_dict()
    cache = tmp_path / "model"
    cache.mkdir()
    for name in (
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ):
        (cache / name).write_text("synthetic provider bytes")

    class Provider(FastEmbedProvider):
        def __init__(self):
            self.model_name, self.dim, self._cache_dir = embedder.model_name, embedder.dim, str(cache)

        def embed(self, text):
            return embedder.embed(text)

    actual = ActualRouter(cfg, db_conn, Provider(), rank_depth=3)
    monkeypatch.setattr("magicite.embeddings.get_embedder", lambda cfg: actual.embedder)
    subject = admission.runtime_subject(cfg, db_conn, actual.embedder, cal)
    evidence_doc["subject"] = subject
    for phase in ("fit", "final"):
        evidence_doc[phase]["report"]["subject"] = subject
        if phase == "final":
            evidence_doc[phase]["report"]["fit_digest"] = evidence_doc["fit"]["sha256"]
        evidence_doc[phase]["sha256"] = admission.canonical_digest(evidence_doc[phase]["report"])
    fixture.activate(cfg, (artifact, evidence_doc))
    before = policy_store.policy_store_path(cfg).read_bytes()
    q = {"query_id": "production-witness", "query_text": "orchid", "compatibility_context": {}}
    report = benchmark.production_witness(
        actual,
        [q],
        tmp_path / "witness.json",
        model_cache=cache,
        model_manifest=freeze_model(cache),
        profile_id="ci-smoke",
    )
    assert report["approved_artifact_digest"] == cal.digest
    assert report["rows"][0]["evaluation_only"] is False
    assert report["rows"][0]["calibration_digest"] == cal.digest
    assert report["rows"][0]["confidence"] is not None
    assert policy_store.policy_store_path(cfg).read_bytes() == before
    evaluation = benchmark._arm(actual, "experimental/sparse-v1", ComparisonBudget(3, 3, 6, 3, 1))
    assert evaluation.predict(q, raw_trace=True)["calibration_digest"] is None
    assert policy_store.policy_store_path(cfg).read_bytes() == before


def test_cli_development_and_witness_exit_nonzero_honestly(run, monkeypatch, capsys):
    from magicite.eval.__main__ import main

    freeze, plan, actual, cache, model, root = prepare(run)
    model_path = root / "model.json"
    model_path.write_text(json.dumps(model))
    monkeypatch.setattr(consumer, "cli_actual", lambda *args: actual)

    # CLI is the connection owner; retain the fixture connection for teardown only.
    class Connection:
        def __getattr__(self, name):
            return getattr(original, name)

        def close(self):
            pass

    original = actual.conn
    actual.conn = Connection()
    args = [
        "--project-root",
        str(actual.cfg.project_root),
        "--model-cache",
        str(cache),
        "--model-manifest",
        str(model_path),
        "--rank-depth",
        "3",
    ]
    assert (
        main(
            [
                "benchmark-develop",
                *args,
                "--freeze",
                str(freeze),
                "--plan",
                str(plan),
                "--group-floors",
                "30",
                "--repetitions",
                "1",
                "--output",
                str(root / "cli-dev.json"),
            ]
        )
        == 2
    )
    assert "incomplete_power" in capsys.readouterr().out
    actual.comparison_budget = None
    queries = root / "queries.json"
    queries.write_text(
        json.dumps([{"query_id": "witness", "query_text": "orchid", "compatibility_context": {}}])
    )
    assert (
        main(
            [
                "benchmark-production-witness",
                *args,
                "--queries",
                str(queries),
                "--output",
                str(root / "cli-witness.json"),
            ]
        )
        != 0
    )
    assert "approved" in capsys.readouterr().err
    actual.conn = original


@pytest.mark.parametrize("boundary", ["result", "completion"])
def test_exact_output_publication_interruption_recovery_without_rescoring(complete, monkeypatch, boundary):
    _, actual, cache, root, commitment, _ = complete
    publish = data._publish_immutable

    def interrupted(path, value):
        if path.parent.name == boundary:
            raise RuntimeError("synthetic interruption at " + boundary)
        return publish(path, value)

    monkeypatch.setattr(data, "_publish_immutable", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        benchmark.final(commitment, root / "interrupted-result.json", actual, model_cache=cache)
    monkeypatch.setattr(data, "_publish_immutable", publish)
    monkeypatch.setattr(
        benchmark, "_observations", lambda *a, **k: pytest.fail("replay rescored immutable completed output")
    )
    recovered = benchmark.final(
        commitment, root / "recovered-result.json", actual, model_cache=cache, replay=True
    )
    assert recovered["access"] == "exposed_replay" and recovered["qualifying"] is False
