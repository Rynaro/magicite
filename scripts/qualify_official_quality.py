#!/usr/bin/env python3
"""Fixed-policy official positive-only observation; no fit, promotion or skill execution."""

from __future__ import annotations

# ruff: noqa: E402 -- pin candidate before any Magicite/helper import.
import argparse
import ast
import fcntl
import importlib.metadata
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import run_benchmark_matrix as benchmark
from qualify_production_empirical import copy_snapshot, snapshot_files

from magicite.config import Config
from magicite.core import registry, router, routing_policy, trust, writer_guard
from magicite.engram import lint
from magicite.engram import parser as engram_parser
from magicite.eval import quality
from magicite.eval.digests import sha256_json
from magicite.eval.production import (
    ActualRouter,
    ProductionEmbedder,
    runtime_queries,
    sha256,
    verify_inventory,
    verify_model,
)
from magicite.eval.skillret import canonical, contained
from magicite.storage import db

POLICIES = ("dense-v1", "experimental/adaptive-blend-v1")
COUNTS = {"train": 10123, "test": 6006}
EXECUTION_LOCK_FD: int | None = None
ACCEPTED_SEAL = "17f0914f1f6f0fe8bc44db312d97ab31d52345a7aa7caba064d9d66c1659da1a"


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def bound(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def read_bound(row: dict):
    path = Path(row["path"])
    if sha256(path) != row["sha256"]:
        raise ValueError("frozen input changed: " + path.name)
    return json.loads(path.read_bytes())


def source_inputs() -> dict:
    names = subprocess.check_output(
        [
            "git",
            "ls-files",
            "src",
            "scripts",
            "pyproject.toml",
            "uv.lock",
            "docs/generated/runtime-reference.json",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    return {name: sha256(ROOT / name) for name in names}


def assert_source(contract: dict) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if dirty or head != contract["source_commit"] or source_inputs() != contract["source_inputs"]:
        raise ValueError("clean frozen candidate binding changed")


def configuration(project: Path, policy: str) -> Config:
    cfg = Config.load(
        project,
        env={
            "MAGICITE_EMBEDDING_PROVIDER": "fastembed",
            "MAGICITE_EMBEDDING_OFFLINE": "1",
            "MAGICITE_ROUTING_POLICY": policy,
        },
    )
    if (
        not cfg.abstention_enabled
        or cfg.abstention_score_threshold != 0
        or cfg.abstention_margin_threshold != 0
    ):
        raise ValueError("frozen uncalibrated thresholds changed")
    return cfg


def policy_identity(cfg: Config) -> dict:
    return {
        "policy_id": cfg.routing_policy,
        "policy_digest": routing_policy.compute_policy_digest(cfg.routing_policy, cfg),
        "config_digest": routing_policy.compute_config_digest(cfg),
        "abstention_enabled": True,
        "score_threshold": 0.0,
        "margin_threshold": 0.0,
        "rank_depth": 10,
        "calibration": "NONE",
    }


def verify_corpus(corpus: Path) -> dict:
    seal = json.loads((corpus / "seal.json").read_bytes())
    if seal["aggregate_sha256"] != ACCEPTED_SEAL or sha256_json(seal["files"]) != ACCEPTED_SEAL:
        raise ValueError("unaccepted corpus seal")
    actual = {str(path.relative_to(corpus)) for path in corpus.rglob("*") if path.is_file()}
    if actual != set(seal["files"]) | {"seal.json"}:
        raise ValueError("corpus coverage changed")
    for name, row in seal["files"].items():
        path = contained(corpus, name)
        if sha256(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("corpus content changed")
    return seal


def freeze(
    corpus: Path, model: Path, cache: Path, output: Path, *, reuse_receipt: Path | None = None
) -> dict:
    if output.exists() or output.resolve().is_relative_to(ROOT):
        raise ValueError("fresh external experiment directory required")
    verify_corpus(corpus)
    expected = json.loads(model.read_bytes())
    verify_model(cache, expected)
    output.mkdir(parents=True)
    bindings = {}
    for split in ("train", "test"):
        inventory = json.loads((corpus / split / "inventory.json").read_bytes())
        runtime = runtime_queries(json.loads((corpus / split / "runtime-queries.json").read_bytes()))
        if len(inventory) != COUNTS[split] or len({row["id"] for row in inventory}) != COUNTS[split]:
            raise ValueError("complete split pool required")
        if len(runtime) != {"train": 63259, "test": 4392}[split]:
            raise ValueError("complete official query projection required")
        bindings[split] = {
            key: bound(corpus / split / name)
            for key, name in [
                ("inventory", "inventory.json"),
                ("runtime", "runtime-queries.json"),
                ("scoring", "scoring.json"),
            ]
        }
    train = read_bound(bindings["train"]["runtime"])
    sample = quality.smoke_sample(train, read_bound(bindings["train"]["scoring"]))
    write(output / "train-sample.json", sample)
    sampled = {row["query_id"]: row for row in train}
    write(output / "runtime-smoke.json", [sampled[qid] for qid in sample["query_ids"]])
    write(
        output / "runtime-test.json",
        sorted(read_bound(bindings["test"]["runtime"]), key=lambda row: row["query_id"]),
    )
    contract: dict = {
        "schema": "magicite/official-quality-freeze/1",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_inputs": source_inputs(),
        "corpus": str(corpus.resolve()),
        "corpus_seal": bound(corpus / "seal.json"),
        "splits": bindings,
        "sample": bound(output / "train-sample.json"),
        "runtime": {
            "smoke": bound(output / "runtime-smoke.json"),
            "test": bound(output / "runtime-test.json"),
        },
        "model_manifest": bound(model),
        "model_cache": str(cache.resolve()),
        "libraries": {
            name: importlib.metadata.version(name)
            for name in ("fastembed", "onnxruntime", "numpy", "ruamel.yaml")
        },
        "policies": {
            policy: policy_identity(configuration(output / "prototype", policy)) for policy in POLICIES
        },
        "parameters": {
            "rank_depth": 10,
            "query_timeout_seconds": 120,
            "phase_budget_seconds": 7200,
            "pool_build_budget_seconds": 7200,
            "progress_seconds": 30,
            "repeat_tolerance": 1e-6,
            "bootstrap_resamples": 10000,
            "bootstrap_seed": 0,
        },
        "metric_definitions": {
            "decision_hit1": (
                "actual first selection relevant; abstention/error/missing is miss; "
                "full requested denominator"
            ),
            "ndcg10": "linear relevance/log2 discount",
            "rank_scope": "actually retained rank slate",
            "interval_scope": "descriptive query-row bootstrap, independence unproven",
        },
        "custody": "Explicit simulated evaluation review, not deployment trust",
        "limitations": [
            "positive-only: no false-selection calibration/ECE",
            "original_id not proven independence",
            "not E3/E6/hybrid/RRF/GA",
            "original side assets unfetched; no skill execution",
            "model bytes self-observed not publisher-authenticated",
        ],
    }
    assert_source(contract)
    contract["subject_cache_capacity"] = router._SUBJECT_CACHE_MAX
    contract["pool_reuse"] = bound(reuse_receipt) if reuse_receipt is not None else None
    if reuse_receipt is not None:
        receipt = verify_reuse_receipt(read_bound(contract["pool_reuse"]), contract)
        producer = Path(receipt["producer_experiment"])
        for split in COUNTS:
            snapshot = json.loads(read_bound_bytes(receipt["snapshots"][split]))
            copy_snapshot(producer / (split + "-base"), output / (split + "-base"), snapshot)
            (output / (split + "-snapshot.json")).write_bytes(read_bound_bytes(receipt["snapshots"][split]))
    write(output / "run-freeze.json", contract)
    (output / "run-freeze.sha256").write_text(sha256(output / "run-freeze.json") + "\n")
    return contract


def frozen_contract(experiment: Path) -> dict:
    path = experiment / "run-freeze.json"
    if sha256(path) != (experiment / "run-freeze.sha256").read_text().strip():
        raise ValueError("prospective experiment freeze changed")
    return json.loads(path.read_bytes())


def read_bound_bytes(record: dict) -> bytes:
    read_bound(record)
    return Path(record["path"]).read_bytes()


def verify_reuse_receipt(receipt: dict, consumer: dict) -> dict:
    """Verify a reviewed C3 producer, not a fabricated C4 build claim."""
    producer = frozen_contract(Path(receipt["producer_experiment"]))
    if (
        receipt["schema"] != "magicite/official-quality-pool-reuse/1"
        or receipt["checker"] != "vigil"
        or receipt["verdict"] != "ACCEPTED"
        or receipt["producer_commit"] != producer["source_commit"]
        or receipt["consumer_commit"] != consumer["source_commit"]
    ):
        raise ValueError("reused pool producer/consumer receipt mismatch")
    read_bound(receipt["independent_review"])
    delta = subprocess.check_output(
        ["git", "diff", "--binary", producer["source_commit"], consumer["source_commit"]], cwd=ROOT
    )
    import hashlib

    if hashlib.sha256(delta).hexdigest() != receipt["complete_diff_sha256"]:
        raise ValueError("reviewed complete source diff changed")
    current = source_inputs()
    allowed = {"src/magicite/core/router.py", "scripts/qualify_official_quality.py"}
    if set(producer["source_inputs"]) != set(current) or any(
        current[name] != digest for name, digest in producer["source_inputs"].items() if name not in allowed
    ):
        raise ValueError("decision runtime changed outside cache/provenance scope")
    for name in allowed:
        old = ast.parse(
            subprocess.check_output(["git", "show", producer["source_commit"] + ":" + name], cwd=ROOT)
        )
        new = ast.parse((ROOT / name).read_bytes())
        if name.endswith("router.py"):
            for tree, capacity in [(old, 4096), (new, 16384)]:
                nodes = [
                    n
                    for n in tree.body
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_SUBJECT_CACHE_MAX" for t in n.targets)
                ]
                if (
                    len(nodes) != 1
                    or not isinstance(nodes[0].value, ast.Constant)
                    or nodes[0].value.value != capacity
                ):
                    raise ValueError("unexpected cache capacity delta")
                nodes[0].value = ast.Constant(value=0)
            if ast.dump(old, include_attributes=False) != ast.dump(new, include_attributes=False):
                raise ValueError("router decision logic changed beyond capacity")
        else:
            protected = {
                "configuration",
                "policy_identity",
                "verify_corpus",
                "admitted",
                "review_with_evidence",
                "build_pool",
                "authorization",
                "query_worker",
                "Channel",
                "launch",
                "run_arm",
                "validate_rows",
                "score_phase",
            }

            def functions(tree, protected=protected):
                return {
                    n.name: ast.dump(n, include_attributes=False)
                    for n in tree.body
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in protected
                }

            if functions(old) != functions(new):
                raise ValueError("pool/route/metric execution changed beyond provenance")
    for key in [
        "corpus",
        "corpus_seal",
        "model_cache",
        "model_manifest",
        "libraries",
        "splits",
        "policies",
        "parameters",
    ]:
        if producer.get(key) != consumer.get(key):
            raise ValueError("reused decision input changed: " + key)
    for key in ["sample", "runtime"]:
        old = producer[key]
        new = consumer[key]
        if key == "sample":
            if old["sha256"] != new["sha256"]:
                raise ValueError("train sample changed")
        elif {k: v["sha256"] for k, v in old.items()} != {k: v["sha256"] for k, v in new.items()}:
            raise ValueError("runtime query bytes changed")
    if set(receipt["snapshots"]) != set(COUNTS):
        raise ValueError("both full producer pools required")
    return receipt


def verify_pool_binding(experiment: Path, split: str, contract: dict, snapshot: dict) -> None:
    if contract.get("pool_reuse"):
        receipt = verify_reuse_receipt(read_bound(contract["pool_reuse"]), contract)
        original = read_bound(receipt["snapshots"][split])
        if (
            snapshot != original
            or sha256(experiment / (split + "-snapshot.json")) != receipt["snapshots"][split]["sha256"]
        ):
            raise ValueError("reused producer manifest changed")
        if snapshot["source_commit"] != receipt["producer_commit"] or snapshot["experiment_sha256"] != sha256(
            Path(receipt["producer_experiment"]) / "run-freeze.json"
        ):
            raise ValueError("false pool producer attribution")
    elif snapshot["source_commit"] != contract["source_commit"] or snapshot["experiment_sha256"] != sha256(
        experiment / "run-freeze.json"
    ):
        raise ValueError("admitted snapshot source/experiment mismatch")


def verify_inputs(experiment: Path) -> dict:
    contract = frozen_contract(experiment)
    assert_source(contract)
    if contract.get("subject_cache_capacity", router._SUBJECT_CACHE_MAX) != router._SUBJECT_CACHE_MAX:
        raise ValueError("frozen cache capacity changed")
    if contract.get("pool_reuse"):
        verify_reuse_receipt(read_bound(contract["pool_reuse"]), contract)
    if {name: importlib.metadata.version(name) for name in contract["libraries"]} != contract["libraries"]:
        raise ValueError("frozen dependency versions changed")
    verify_corpus(Path(contract["corpus"]))
    verify_model(Path(contract["model_cache"]), read_bound(contract["model_manifest"]))
    if contract["parameters"] != {
        "rank_depth": 10,
        "query_timeout_seconds": 120,
        "phase_budget_seconds": 7200,
        "pool_build_budget_seconds": 7200,
        "progress_seconds": 30,
        "repeat_tolerance": 1e-6,
        "bootstrap_resamples": 10000,
        "bootstrap_seed": 0,
    }:
        raise ValueError("frozen operational/metric settings changed")
    for phase in ("smoke", "test"):
        runtime_queries(read_bound(contract["runtime"][phase]))
    for split in ("train", "test"):
        for row in contract["splits"][split].values():
            if sha256(Path(row["path"])) != row["sha256"]:
                raise ValueError("split input binding changed")
    read_bound(contract["sample"])
    return contract


def pool_manifest(experiment: Path, split: str) -> dict:
    return json.loads((experiment / (split + "-snapshot.json")).read_bytes())


def admitted(conn, embedder) -> list[dict]:
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id,content_sha256,path,status,verification_status FROM engram ORDER BY id"
        )
    ]
    ids = {row["id"] for row in rows}
    if {row["id"] for row in router._fetch_candidates(conn, embedder.model_name)} != ids:
        raise ValueError("unembedded/unroutable reduced pool")
    if any(row["verification_status"] != "verified" for row in rows):
        raise ValueError("incomplete review admission")
    return rows


def review_with_evidence(cfg: Config, conn, work: Path) -> dict:
    before = []
    for row in conn.execute("SELECT id,path,content_sha256,verification_status FROM engram ORDER BY id"):
        source = cfg.project_root / row["path"]
        artifact, _ = engram_parser.parse_artifact_file(source, registry_root=cfg.project_root)
        artifact = registry._artifact_to_engram(artifact, intake_channel="local_register")
        profile: lint.LintProfile = "import" if registry._lint_profile_for(artifact) == "import" else "strict"
        before.append(
            {
                **dict(row),
                "lint_profile": profile,
                "lint_issues": [asdict(issue) for issue in lint.lint(artifact, profile=profile).issues],
                "injection_scan": asdict(lint.injection_scan(artifact)),
            }
        )
    pre_path = work / "pre-review.json"
    write(pre_path, before)  # Durable source-bound disclosure before any approval.
    try:
        benchmark._review_all(
            cfg,
            conn,
            reason=(
                "root-authorized isolated official corpus evaluation; "
                "not deployment trust or execution approval"
            ),
        )
    finally:
        decisions = {decision.engram_id: decision.to_dict() for decision in trust.list_decisions(cfg)}
        after = [
            {**dict(row), "decision": decisions.get(row["id"])}
            for row in conn.execute("SELECT id,content_sha256,verification_status FROM engram ORDER BY id")
        ]
        write(work / "review-outcomes.json", after)
    return {"before": bound(pre_path), "outcomes": bound(work / "review-outcomes.json")}


def build_pool(experiment: Path, split: str) -> dict:
    contract = verify_inputs(experiment)
    inventory = read_bound(contract["splits"][split]["inventory"])
    paths = verify_inventory(Path(contract["corpus"]), inventory)
    work = experiment / (split + "-base")
    if work.exists():
        raise ValueError("incomplete prior build retained; no silent restart")
    work.mkdir()
    embedder = ProductionEmbedder(Path(contract["model_cache"]))
    cfg = configuration(work / "project", "dense-v1")
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    registry_id = benchmark._enroll_disposable_custody(cfg, work / "disposable-custody")
    staging = cfg.project_root / "corpus-input"
    staging.mkdir()
    for source in paths:
        shutil.copyfile(source, staging / source.name)
    started = time.monotonic()
    try:
        result = registry.register(cfg, conn, embedder, path="corpus-input")
        if result.validation_errors or result.ingested != len(inventory):
            raise ValueError("full pool register failed: " + str(result.validation_errors[:3]))
        review_evidence = review_with_evidence(cfg, conn, work)
        rows = admitted(conn, embedder)
        if {row["id"] for row in rows} != {row["id"] for row in inventory}:
            raise ValueError("wrong/missing admitted split IDs")
        index_identity = router.pin_index_identity(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
        _, provider = writer_guard.resolve_custody(cfg)
        if not isinstance(provider, benchmark._DisposableCustody):
            raise ValueError("explicit evaluation custody provider mismatch")
        provider.store.close()
    verify_model(Path(contract["model_cache"]), read_bound(contract["model_manifest"]))
    manifest = {
        "source_commit": contract["source_commit"],
        "experiment_sha256": sha256(experiment / "run-freeze.json"),
        "input_inventory": contract["splits"][split]["inventory"],
        "registry_id": registry_id,
        "review_evidence": review_evidence,
        "admitted": rows,
        "files": snapshot_files(work),
        "build_seconds": time.monotonic() - started,
        "production_embed_calls": embedder.embed_calls,
        "production_batch_calls": embedder.batch_calls,
        "index_identity": index_identity,
        "index_note": "Null generation IDs are no published generation; snapshot hash binds actual state",
        "model_manifest_sha256": contract["model_manifest"]["sha256"],
    }
    write(experiment / (split + "-snapshot.json"), manifest)
    return manifest


def authorization(experiment: Path, path: Path) -> dict:
    value = json.loads(path.read_bytes())
    contract = json.loads((experiment / "run-freeze.json").read_bytes())
    if (
        value.get("source_commit") != contract["source_commit"]
        or value.get("experiment_sha256") != sha256(experiment / "run-freeze.json")
        or value.get("smoke_report_sha256") != sha256(experiment / "smoke-report.json")
        or value.get("test_snapshot_sha256") != sha256(experiment / "test-snapshot.json")
        or value.get("verdict") != "ACCEPTED"
        or value.get("checker") != "vigil"
    ):
        raise ValueError("independent full-test readiness authorization missing/mismatched")
    return value


def query_worker(experiment: Path, phase: str, policy: str, auth: Path | None) -> None:
    contract = frozen_contract(experiment)
    assert_source(contract)
    if {name: importlib.metadata.version(name) for name in contract["libraries"]} != contract["libraries"]:
        raise ValueError("worker dependency versions changed")
    if phase == "test":
        if auth is None:
            raise ValueError("full test requires independent readiness authorization")
        authorization(experiment, auth)
    verify_model(Path(contract["model_cache"]), read_bound(contract["model_manifest"]))
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    expected_queries = {
        row["query_id"]: row for row in runtime_queries(read_bound(contract["runtime"][phase]))
    }
    split = "train" if phase == "smoke" else "test"
    snapshot = pool_manifest(experiment, split)
    work = experiment / "workers" / (phase + "-" + policy.replace("/", "_") + "-" + uuid.uuid4().hex)
    snapshot_bytes = sum((experiment / (split + "-base") / name).stat().st_size for name in snapshot["files"])
    if shutil.disk_usage(experiment).free < snapshot_bytes + 1024**3:
        raise ValueError("insufficient space for a fresh verified worker clone")
    copy_snapshot(experiment / (split + "-base"), work, snapshot)
    cfg = configuration(work / "project", policy)
    if policy_identity(cfg) != contract["policies"][policy]:
        raise ValueError("resolved policy/config differs from freeze")
    benchmark._attach_custody(cfg.project_root, work / "disposable-custody", snapshot["registry_id"])
    conn = db.connect(cfg.db_path)
    embedder = ProductionEmbedder(Path(contract["model_cache"]))
    if admitted(conn, embedder) != snapshot["admitted"]:
        raise ValueError("worker admission differs from frozen full pool")
    adapter = ActualRouter(cfg, conn, embedder, rank_depth=10)
    print(
        json.dumps(
            {
                "ready": True,
                "policy": policy,
                "pool_count": len(snapshot["admitted"]),
                "snapshot_sha256": sha256(experiment / (split + "-snapshot.json")),
                "worker_initial_state": "fresh byte-verified admitted snapshot; new process/cache",
                "worker_directory": str(work),
            }
        ),
        flush=True,
    )
    try:
        for line in sys.stdin:
            query = json.loads(line)
            if query.get("query_id") not in expected_queries or query != expected_queries[query["query_id"]]:
                raise ValueError("query input not exact frozen runtime projection")
            started = time.monotonic()
            signal.setitimer(signal.ITIMER_REAL, 120)
            try:
                prediction = adapter.predict(query)
                if (
                    prediction["policy_digest"] != contract["policies"][policy]["policy_digest"]
                    or prediction["config_digest"] != contract["policies"][policy]["config_digest"]
                ):
                    raise ValueError("actual resolved config/policy changed")
                if not set(prediction["candidate_ids"]).issubset({row["id"] for row in snapshot["admitted"]}):
                    raise ValueError("foreign candidate outside admitted pool")
                quality.validate_prediction(prediction)
            except Exception as exc:
                prediction = {
                    "query_id": query["query_id"],
                    "status": "error",
                    "candidate_ids": [],
                    "scores": [],
                    "selected_ids": [],
                    "abstained": False,
                    "confidence": None,
                    "exclusions": [],
                    "error": {
                        "class": type(exc).__name__,
                        "code": "ACTUAL_ROUTE_ERROR",
                        "message": str(exc)[:300],
                    },
                }
            signal.setitimer(signal.ITIMER_REAL, 0)
            prediction.update(
                policy_id=policy,
                expected_model_manifest_sha256=contract["model_manifest"]["sha256"],
                pool_snapshot_sha256=sha256(experiment / (split + "-snapshot.json")),
                operational_seconds=time.monotonic() - started,
                worker_pid=os.getpid(),
                declared_pool_count=len(snapshot["admitted"]),
            )
            print(json.dumps(prediction, sort_keys=True, allow_nan=False), flush=True)
    finally:
        conn.close()
        _, provider = writer_guard.resolve_custody(cfg)
        if not isinstance(provider, benchmark._DisposableCustody):
            raise ValueError("explicit evaluation custody provider mismatch")
        provider.store.close()


class Channel:
    def __init__(self, process):
        self.process = process
        self.buffer = b""
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def receive(self, seconds: float, heartbeat) -> dict:
        deadline = time.monotonic() + seconds
        next_progress = time.monotonic() + 30
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("frozen worker response deadline")
            if self.selector.select(min(left, 1)):
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("worker exited before response")
                self.buffer += chunk
            if time.monotonic() >= next_progress:
                heartbeat()
                next_progress = time.monotonic() + 30
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def close(self):
        self.selector.close()


def child_env() -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}
    }
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        PYTHONNOUSERSITE="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_OFFLINE="1",
    )
    return env


