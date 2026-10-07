#!/usr/bin/env python3
"""Bounded first-party production diagnostic; never E3/E6/GA qualification."""

from __future__ import annotations

# ruff: noqa: E402 -- candidate-source import must precede Magicite imports.
import argparse
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Force this candidate, including when the dependency environment is editable.
sys.path.insert(0, str(ROOT / "src"))
import run_benchmark_matrix as benchmark

from magicite.config import Config
from magicite.core import registry, routing_policy, writer_guard
from magicite.core.routing_policy import POLICY_DENSE_V1, POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
from magicite.eval.production import (
    ActualRouter,
    ProductionEmbedder,
    descriptive_quality,
    runtime_queries,
    sha256,
    verify_inventory,
    verify_model,
)
from magicite.eval.scale import latency_percentiles_ms, path_size_gib, process_rss_gib
from magicite.storage import db

POLICIES = (POLICY_DENSE_V1, POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1)
ORACLE_FREEZE_SHA = "284febcc499e186cf4199f7e332af436eb83b7ca45136d643cc7d266a0b2b069"


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


def read_bound(row: dict) -> object:
    path = Path(row["path"])
    if sha256(path) != row["sha256"]:
        raise ValueError("input digest mismatch: " + path.name)
    return json.loads(path.read_text())


def source_inputs() -> dict[str, str]:
    paths = list((ROOT / "src/magicite").rglob("*.py"))
    paths += [
        ROOT / "scripts/qualify_production_empirical.py",
        ROOT / "scripts/run_benchmark_matrix.py",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "docs/generated/runtime-reference.json",
    ]
    return {str(path.relative_to(ROOT)): sha256(path) for path in sorted(paths)}


def assert_candidate(commit: str, expected: dict[str, str]) -> None:
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != commit:
        raise ValueError("candidate HEAD changed")
    if subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True):
        raise ValueError("clean candidate required")
    if source_inputs() != expected:
        raise ValueError("candidate runtime inputs changed")


def prepare_registry(work: Path, inventory: list[dict], policy: str, embedder: object):
    paths = verify_inventory(ROOT, inventory)
    cfg = Config.load(
        work / "project",
        env={
            "MAGICITE_EMBEDDING_PROVIDER": "fastembed",
            "MAGICITE_EMBEDDING_OFFLINE": "1",
            "MAGICITE_ROUTING_POLICY": policy,
        },
    )
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    benchmark._enroll_disposable_custody(cfg, work / "disposable-custody")
    staging = cfg.project_root / "corpus-input"
    staging.mkdir()
    for source in paths:
        shutil.copyfile(source, staging / source.name)
    result = registry.register(cfg, conn, embedder, path="corpus-input")
    if result.validation_errors or result.ingested != len(paths):
        raise ValueError("complete corpus admission failed: " + str(result.validation_errors))
    benchmark._review_all(cfg, conn, reason="explicit isolated first-party diagnostic; no deployment trust")
    actual = {row[0] for row in conn.execute("SELECT id FROM engram")}
    if actual != {row["id"] for row in inventory}:
        raise ValueError("admitted full inventory differs from frozen corpus")
    return cfg, conn


def snapshot_files(root: Path) -> dict[str, str]:
    values = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("snapshot file escape")
            values[str(path.relative_to(root))] = sha256(path)
    return values


def copy_snapshot(snapshot: Path, output: Path, expected: dict) -> None:
    if snapshot_files(snapshot) != expected["files"]:
        raise ValueError("admitted snapshot changed")
    if output.exists():
        raise ValueError("fresh worker snapshot directory required")
    shutil.copytree(snapshot, output)
    if snapshot_files(output) != expected["files"]:
        raise ValueError("worker admitted snapshot differs")


