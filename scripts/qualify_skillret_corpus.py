#!/usr/bin/env python3
"""Acquire locked public SkillRet objects and replay a complete inert codec offline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from magicite.engram import parser  # noqa: E402
from magicite.eval import skillret  # noqa: E402
from magicite.eval.digests import sha256_json  # noqa: E402

LOCK = ROOT / "docs/evaluation/v1/skillret-v1.1-source.json"
REVISION = "6583d7d2ed07644d0fb8938ed8178f3a7dc42a12"


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def source_inputs() -> dict[str, str]:
    paths = git("ls-files", "src", "scripts", "pyproject.toml", "uv.lock", str(LOCK.relative_to(ROOT)))
    return {name: skillret.digest(ROOT / name) for name in paths.splitlines() if (ROOT / name).is_file()}


def candidate_origins() -> dict[str, str]:
    origins = {}
    for name, module in tuple(sys.modules.items()):
        if name == "magicite" or name.startswith("magicite."):
            path = getattr(module, "__file__", None)
            if path is None:
                spec = getattr(module, "__spec__", None)
                locations = list(getattr(spec, "submodule_search_locations", None) or [])
                if (
                    getattr(spec, "origin", None) is not None
                    or len(locations) != 1
                    or list(getattr(module, "__path__", [])) != locations
                ):
                    raise ValueError("invalid fileless candidate module: " + name)
                directory = Path(locations[0]).resolve()
                if not directory.is_dir() or not directory.is_relative_to((ROOT / "src").resolve()):
                    raise ValueError("namespace outside candidate source: " + name)
                origins[name] = str(directory.relative_to(ROOT)) + "/"
                continue
            if not Path(path).resolve().is_relative_to((ROOT / "src").resolve()):
                raise ValueError("module imported outside candidate source: " + name)
            origins[name] = str(Path(path).resolve().relative_to(ROOT))
    return origins


def pinned_lock() -> dict:
    lock = json.loads(LOCK.read_bytes())
    required = {".gitattributes", "README.md", "data/taxonomy.json", "data/skills.jsonl"}
    required |= {
        f"data/{kind}/{split}.jsonl" for kind in ("skills", "queries", "qrels") for split in ("train", "test")
    }
    if (
        lock["revision"] != REVISION
        or {row["path"] for row in lock["artifacts"]} != required
        or len(lock["artifacts"]) != 10
    ):
        raise ValueError("incomplete or unpinned source lock")
    return lock


def acquire(raw: Path, lock: dict) -> None:
    """No netrc, ambient proxy, HuggingFace token, remote code or downstream URL fetch."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for row in lock["artifacts"]:
        expected_url = f"https://huggingface.co/datasets/ThakiCloud/SKILLRET/resolve/{REVISION}/{row['path']}"
        if row["url"] != expected_url:
            raise ValueError("source URL not pinned")
        path = skillret.contained(raw, row["path"])
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        with opener.open(expected_url, timeout=120) as response, partial.open("xb") as stream:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > row["size_bytes"]:
                    raise ValueError("oversized source object")
                stream.write(block)
        partial.rename(path)
    skillret.verify_raw(raw, lock)


def offline_audit(event: str, _args: tuple) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
        raise RuntimeError("offline inert conversion forbids network or child execution")


def verify_seal(root: Path) -> dict:
    seal = json.loads((root / "seal.json").read_bytes())
    if seal["status"] != "READY_FOR_EVALUATION_INPUT" or seal["revision"] != REVISION:
        raise ValueError("converted seal scope mismatch")
    files = seal["files"]
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != set(files) | {"seal.json"} or seal["aggregate_sha256"] != sha256_json(files):
        raise ValueError("converted inventory coverage mismatch")
    for name, row in files.items():
        path = skillret.contained(root, name)
        if path.stat().st_size != row["bytes"] or skillret.digest(path) != row["sha256"]:
            raise ValueError("converted content mismatch")
    for split in ("master", "train", "test"):
        inventory = json.loads((root / split / "inventory.json").read_bytes())
        expected_skills = {"master": 17810, "train": 10123, "test": 6006}
        if len(inventory) != expected_skills[split]:
            raise ValueError("official candidate pool incomplete")
        seen = set()
        for row in inventory:
            if row["id"] in seen:
                raise ValueError("duplicate inventory ID")
            seen.add(row["id"])
            path = skillret.contained(root, row["path"])
            artifact, _ = parser.load_artifact_file(path, registry_root=root)
            if (artifact.id, artifact.name) != (row["id"], row["name"]):
                raise ValueError("native identity mismatch")
            body = skillret.contained(root, row["source_body"])
            if skillret.digest(body) != row["source_body_sha256"]:
                raise ValueError("source-body mismatch")
        if split != "master":
            queries = json.loads((root / split / "runtime-queries.json").read_bytes())
            if len(queries) != {"train": 63259, "test": 4392}[split]:
                raise ValueError("official query pool incomplete")
            if not queries or any(
                set(row) != {"query_id", "query_text", "compatibility_context"}
                or row["compatibility_context"] != {}
                for row in queries
            ):
                raise ValueError("runtime query projection contains annotations")
    return seal


