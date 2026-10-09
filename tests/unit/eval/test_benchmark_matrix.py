"""Local quality attachment preserves actual matrix provenance and nonqualification."""

from copy import deepcopy

import pytest
from tests.unit.eval import test_protected_benchmark as fixtures

from magicite.eval import protected_benchmark as benchmark
from magicite.eval.digests import sha256_json


def test_bound_quality_attachment_rejects_incomplete_or_incompatible_results(
    tmp_path, cfg, db_conn, embedder
):
    run = fixtures.run.__wrapped__(tmp_path, cfg, db_conn, embedder)
    _, actual, cache, root, commitment, _ = fixtures.complete.__wrapped__(run)
    report = benchmark.final(commitment, root / "result.json", actual, model_cache=cache)
    reference = benchmark.quality_reference(
        report, report["profile"], observed_model=report["model_manifest"]
    )
    assert reference["sha256"] == sha256_json(report) and reference["qualifying"] is False
    for mutation in ("empty", "missing", "foreign", "profile", "environment", "model", "source"):
        altered = deepcopy(report)
        matrix = deepcopy(report["profile"])
        model = deepcopy(report["model_manifest"])
        if mutation == "empty":
            altered["arms"] = {}
            altered["trace_digest"] = sha256_json(altered["arms"])
        elif mutation == "missing":
            altered["arms"].pop("dense-v1")
            altered["trace_digest"] = sha256_json(altered["arms"])
        elif mutation == "foreign":
            altered["arms"]["dense-v1"][0]["query_id"] = "foreign"
            altered["trace_digest"] = sha256_json(altered["arms"])
        elif mutation == "profile":
            matrix["profile"]["profile_id"] = "small-100"
        elif mutation == "environment":
            matrix["fingerprint"]["platform"] = "other"
        elif mutation == "model":
            model["files"]["model_optimized.onnx"] = "a" * 64
        else:
            altered["source_commit"] = "1" * 40
        with pytest.raises(ValueError):
            benchmark.quality_reference(altered, matrix, observed_model=model)
    assert report["E6"] == report["GA"] == "UNEVALUATED"


def _matrix_module():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts/run_benchmark_matrix.py"
    spec = importlib.util.spec_from_file_location("e6_matrix", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_root_accepts_only_empty_shipped_bootstrap(tmp_path):
    from magicite.config import Config
    from magicite.storage import db

    module = _matrix_module()
    root = tmp_path.resolve()
    config = Config(project_root=root)
    config.ensure_dirs()
    connection = db.connect(config.db_path)
    connection.close()
    receipt = module._clean_project_root(root, protected=False)
    assert receipt["project_root"] == str(root)
    assert receipt["empty_table_counts"]["engram"] == 0
    connection = db.connect(config.db_path)
    connection.execute("UPDATE evidence_meta SET last_sequence=1")
    connection.close()
    with pytest.raises(ValueError, match="evidence bootstrap"):
        module._clean_project_root(root, protected=False)


def test_explicit_root_refuses_workload_and_alias(tmp_path):
    module = _matrix_module()
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (root / "previous-result.json").write_text("{}")
    with pytest.raises(ValueError, match="preexisting workload"):
        module._clean_project_root(root, protected=False)
    link = tmp_path / "alias"
    link.symlink_to(root)
    with pytest.raises(ValueError, match="canonical"):
        module._clean_project_root(link, protected=False)


def test_explicit_protected_root_requires_authentic_custody(tmp_path):
    module = _matrix_module()
    from magicite.core.trust_custodian import CustodianError

    with pytest.raises(CustodianError):
        module._clean_project_root(tmp_path.resolve(), protected=True)


def test_frozen_model_refuses_changed_bytes_and_library_inventory(tmp_path, monkeypatch):
    import importlib.metadata
    import json

    from magicite.eval.production import freeze_model

    module = _matrix_module()
    cache = tmp_path / "model"
    cache.mkdir()
    for name in [
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ]:
        (cache / name).write_text("frozen bytes")
    manifest = tmp_path / "manifest.json"
    expected = freeze_model(cache)
    manifest.write_text(json.dumps(expected))
    assert module._verify_frozen_model(cache, manifest) == expected
    (cache / "tokenizer.json").write_text("changed")
    with pytest.raises(ValueError, match="bytes changed"):
        module._verify_frozen_model(cache, manifest)
    (cache / "tokenizer.json").write_text("frozen bytes")
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "foreign")
    with pytest.raises(ValueError, match="library inventory"):
        module._verify_frozen_model(cache, manifest)


def test_frozen_cold_preflight_occurs_before_original_wall_timer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from magicite.config import Config

    module = _matrix_module()
    events = []
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda args, **kwargs: events.append(("child", args)) or SimpleNamespace(stdout=""),
    )
    monkeypatch.setattr(module.time, "perf_counter", lambda: events.append(("timer",)) or len(events))
    elapsed = module._cold_process_ready(
        Config(project_root=tmp_path),
        tmp_path / "db",
        "query",
        "fastembed",
        model_cache=tmp_path / "cache",
        model_manifest=tmp_path / "manifest",
    )
    assert elapsed == 2
    assert [event[0] for event in events] == ["child", "timer", "child", "timer"]
    assert "verify_model" in events[0][1][2]
    assert "verify_model" not in events[2][1][2]
    assert "cache_dir=cache, offline=True" in events[2][1][2]


def test_root_and_model_options_are_paired_and_propagate(tmp_path, monkeypatch):
    import sys

    module = _matrix_module()
    monkeypatch.setattr(sys, "argv", ["matrix", "--model-cache", str(tmp_path)])
    with pytest.raises(SystemExit):
        module._parse_args()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "matrix",
            "--provider",
            "production",
            "--project-root",
            str(tmp_path),
            "--model-cache",
            str(tmp_path / "cache"),
            "--model-manifest",
            str(tmp_path / "manifest"),
        ],
    )
    args = module._parse_args()
    assert args.project_root == tmp_path and args.model_cache == tmp_path / "cache"


def test_portable_json_rows_do_not_split_unicode_payload(tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts/prepare_performance_corpus.py"
    spec = importlib.util.spec_from_file_location("e6_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = tmp_path / "rows.jsonl"
    records = [{"text": "full\u2028article\u2029payload"}, {"text": "next\narticle"}]
    rows.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
    assert list(module.batches(rows)) == [records]


def test_repetitions_preflight_all_roots_and_forward_frozen_model(tmp_path, monkeypatch):
    import sys

    module = _matrix_module()
    base = tmp_path.resolve()
    cache, manifest = base / "cache", base / "manifest"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "matrix",
            "--provider",
            "production",
            "--profile",
            "small-100",
            "--project-root",
            str(base),
            "--model-cache",
            str(cache),
            "--model-manifest",
            str(manifest),
        ],
    )
    args = module._parse_args()
    roots = []
    monkeypatch.setattr(module, "_clean_project_root", lambda root, **kwargs: roots.append(root))
    commands = []

    def stop_after_command(command, **kwargs):
        commands.append(command)
        raise RuntimeError("captured before launch")

    monkeypatch.setattr(module.subprocess, "run", stop_after_command)
    with pytest.raises(RuntimeError, match="captured before launch"):
        module._run_repetitions(args)
    assert roots == [base / f"repetition-{index:03d}" for index in range(3)]
    command = commands[0]
    assert command[-2:] == ["--project-root", str(roots[0])]
    assert command[command.index("--model-cache") + 1] == str(cache)
    assert command[command.index("--model-manifest") + 1] == str(manifest)