def worker(manifest: Path, policy: str, kind: str, repetition: int, output: Path) -> dict:
    inputs = json.loads(manifest.read_text())
    assert_candidate(inputs["source_commit"], inputs["source_inputs"])
    expected_model = read_bound(inputs["model_manifest"])
    verify_model(Path(inputs["model_cache"]), expected_model)
    inventory = read_bound(inputs["inventory"])["inventory"]
    queries = runtime_queries(read_bound(inputs["runtime_queries"]))
    if len(inventory) != 30 or len(queries) != 16 or policy not in POLICIES:
        raise ValueError("fixed diagnostic shape/policy required")
    if output.exists():
        raise ValueError("fresh worker directory required")
    # Nothing constructed before expected-model validation can execute embedding.
    embedder = ProductionEmbedder(Path(inputs["model_cache"]))
    network_attempts = []

    def offline_guard(event, args):
        if event == "socket.connect":
            network_attempts.append(event)
            raise RuntimeError("offline empirical execution forbids network")

    sys.addaudithook(offline_guard)
    if kind == "bootstrap":
        output.mkdir()
        started = time.perf_counter()
        cfg, conn = prepare_registry(output, inventory, POLICY_DENSE_V1, embedder)
        registry_id, provider = writer_guard.resolve_custody(cfg)
        admitted = [dict(row) for row in conn.execute("SELECT id, content_sha256 FROM engram ORDER BY id")]
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        provider.store.close()
        build_seconds = time.perf_counter() - started
        verify_model(Path(inputs["model_cache"]), expected_model)
        identity = {
            "registry_id": registry_id,
            "files": snapshot_files(output),
            "admitted": admitted,
            "build_seconds": build_seconds,
            "expected_model_manifest_sha256": inputs["model_manifest"]["sha256"],
            "corpus_inventory_sha256": inputs["inventory"]["sha256"],
            "source_commit": inputs["source_commit"],
            "pid": os.getpid(),
            "production_embed_calls": embedder.embed_calls,
            "production_batch_calls": embedder.batch_calls,
        }
        write(output.parent / "admitted-snapshot.json", identity)
        return identity
    snapshot = read_bound(inputs["admitted_snapshot_manifest"])
    copy_snapshot(Path(inputs["admitted_snapshot"]), output, snapshot)
    cfg = Config.load(
        output / "project",
        env={
            "MAGICITE_EMBEDDING_PROVIDER": "fastembed",
            "MAGICITE_EMBEDDING_OFFLINE": "1",
            "MAGICITE_ROUTING_POLICY": policy,
        },
    )
    benchmark._attach_custody(cfg.project_root, output / "disposable-custody", snapshot["registry_id"])
    conn = db.connect(cfg.db_path)
    admitted = [dict(row) for row in conn.execute("SELECT id, content_sha256 FROM engram ORDER BY id")]
    if admitted != snapshot["admitted"]:
        raise ValueError("worker admission/eligibility content differs")
    frozen_policy = {
        "routing_policy": cfg.routing_policy,
        "family": routing_policy.policy_family(policy),
        "policy_digest": routing_policy.compute_policy_digest(policy, cfg),
        "config_digest": routing_policy.compute_config_digest(cfg),
    }
    write(output / "policy-freeze.json", frozen_policy)
    build_seconds = snapshot["build_seconds"]
    adapter = ActualRouter(cfg, conn, embedder)
    warmups = 0 if kind == "quality" else 50
    operations = len(queries) if kind == "quality" else 1000
    try:
        for index in range(warmups):
            adapter.predict(queries[index % len(queries)])
        rows = []
        for index in range(operations):
            query = queries[index % len(queries)]
            started = time.perf_counter()
            prediction = adapter.predict(query)
            if (
                prediction["policy_digest"] != frozen_policy["policy_digest"]
                or prediction["config_digest"] != frozen_policy["config_digest"]
            ):
                raise ValueError("resolved policy/config differs from pre-query freeze")
            serialized = json.dumps(prediction, sort_keys=True, allow_nan=False)
            elapsed = (time.perf_counter() - started) * 1000
            rows.append(
                {
                    "operation": index,
                    "query_id": query["query_id"],
                    "elapsed_ms": elapsed,
                    "prediction": json.loads(serialized),
                }
            )
        if network_attempts:
            raise ValueError("offline execution attempted a connection")
        verify_model(Path(inputs["model_cache"]), expected_model)
        evidence = {
            "schema": "magicite/empirical-worker/1",
            "source_commit": inputs["source_commit"],
            "expected_model_manifest_sha256": inputs["model_manifest"]["sha256"],
            "runtime_queries_sha256": inputs["runtime_queries"]["sha256"],
            "corpus_inventory_sha256": inputs["inventory"]["sha256"],
            "kind": kind,
            "policy_id": policy,
            "repetition": repetition,
            "pid": os.getpid(),
            "python": sys.version,
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "physical_memory_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"),
            "libraries": {
                name: importlib.metadata.version(name)
                for name in ("fastembed", "onnxruntime", "numpy", "mcp")
            },
            "provider": type(embedder).__name__,
            "model_name": embedder.model_name,
            "model_dimensions": embedder.dim,
            "upstream_authenticity": "UNEVALUATED",
            "deployment_custody": "disposable-simulated; UNEVALUATED",
            "policy_config": frozen_policy,
            "admitted_snapshot_manifest_sha256": inputs["admitted_snapshot_manifest"]["sha256"],
            "initial_snapshot_byte_equality_verified": True,
            "admitted_registry_id": snapshot["registry_id"],
            "admitted_content": admitted,
            "warmups": warmups,
            "operations": operations,
            "distinct_queries": len(queries),
            "repeated_operations": operations - len(queries),
            "per_operation": rows,
            "latency": latency_percentiles_ms([row["elapsed_ms"] / 1000 for row in rows]),
            "process_peak_rss_gib_including_startup": process_rss_gib(),
            "full_data_directory_gib_including_trust_and_logs": path_size_gib(cfg.data_dir),
            "index_build_seconds": build_seconds,
            "admitted_ids": sorted(row["id"] for row in inventory),
            "production_embed_calls": embedder.embed_calls,
            "production_batch_calls": embedder.batch_calls,
            "network_attempts": len(network_attempts),
            "qualification": "UNEVALUATED",
        }
        write(output / "result.json", evidence)
        return evidence
    finally:
        conn.close()