def verify(evidence: Path) -> dict:
    report = json.loads((evidence / "report.json").read_bytes())
    if (
        git("status", "--porcelain")
        or report["source_commit"] != git("rev-parse", "HEAD")
        or report["source_inputs"] != source_inputs()
    ):
        raise ValueError("candidate binding mismatch")
    if report["lock_sha256"] != skillret.digest(LOCK):
        raise ValueError("lock binding mismatch")
    expected_logs = {f"replay-{number}-{stream}.txt" for number in (1, 2) for stream in ("stdout", "stderr")}
    if (
        set(report["evidence_files"]) != expected_logs
        or len(report["commands"]) != 2
        or any(row["exit_code"] != 0 or row["offline"] is not True for row in report["commands"])
    ):
        raise ValueError("replay execution coverage mismatch")
    if report["raw_before"] != skillret.verify_raw(Path(report["raw_directory"]), pinned_lock()):
        raise ValueError("raw evidence binding mismatch")
    for name, row in report["evidence_files"].items():
        path = skillret.contained(evidence, name)
        if skillret.digest(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("replay evidence mismatch")
    for number in (1, 2):
        worker = json.loads((evidence / f"replay-{number}-stdout.txt").read_bytes())
        if worker["source_commit"] != report["source_commit"] or not worker["candidate_module_origins"]:
            raise ValueError("worker source binding mismatch")
        for name in worker["candidate_module_origins"].values():
            bound = name in report["source_inputs"] or (
                name.endswith("/") and any(path.startswith(name) for path in report["source_inputs"])
            )
            if not name.startswith("src/magicite/") or not bound:
                raise ValueError("worker origin outside candidate")
    first, second = (verify_seal(evidence / name) for name in ("replay-1", "replay-2"))
    if first != second or first["aggregate_sha256"] != report["aggregate_sha256"]:
        raise ValueError("offline replay differs")
    if report["raw_before"] != report["raw_after"]:
        raise ValueError("raw input changed")
    return report


def run(raw: Path, evidence: Path) -> dict:
    if git("status", "--porcelain"):
        raise ValueError("clean frozen source required")
    if evidence.exists() or evidence.resolve().is_relative_to(ROOT):
        raise ValueError("fresh external evidence directory required")
    candidate = git("rev-parse", "HEAD")
    inputs = source_inputs()
    origins = candidate_origins()
    lock = pinned_lock()
    needed = lock["raw_primary_total_bytes"] * 6 + 1024**3
    parent = evidence.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < needed:
        raise ValueError("insufficient space for two full conversion trees")
    before = skillret.verify_raw(raw, lock)
    evidence.mkdir(parents=True)
    logs = {}
    commands = []
    for repetition in (1, 2):
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--raw",
            str(raw.resolve()),
            "--output",
            str(evidence / f"replay-{repetition}"),
        ]
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
        }
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        commands.append({"argv": command, "exit_code": result.returncode, "offline": True})
        for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            path = evidence / f"replay-{repetition}-{stream}.txt"
            path.write_text(text)
            logs[path.name] = {"sha256": skillret.digest(path), "bytes": path.stat().st_size}
        if result.returncode:
            raise RuntimeError("offline conversion failed; retained replay logs")
    first, second = (verify_seal(evidence / f"replay-{number}") for number in (1, 2))
    after = skillret.verify_raw(raw, lock)
    if git("status", "--porcelain") or candidate != git("rev-parse", "HEAD") or inputs != source_inputs():
        raise ValueError("candidate changed during replay")
    if first != second or before != after:
        raise ValueError("offline reproducibility failure")
    report = {
        "schema": "magicite/skillret-qualification/1",
        "status": "PASS",
        "source_commit": candidate,
        "source_inputs": inputs,
        "candidate_module_origins": origins,
        "disk_preflight_required_bytes": needed,
        "lock_sha256": skillret.digest(LOCK),
        "revision": REVISION,
        "raw_directory": str(raw.resolve()),
        "raw_before": before,
        "raw_after": after,
        "aggregate_sha256": first["aggregate_sha256"],
        "commands": commands,
        "evidence_files": logs,
        "python": sys.version,
        "platform": sys.platform,
        "limitations": [
            "Codec freeze only, not runtime admission or E3/E6",
            "Original repository and license declarations unverified",
            "No models, embeddings, ranking or linked assets fetched",
            "No publication or human release acceptance",
        ],
    }
    (evidence / "report.json").write_bytes(skillret.canonical(report))
    verify(evidence)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--acquire", action="store_true")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    candidate = git("rev-parse", "HEAD")
    if args.verify:
        report = verify(args.output)
    elif args.worker:
        candidate_origins()
        sys.addaudithook(offline_audit)
        report = skillret.convert(args.raw, args.output, pinned_lock())
    else:
        if args.raw is None:
            ap.error("--raw required")
        if args.acquire:
            acquire(args.raw, pinned_lock())
        report = run(args.raw, args.output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "aggregate_sha256": report["aggregate_sha256"],
                "source_commit": candidate,
                "candidate_module_origins": candidate_origins(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