def launch(experiment: Path, phase: str, policy: str, auth: Path | None, log):
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "query",
        "--experiment",
        str(experiment),
        "--phase",
        phase,
        "--policy",
        policy,
    ]
    if auth:
        command += ["--authorization", str(auth)]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        env=child_env(),
        pass_fds=(EXECUTION_LOCK_FD,) if EXECUTION_LOCK_FD is not None else (),
    )
    return process, Channel(process), command


def progress(experiment: Path, phase: str, policy: str, completed: int, spent: float) -> None:
    value = {
        "phase": phase,
        "policy": policy,
        "completed": completed,
        "query_budget_spent_seconds": spent,
        "at": time.time(),
    }
    write(experiment / "progress.json", value)
    print(json.dumps(value), flush=True)


def rows_from(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def run_arm(experiment: Path, phase: str, policy: str, auth: Path | None) -> dict:
    contract = json.loads((experiment / "run-freeze.json").read_bytes())
    queries = runtime_queries(read_bound(contract["runtime"][phase]))
    ids = [row["query_id"] for row in queries]
    split = "train" if phase == "smoke" else "test"
    snapshot = pool_manifest(experiment, split)
    pool_ids = {row["id"] for row in snapshot["admitted"]}
    stem = phase + "-" + policy.replace("/", "_")
    out = experiment / (stem + ".jsonl")
    checkpoint = experiment / (stem + "-checkpoint.json")
    rows = rows_from(out)
    quality.resume_rows(rows, ids)
    for row in rows:
        if not set(row["candidate_ids"]).issubset(pool_ids) or row["policy_id"] != policy:
            raise ValueError("foreign resumed candidate/policy")
    binding = {
        "experiment_sha256": sha256(experiment / "run-freeze.json"),
        "snapshot_sha256": sha256(experiment / (split + "-snapshot.json")),
        "runtime_sha256": contract["runtime"][phase]["sha256"],
        "policy_id": policy,
    }
    validate_rows(rows, binding, contract, snapshot, phase, policy)
    state: dict[str, Any] = {
        "binding": binding,
        "completed": 0,
        "spent_seconds": 0.0,
        "pending": None,
        "row_file_sha256": None,
        "interruption_estimates": [],
    }
    if checkpoint.exists():
        state = json.loads(checkpoint.read_bytes())
        interruptions = state["interruption_estimates"]
        state = quality.reconcile_checkpoint(
            state, out.read_bytes() if out.exists() else b"", binding, interruptions
        )
        write(checkpoint, state)
        if state["pending"]:
            allocated = state["pending"]["allocated_seconds"]
            state["spent_seconds"] += allocated
            state["interruption_estimates"].append(
                {
                    "uncompleted_attempt": state["pending"],
                    "conservative_budget_charge_seconds": allocated,
                    "elapsed_measurement": "UNKNOWN; allocated upper bound estimate",
                    "not_timeout_outcome": True,
                }
            )
            state["pending"] = None
            write(checkpoint, state)
    elif rows:
        raise ValueError("existing outcomes without checkpoint")
    process: Any = None
    channel: Any = None
    log: Any = None
    command_path = experiment / (stem + "-commands.jsonl")
    commands = rows_from(command_path)
    try:
        for index in range(len(rows), len(queries)):
            if 7200 - state["spent_seconds"] < 120:
                break
            if process is None:
                log = (experiment / (stem + "-worker.log")).open("ab")
                process, channel, command = launch(experiment, phase, policy, auth, log)
                command_record = {"argv": command, "worker_pid": process.pid, "at": time.time()}
                with command_path.open("ab") as stream:
                    stream.write(canonical(command_record))
                    stream.flush()
                    os.fsync(stream.fileno())
                commands.append(command_record)
                ready = channel.receive(
                    7200, lambda: progress(experiment, phase, policy, len(rows), state["spent_seconds"])
                )
                if (
                    ready.get("ready") is not True
                    or ready.get("pool_count") != COUNTS[split]
                    or ready.get("policy") != policy
                    or ready.get("snapshot_sha256") != binding["snapshot_sha256"]
                ):
                    raise ValueError("worker full-pool readiness mismatch")
            query = queries[index]
            state["pending"] = {
                "query_id": query["query_id"],
                "started_at": time.time(),
                "worker_pid": process.pid,
                "allocated_seconds": 120,
            }
            write(checkpoint, state)
            started = time.monotonic()
            process.stdin.write(canonical(query))
            process.stdin.flush()
            timeout = 120
            try:
                row = channel.receive(
                    timeout,
                    lambda started=started: progress(
                        experiment,
                        phase,
                        policy,
                        len(rows),
                        state["spent_seconds"] + time.monotonic() - started,
                    ),
                )
                if not set(row["candidate_ids"]).issubset(pool_ids):
                    raise ValueError("foreign worker candidate outside admitted pool")
                if row["query_id"] != query["query_id"]:
                    raise ValueError("worker response identity mismatch")
                quality.validate_prediction(row)
            except (TimeoutError, RuntimeError) as exc:
                alarm = process.poll() == -signal.SIGALRM
                row = {
                    "query_id": query["query_id"],
                    "status": "timeout" if isinstance(exc, TimeoutError) or alarm else "error",
                    "candidate_ids": [],
                    "scores": [],
                    "selected_ids": [],
                    "abstained": False,
                    "confidence": None,
                    "exclusions": [],
                    "error": {
                        "class": type(exc).__name__,
                        "code": "QUERY_DEADLINE" if isinstance(exc, TimeoutError) or alarm else "WORKER_EXIT",
                        "worker_exit": process.poll(),
                    },
                    "policy_id": policy,
                    "expected_model_manifest_sha256": contract["model_manifest"]["sha256"],
                    "pool_snapshot_sha256": binding["snapshot_sha256"],
                    "worker_pid": process.pid,
                    "declared_pool_count": COUNTS[split],
                }
                if process.poll() is None:
                    process.kill()
                process.wait()
                channel.close()
                log.close()
                process = channel = log = None
            elapsed = time.monotonic() - started
            row.update(ordinal=index, query_elapsed_seconds=elapsed, binding_sha256=sha256_json(binding))
            with out.open("ab") as stream:
                stream.write(canonical(row))
                stream.flush()
                os.fsync(stream.fileno())
            rows.append(row)
            state.update(
                completed=len(rows),
                spent_seconds=state["spent_seconds"] + elapsed,
                pending=None,
                row_file_sha256=sha256(out),
            )
            write(checkpoint, state)
            if len(rows) % 10 == 0:
                progress(experiment, phase, policy, len(rows), state["spent_seconds"])
    finally:
        if process is not None:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            channel.close()
            log.close()
    result = {
        "source_commit": contract["source_commit"],
        "policy": policy,
        "requested": len(queries),
        "completed": len(rows),
        "complete": len(rows) == len(queries),
        "checkpoint": bound(checkpoint),
        "outcomes": bound(out) if out.exists() else None,
        "commands": commands,
        "query_budget_spent_seconds": state["spent_seconds"],
        "stop_reason": "complete" if len(rows) == len(queries) else "phase_budget_reservation_exhausted",
        "budget_scope": (
            "Measured query time plus interrupted-attempt upper-bound estimates; no phase budget reset"
        ),
    }
    write(experiment / (stem + "-result.json"), result)
    return result


def prepare(experiment: Path) -> None:
    for split in ("train", "test"):
        manifest = experiment / (split + "-snapshot.json")
        if manifest.exists():
            if snapshot_files(experiment / (split + "-base")) != pool_manifest(experiment, split)["files"]:
                raise ValueError("frozen base snapshot changed")
            continue
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "build",
            "--experiment",
            str(experiment),
            "--pool",
            split,
        ]
        start = time.monotonic()
        with (experiment / (split + "-build.log")).open("ab") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                env=child_env(),
                pass_fds=(EXECUTION_LOCK_FD,) if EXECUTION_LOCK_FD is not None else (),
            )
            while process.poll() is None:
                if time.monotonic() - start >= 7200:
                    process.kill()
                    process.wait()
                    raise TimeoutError("pool build budget exceeded; incomplete pool retained")
                progress(experiment, "build", split, 0, time.monotonic() - start)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            write(
                experiment / (split + "-build-command.json"),
                {
                    "argv": command,
                    "exit_code": process.returncode,
                    "elapsed_seconds": time.monotonic() - start,
                },
            )
            if process.returncode:
                raise RuntimeError("full-pool build failed; retained log")