def run(output: Path, oracle_freeze: Path, model_manifest: Path, model_cache: Path) -> dict:
    if output.resolve().is_relative_to(ROOT) or output.exists():
        raise ValueError("fresh output outside checkout required")
    if sha256(oracle_freeze) != ORACLE_FREEZE_SHA:
        raise ValueError("independent pre-output oracle freeze mismatch")
    freeze = json.loads(oracle_freeze.read_text())
    descriptors = {}
    for row in freeze["artifacts"]:
        original = Path(row["path"])
        transported = oracle_freeze.parent / original.name
        chosen = original if original.exists() else transported
        bound = {**row, "path": str(chosen.resolve())}
        read_bound(bound)
        descriptors[original.name] = bound
    expected = json.loads(model_manifest.read_text())
    verify_model(model_cache, expected)
    output.mkdir()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source = source_inputs()
    assert_candidate(commit, source)

    def binding(path):
        return {"path": str(path.resolve()), "sha256": sha256(path)}

    inputs = {
        "source_commit": commit,
        "source_inputs": source,
        "model_manifest": binding(model_manifest),
        "model_cache": str(model_cache.resolve()),
        "inventory": descriptors["independent-corpus-inventory.json"],
        "runtime_queries": descriptors["independent-runtime-queries.json"],
    }
    write(output / "worker-inputs.json", inputs)
    write(
        output / "run-freeze.json",
        {
            **inputs,
            "oracle_freeze": binding(oracle_freeze),
            "scoring_oracle": descriptors["independent-oracle.json"],
            "policies": list(POLICIES),
            "warmups": 50,
            "operations": 1000,
            "process_repetitions": 3,
            "frozen_before_worker_execution": True,
        },
    )
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("MAGICITE_") or key in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "PYTHONHOME"}:
            env.pop(key)
    (output / "home").mkdir()
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        PYTHONNOUSERSITE="1",
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HOME=str(output / "home/hf"),
        HOME=str(output / "home"),
    )
    results, commands = {}, []
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--manifest",
        str(output / "worker-inputs.json"),
        "--policy",
        POLICY_DENSE_V1,
        "--kind",
        "bootstrap",
        "--output",
        str(output / "admitted-snapshot"),
    ]
    with (output / "bootstrap.log").open("w") as log:
        bootstrap = subprocess.run(
            command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False
        )
    commands.append({"argv": command, "exit": bootstrap.returncode, "log": "bootstrap.log"})
    if bootstrap.returncode:
        raise ValueError("shared admission bootstrap failed; inspect bootstrap.log")
    inputs["admitted_snapshot_manifest"] = binding(output / "admitted-snapshot.json")
    inputs["admitted_snapshot"] = str(output / "admitted-snapshot")
    write(output / "worker-inputs.json", inputs)
    write(
        output / "query-freeze.json",
        {
            **inputs,
            "policies": list(POLICIES),
            "warmups": 50,
            "operations": 1000,
            "process_repetitions": 3,
            "frozen_before_first_query": True,
        },
    )
    for policy in POLICIES:
        slug = policy.replace("/", "-")
        for kind, repetitions in (("quality", 1), ("timing", 3)):
            for repetition in range(repetitions):
                name = f"{slug}-{kind}-{repetition}"
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--manifest",
                    str(output / "worker-inputs.json"),
                    "--policy",
                    policy,
                    "--kind",
                    kind,
                    "--repetition",
                    str(repetition),
                    "--output",
                    str(output / name),
                ]
                with (output / (name + ".log")).open("w") as log:
                    result = subprocess.run(
                        command,
                        env=env,
                        cwd=ROOT,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=1800,
                        check=False,
                    )
                commands.append({"argv": command, "exit": result.returncode, "log": name + ".log"})
                if result.returncode:
                    raise ValueError("empirical worker failed: " + name)
                results[name] = json.loads((output / name / "result.json").read_text())
    labels = read_bound(descriptors["independent-oracle.json"])
    quality = {
        policy: descriptive_quality(
            [row["prediction"] for row in results[policy.replace("/", "-") + "-quality-0"]["per_operation"]],
            labels,
        )
        for policy in POLICIES
    }
    assert_candidate(commit, source)
    report = {
        "schema": "magicite/production-empirical/1",
        "status": "EXECUTED",
        "source_commit": commit,
        "run_freeze_sha256": sha256(output / "run-freeze.json"),
        "commands": commands,
        "quality": quality,
        "workers": {
            name: {"path": name + "/result.json", "sha256": sha256(output / name / "result.json")}
            for name in results
        },
        "expected_model_manifest_sha256": inputs["model_manifest"]["sha256"],
        "unresolved": {
            name: "UNEVALUATED"
            for name in [
                "official-SkillRet-E3",
                "temporal-holdout",
                "hybrid-RRF",
                "reference-Linux-E6-100-1000-10k",
                "deployment-custody",
                "F16-publisher-authenticity",
                "F17-scanner-policy",
                "external-host-outcomes",
                "published-channels",
                "human-security-approval",
                "GA",
            ]
        },
    }
    write(output / "report.json", report)
    files = [
        output / "report.json",
        output / "run-freeze.json",
        output / "worker-inputs.json",
        output / "query-freeze.json",
        output / "admitted-snapshot.json",
    ]
    files += [output / name / "policy-freeze.json" for name in results]
    files += [output / row["log"] for row in commands] + [
        output / row["path"] for row in report["workers"].values()
    ]
    write(output / "artifacts.json", {str(path.relative_to(output)): sha256(path) for path in files})
    return report


