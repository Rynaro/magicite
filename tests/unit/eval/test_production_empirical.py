"""Production empirical mechanism tests; no fixture vectors qualify production."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magicite.eval import production
from magicite.eval.operator_cli import cmd_run_paired_policies, cmd_run_retrieval


def test_production_label_refuses_legacy_hash_ranker_before_inputs(tmp_path):
    with pytest.raises(ValueError, match="production adapter"):
        cmd_run_retrieval(
            experiment_path=tmp_path / "missing",
            corpus_path=tmp_path / "missing",
            split="development",
            provider="production",
            output=tmp_path / "out",
        )
    with pytest.raises(ValueError, match="synthetic fixture"):
        cmd_run_paired_policies(
            incumbent="dense-v1",
            candidate="hybrid-rrf-v1",
            experiment_path=tmp_path / "missing",
            corpus_path=tmp_path / "missing",
            n_resamples=10,
            seed=0,
            output=tmp_path / "out",
            provider="production",
        )


def test_paired_cli_propagates_production_refusal(tmp_path, capsys):
    from magicite.eval.__main__ import main

    code = main(
        [
            "run-paired-policies",
            "--incumbent",
            "dense-v1",
            "--candidate",
            "hybrid-rrf-v1",
            "--experiment",
            str(tmp_path / "missing"),
            "--corpus",
            str(tmp_path / "missing"),
            "--provider",
            "production",
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert code != 0
    assert "synthetic fixture" in capsys.readouterr().err


def test_pre_execution_model_manifest_rejects_missing_and_changed_bytes(tmp_path):
    names = [
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"owned test bytes")
    manifest = production.freeze_model(tmp_path)
    production.verify_model(tmp_path, manifest)
    (tmp_path / "model_optimized.onnx").write_bytes(b"different owned bytes")
    with pytest.raises(ValueError, match="changed"):
        production.verify_model(tmp_path, manifest)
    (tmp_path / "model_optimized.onnx").unlink()
    with pytest.raises(ValueError, match="missing"):
        production.verify_model(tmp_path, manifest)
    assert manifest["publisher_authenticated"] is False


def test_model_symlink_escape_cannot_enter_expected_manifest(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (cache / "model_optimized.onnx").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        production.freeze_model(cache)


def test_actual_router_projection_rejects_annotations_and_preserves_dispatch(monkeypatch, tmp_path):
    query = {"query_id": "q", "query_text": "public diagnostic", "compatibility_context": {}}
    seen = []
    decision = SimpleNamespace(
        config_digest="config",
        status="selected",
        operational_error=None,
        selected_ids=["unlabeled-distractor"],
        exclusions=[],
        selected_content_digests={"unlabeled-distractor": "digest"},
        model_digest="model",
        registry_digest="registry",
        index_generation_id="generation",
    )

    def actual_call(cfg, conn, embedder, **arguments):
        seen.append(arguments)
        return SimpleNamespace(
            policy_id=cfg.routing_policy,
            policy_family="stable",
            policy_digest="policy",
            decision=decision,
            candidates=[SimpleNamespace(id="unlabeled-distractor", score=0.9)],
        )

    monkeypatch.setattr(production.router, "route", actual_call)
    provider = production.ProductionEmbedder(tmp_path)
    adapter = production.ActualRouter(SimpleNamespace(routing_policy="dense-v1"), None, provider)
    first = adapter.predict(query)
    # Scoring annotations can change freely; they never enter runtime projection.
    annotations = {"relevance": {"gold": 1}, "no_match": True}
    assert adapter.predict(query) == first
    assert first["candidate_ids"] == ["unlabeled-distractor"]
    assert all(set(call) == {"query", "context", "k"} for call in seen)
    assert all(call["k"] == 5 for call in seen)
    production.ActualRouter(
        SimpleNamespace(routing_policy="dense-v1"), None, provider, rank_depth=10
    ).predict(query)
    assert seen[-1]["k"] == 10
    with pytest.raises(ValueError, match="only query"):
        adapter.predict({**query, **annotations})


def test_inventory_is_complete_without_any_gold_annotation(tmp_path):
    from magicite.engram import parser

    source = Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams"
    rows = []
    for path in sorted(source.glob("*.egr.md")):
        target = tmp_path / path.name
        target.write_bytes(path.read_bytes())
        artifact, _ = parser.load_artifact_file(target, registry_root=tmp_path)
        rows.append(
            {
                "id": artifact.id,
                "name": artifact.name,
                "path": path.name,
                "sha256": production.sha256(target),
                "bytes": target.stat().st_size,
            }
        )
    assert len(production.verify_inventory(tmp_path, rows)) == len(rows) > 1
    target.write_bytes(b"tamper")
    with pytest.raises(ValueError, match="digest"):
        production.verify_inventory(tmp_path, rows)


def test_descriptive_denominators_include_false_selections_and_abstention():
    labels = [
        {"query_id": "a", "relevance": {"good": 1}},
        {"query_id": "b", "relevance": {"good": 1}},
        {"query_id": "n", "relevance": {}},
    ]
    predictions = [
        {"query_id": "a", "candidate_ids": ["good"], "selected_ids": ["good"]},
        {"query_id": "b", "candidate_ids": [], "selected_ids": []},
        {"query_id": "n", "candidate_ids": ["wrong"], "selected_ids": ["wrong"]},
    ]
    report = production.descriptive_quality(predictions, labels)
    assert report["hit_at_1"]["value"] == 0.5
    assert report["selection_coverage"]["value"] == 2 / 3
    assert report["selective_accuracy"]["value"] == 0.5
    assert report["no_match_false_selections"]["value"] == 1
    assert report["qualification"] == "UNEVALUATED"


def test_runner_scoring_changes_do_not_change_actual_router_or_full_pool(tmp_path, monkeypatch):
    import importlib.util

    import numpy as np

    from magicite.core import writer_guard
    from magicite.embeddings.fastembed_provider import FastEmbedProvider

    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "empirical_runner", root / "scripts/qualify_production_empirical.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    source = root / "tests/fixtures/toy-registry/engrams"
    from magicite.engram import parser

    inventory = []
    for path in sorted(source.glob("*.egr.md")):
        artifact, _ = parser.load_artifact_file(path, registry_root=source)
        inventory.append(
            {
                "id": artifact.id,
                "name": artifact.name,
                "path": str(path.relative_to(root)),
                "sha256": production.sha256(path),
                "bytes": path.stat().st_size,
            }
        )

    class UnitModel:
        def embed(self, documents):
            return [np.ones(384, dtype=np.float32) for _ in documents]

    # Unit-only factory exercises the real router, not production measurements.
    embedder = FastEmbedProvider(factory=lambda **kwargs: UnitModel())
    original = writer_guard.resolve_custody
    work = tmp_path / "work"
    work.mkdir()
    cfg, conn = runner.prepare_registry(work, inventory, "dense-v1", embedder)
    try:
        adapter = production.ActualRouter(cfg, conn, embedder)
        query = {"query_id": "unit", "query_text": "rollback proton", "compatibility_context": {}}
        before = adapter.predict(query)
        labels = [{"query_id": "unit", "relevance": {inventory[0]["id"]: 1}, "negative_ids": []}]
        production.descriptive_quality([before], labels)
        changed = [
            {
                "query_id": "unit",
                "relevance": {},
                "negative_ids": list(before["candidate_ids"]),
                "no_match": True,
            }
        ]
        production.descriptive_quality([before], changed)
        after = adapter.predict(query)
        for field in ("candidate_ids", "scores", "selected_ids", "abstained"):
            assert before[field] == after[field]
        assert conn.execute("SELECT COUNT(*) FROM engram").fetchone()[0] == len(inventory)
        assert len(inventory) > len(labels)
    finally:
        conn.close()
        _, provider = writer_guard.resolve_custody(cfg)
        provider.store.close()
        monkeypatch.setattr(writer_guard, "resolve_custody", original)


def _load_empirical_runner(monkeypatch):
    import importlib.util

    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "empirical_guard_runner", root / "scripts/qualify_production_empirical.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_snapshot_copies_are_exact_and_tamper_refused(tmp_path, monkeypatch):
    runner = _load_empirical_runner(monkeypatch)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "admitted-state").write_bytes(b"same authority, graph, plasticity and content")
    expected = {"files": runner.snapshot_files(snapshot)}
    runner.copy_snapshot(snapshot, tmp_path / "dense", expected)
    runner.copy_snapshot(snapshot, tmp_path / "adaptive", expected)
    assert runner.snapshot_files(tmp_path / "dense") == runner.snapshot_files(tmp_path / "adaptive")
    (snapshot / "admitted-state").write_bytes(b"tamper")
    with pytest.raises(ValueError, match="snapshot changed"):
        runner.copy_snapshot(snapshot, tmp_path / "bad", expected)


def test_worker_model_guard_precedes_provider_or_query(tmp_path, monkeypatch):
    import json

    runner = _load_empirical_runner(monkeypatch)
    cache = tmp_path / "cache"
    cache.mkdir()
    expected_file = tmp_path / "model.json"
    expected_file.write_text(
        json.dumps(
            {
                "schema": "magicite/observed-model/1",
                "model_name": "BAAI/bge-small-en-v1.5",
                "files": {"absent": "0" * 64},
            }
        )
    )
    manifest = tmp_path / "worker.json"
    manifest.write_text(
        json.dumps(
            {
                "source_commit": "unit",
                "source_inputs": {},
                "model_manifest": {"path": str(expected_file), "sha256": production.sha256(expected_file)},
                "model_cache": str(cache),
            }
        )
    )
    monkeypatch.setattr(runner, "assert_candidate", lambda *args: None)
    called = []
    monkeypatch.setattr(runner, "ProductionEmbedder", lambda *args: called.append("provider"))
    with pytest.raises(ValueError, match="missing"):
        runner.worker(manifest, "dense-v1", "quality", 0, tmp_path / "out")
    assert called == [] and not (tmp_path / "out").exists()


@pytest.mark.parametrize("consumer", ["retrieval", "paired", "abstention"])
def test_final_fixture_consumer_explicit_classification_before_predictions(tmp_path, monkeypatch, consumer):
    from magicite.eval import operator_cli as cli

    seen = []
    monkeypatch.setattr(cli, "_load_experiment", lambda p: object())
    monkeypatch.setattr(cli, "_load_corpus", lambda p: object())

    def seal(exp, corpus, **kw):
        seen.append(kw)
        return ["probe denies before predictions"]

    monkeypatch.setattr(cli, "validate_experiment_corpus_seal", seal)
    monkeypatch.setattr(cli, "run_predictions", lambda *a, **kw: pytest.fail("predictions before seal"))
    args = dict(experiment_path=tmp_path / "exp", corpus_path=tmp_path / "corpus", output=tmp_path / "out")
    with pytest.raises(ValueError, match="probe denies"):
        if consumer == "retrieval":
            cli.cmd_run_retrieval(**args, split="final", provider="hashing")
        elif consumer == "paired":
            cli.cmd_run_paired_policies(
                **args, incumbent="dense-v1", candidate="hybrid-rrf-v1", n_resamples=2, seed=0
            )
        else:
            cli.cmd_run_abstention_gate(**args, calibration_split="calibration", final_split="final")
    assert seen == [{"fixture_classification": "synthetic_hashing_diagnostic"}]
    assert not (tmp_path / "out").exists()


def test_unknown_retrieval_provider_cannot_fall_back_to_fixture(tmp_path):
    with pytest.raises(ValueError, match="production adapter"):
        cmd_run_retrieval(
            experiment_path=tmp_path / "missing",
            corpus_path=tmp_path / "missing",
            split="final",
            provider="unclassified",
            output=tmp_path / "out",
        )