def validate_rows(
    rows: list[dict], binding: dict, contract: dict, snapshot: dict, phase: str, policy: str
) -> None:
    split = "train" if phase == "smoke" else "test"
    pool = {row["id"] for row in snapshot["admitted"]}
    for ordinal, row in enumerate(rows):
        if (
            row["ordinal"] != ordinal
            or row["binding_sha256"] != sha256_json(binding)
            or row["policy_id"] != policy
            or row["pool_snapshot_sha256"] != binding["snapshot_sha256"]
            or row["declared_pool_count"] != COUNTS[split]
            or row["expected_model_manifest_sha256"] != contract["model_manifest"]["sha256"]
        ):
            raise ValueError("retained outcome provenance mismatch")
        if not set(row["candidate_ids"]).issubset(pool):
            raise ValueError("foreign retained candidate outside full pool")
        if row["status"] not in {"error", "timeout"} and (
            row["policy_digest"] != contract["policies"][policy]["policy_digest"]
            or row["config_digest"] != contract["policies"][policy]["config_digest"]
        ):
            raise ValueError("retained actual policy/config mismatch")


def verify_phase(experiment: Path, phase: str) -> None:
    contract = frozen_contract(experiment)
    queries = runtime_queries(read_bound(contract["runtime"][phase]))
    ids = [row["query_id"] for row in queries]
    split = "train" if phase == "smoke" else "test"
    snapshot = pool_manifest(experiment, split)
    verify_pool_binding(experiment, split, contract, snapshot)
    declared = read_bound(contract["splits"][split]["inventory"])
    pool = {row["id"] for row in snapshot["admitted"]}
    if pool != {row["id"] for row in declared} or len(pool) != COUNTS[split]:
        raise ValueError("wrong/reduced observed pool")
    if snapshot_files(experiment / (split + "-base")) != snapshot["files"]:
        raise ValueError("immutable admitted pool snapshot changed")
    for policy in POLICIES:
        stem = phase + "-" + policy.replace("/", "_")
        path = experiment / (stem + ".jsonl")
        checkpoint = experiment / (stem + "-checkpoint.json")
        rows = rows_from(path)
        quality.resume_rows(rows, ids)
        binding = {
            "experiment_sha256": sha256(experiment / "run-freeze.json"),
            "snapshot_sha256": sha256(experiment / (split + "-snapshot.json")),
            "runtime_sha256": contract["runtime"][phase]["sha256"],
            "policy_id": policy,
        }
        state = json.loads(checkpoint.read_bytes())
        quality.reconcile_checkpoint(
            state, path.read_bytes() if path.exists() else b"", binding, state["interruption_estimates"]
        )
        validate_rows(rows, binding, contract, snapshot, phase, policy)
        result = json.loads((experiment / (stem + "-result.json")).read_bytes())
        if (
            result["source_commit"] != contract["source_commit"]
            or result["requested"] != len(queries)
            or result["completed"] != len(rows)
            or result["complete"] != (len(rows) == len(queries))
            or result["checkpoint"] != bound(checkpoint)
            or result["outcomes"] != (bound(path) if path.exists() else None)
        ):
            raise ValueError("retained arm result/coverage mismatch")