def verify(output: Path) -> dict:
    report = json.loads((output / "report.json").read_text())
    inputs = json.loads((output / "worker-inputs.json").read_text())
    assert_candidate(report["source_commit"], inputs["source_inputs"])
    manifest = json.loads((output / "artifacts.json").read_text())
    expected_names = {
        "report.json",
        "run-freeze.json",
        "worker-inputs.json",
        "query-freeze.json",
        "admitted-snapshot.json",
        "bootstrap.log",
    }
    expected_rows = {
        f"{policy.replace('/', '-')}-{kind}-{rep}": (policy, kind, rep)
        for policy in POLICIES
        for kind, count in (("quality", 1), ("timing", 3))
        for rep in range(count)
    }
    names = list(expected_rows)
    expected_names |= {name + ".log" for name in names}
    expected_names |= {name + "/result.json" for name in names}
    expected_names |= {name + "/policy-freeze.json" for name in names}
    if set(manifest) != expected_names or set(report["workers"]) != set(names):
        raise ValueError("mandatory empirical evidence coverage mismatch")
    for name, digest in manifest.items():
        if sha256(output / name) != digest:
            raise ValueError("empirical evidence digest mismatch")
    model = read_bound(inputs["model_manifest"])
    verify_model(Path(inputs["model_cache"]), model)
    queries = runtime_queries(read_bound(inputs["runtime_queries"]))
    inventory = read_bound(inputs["inventory"])["inventory"]
    verify_inventory(ROOT, inventory)
    frozen = json.loads((output / "run-freeze.json").read_text())
    if frozen["oracle_freeze"]["sha256"] != ORACLE_FREEZE_SHA:
        raise ValueError("independent pre-output oracle identity changed")
    original_freeze = read_bound(frozen["oracle_freeze"])
    frozen_refs = {Path(ref["path"]).name: ref["sha256"] for ref in original_freeze["artifacts"]}
    for name, ref in (
        ("independent-runtime-queries.json", inputs["runtime_queries"]),
        ("independent-corpus-inventory.json", inputs["inventory"]),
        ("independent-oracle.json", frozen["scoring_oracle"]),
    ):
        if ref["sha256"] != frozen_refs[name]:
            raise ValueError("frozen corpus/runtime/scoring input changed")
    if report["expected_model_manifest_sha256"] != inputs["model_manifest"]["sha256"]:
        raise ValueError("report expected-model identity changed")
    labels = read_bound(frozen["scoring_oracle"])
    expected_snapshot = read_bound(inputs["admitted_snapshot_manifest"])
    if report["run_freeze_sha256"] != sha256(output / "run-freeze.json") or len(report["commands"]) != 9:
        raise ValueError("run freeze/command coverage mismatch")
    pids = set()
    for name in names:
        row = read_bound(
            {
                "path": str(output / report["workers"][name]["path"]),
                "sha256": report["workers"][name]["sha256"],
            }
        )
        expected_policy, expected_kind, expected_rep = expected_rows[name]
        if (row["policy_id"], row["kind"], row["repetition"]) != (
            expected_policy,
            expected_kind,
            expected_rep,
        ):
            raise ValueError("worker policy/kind/repetition relabeled")
        count = 16 if expected_kind == "quality" else 1000
        if (
            row["source_commit"] != report["source_commit"]
            or row["policy_id"] not in POLICIES
            or row["operations"] != count
            or len(row["per_operation"]) != count
            or row["warmups"] != (0 if row["kind"] == "quality" else 50)
            or row["admitted_content"] != expected_snapshot["admitted"]
            or row["admitted_registry_id"] != expected_snapshot["registry_id"]
            or row["expected_model_manifest_sha256"] != inputs["model_manifest"]["sha256"]
            or row["network_attempts"] != 0
            or row["distinct_queries"] != 16
        ):
            raise ValueError("actual worker evidence inconsistent")
        if row["pid"] in pids:
            raise ValueError("fresh-process identity repeated")
        pids.add(row["pid"])
        if row["policy_config"] != json.loads((output / name / "policy-freeze.json").read_text()):
            raise ValueError("pre-query policy identity mismatch")
        for index, operation in enumerate(row["per_operation"]):
            prediction = operation["prediction"]
            if (
                operation["operation"] != index
                or operation["query_id"] != queries[index % 16]["query_id"]
                or prediction["policy_id"] != row["policy_id"]
                or prediction["policy_digest"] != row["policy_config"]["policy_digest"]
                or prediction["config_digest"] != row["policy_config"]["config_digest"]
                or set(prediction["candidate_ids"]) - set(row["admitted_ids"])
                or len(prediction["candidate_ids"]) != len(prediction["scores"])
                or isinstance(operation["elapsed_ms"], bool)
                or not math.isfinite(operation["elapsed_ms"])
                or operation["elapsed_ms"] < 0
            ):
                raise ValueError("per-operation query/policy/result mismatch")
        if row["latency"] != latency_percentiles_ms([op["elapsed_ms"] / 1000 for op in row["per_operation"]]):
            raise ValueError("latency summary mismatch")
    for policy in POLICIES:
        row = json.loads((output / (policy.replace("/", "-") + "-quality-0/result.json")).read_text())
        if report["quality"][policy] != descriptive_quality(
            [op["prediction"] for op in row["per_operation"]], labels
        ):
            raise ValueError("descriptive quality mismatch")
    if any(row["exit"] != 0 for row in report["commands"]) or any(
        value != "UNEVALUATED" for value in report["unresolved"].values()
    ):
        raise ValueError("command outcome or limitation mismatch")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oracle-freeze", type=Path)
    parser.add_argument("--model-manifest", type=Path)
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--policy", choices=POLICIES)
    parser.add_argument("--kind", choices=("bootstrap", "quality", "timing"))
    parser.add_argument("--repetition", type=int, default=0)
    args = parser.parse_args()
    if args.verify:
        report = verify(args.output)
        print(json.dumps({"status": report["status"], "source_commit": report["source_commit"]}))
    elif args.worker:
        worker(args.manifest, args.policy, args.kind, args.repetition, args.output)
    else:
        report = run(args.output, args.oracle_freeze, args.model_manifest, args.model_cache)
        print(json.dumps({"status": report["status"], "source_commit": report["source_commit"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