def score_phase(experiment: Path, phase: str, *, save: bool = True) -> dict:
    verify_phase(experiment, phase)
    contract = frozen_contract(experiment)
    split = "train" if phase == "smoke" else "test"
    labels = read_bound(contract["splits"][split]["scoring"])
    ids = {row["query_id"] for row in read_bound(contract["runtime"][phase])}
    labels = [row for row in labels if row["query_id"] in ids]
    scores = {
        policy: quality.metrics(
            rows_from(experiment / (phase + "-" + policy.replace("/", "_") + ".jsonl")),
            labels,
            allow_incomplete=True,
        )
        for policy in POLICIES
    }
    report = {
        "source_commit": contract["source_commit"],
        "experiment_sha256": sha256(experiment / "run-freeze.json"),
        "phase": phase,
        "complete": all(row["complete"] for row in scores.values()),
        "scores": scores,
        "scope": "Train plumbing only"
        if phase == "smoke"
        else "Official positive-only fixed-policy observation, not E3/E6",
        "safety_status": "FAIL"
        if any(row["safety_exclusion_violations"] for row in scores.values())
        else "NO_OBSERVED_VIOLATIONS; not whole safety qualification",
    }
    if phase == "test" and report["complete"]:
        report["paired_descriptive_query_rows"] = quality.paired_descriptive(
            scores[POLICIES[1]], scores[POLICIES[0]]
        )
    if save:
        write(experiment / (phase + "-report.json"), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--model-manifest", type=Path)
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument(
        "--reuse-pools",
        type=Path,
        help="Reviewed producer/consumer receipt; preserve original pool provenance",
    )
    parser.add_argument("--phase", choices=("smoke", "test"))
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--worker", choices=("build", "query"), help=argparse.SUPPRESS)
    parser.add_argument("--pool", choices=("train", "test"))
    parser.add_argument("--policy", choices=POLICIES)
    args = parser.parse_args()
    if args.freeze:
        freeze(
            args.corpus,
            args.model_manifest,
            args.model_cache,
            args.experiment,
            reuse_receipt=args.reuse_pools,
        )
        return 0
    global EXECUTION_LOCK_FD
    if not args.worker and not args.verify:
        lock_stream = (args.experiment / ".execution.lock").open("a+b")
        try:
            fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("same experiment already executing; do not start concurrent reruns") from exc
        EXECUTION_LOCK_FD = lock_stream.fileno()
    if args.worker:

        def offline(event, _args):
            if event == "socket.connect":
                raise RuntimeError("offline observation forbids network")

        sys.addaudithook(offline)
        if args.worker == "build":
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(signal.ITIMER_REAL, 7200)
            build_pool(args.experiment, args.pool)
        else:
            query_worker(args.experiment, args.phase, args.policy, args.authorization)
        return 0
    verify_inputs(args.experiment)
    if args.prepare:
        prepare(args.experiment)
    if args.phase:
        if args.phase == "test":
            if args.authorization is None:
                raise ValueError("independent readiness required before heldout queries")
            authorization(args.experiment, args.authorization)
        if args.phase == "test" and not (args.experiment / "test-exposure.json").exists():
            exposure = {
                "source_commit": frozen_contract(args.experiment)["source_commit"],
                "experiment_sha256": sha256(args.experiment / "run-freeze.json"),
                "at": time.time(),
                "scope": (
                    "Official test authorized; any later changed-source run is not a newly untouched holdout"
                ),
            }
            write(args.experiment / "test-exposure.json", exposure)
            with (args.experiment.parent / "exposure-history.jsonl").open("ab") as stream:
                stream.write(canonical(exposure))
                stream.flush()
                os.fsync(stream.fileno())
        for policy in POLICIES:
            run_arm(args.experiment, args.phase, policy, args.authorization)
        report = score_phase(args.experiment, args.phase)
        print(
            json.dumps({"complete": report["complete"], "source_commit": report["source_commit"]}), flush=True
        )
        return 0 if report["complete"] else 1
    if args.verify:
        for phase in ("smoke", "test"):
            if (args.experiment / (phase + "-report.json")).exists():
                original = json.loads((args.experiment / (phase + "-report.json")).read_bytes())
                recomputed = score_phase(args.experiment, phase, save=False)
                if original != recomputed:
                    raise ValueError("retained report does not match raw predictions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
