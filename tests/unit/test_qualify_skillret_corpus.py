"""Offline replay and exact pinned-lock boundaries."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("qualify_skillret", ROOT / "scripts/qualify_skillret_corpus.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_upstream_lock():
    lock = module.pinned_lock()
    assert len(lock["artifacts"]) == 10
    assert lock["raw_primary_total_bytes"] == 714420871


@pytest.mark.parametrize("event", ["socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"])
def test_offline_worker_refuses_network_and_execution(event):
    with pytest.raises(RuntimeError, match="offline"):
        module.offline_audit(event, ())


def test_inert_io_allowed():
    module.offline_audit("open", ())


def test_tampered_converted_file_rejected_before_domain_read(tmp_path):
    content = tmp_path / "body.txt"
    content.write_bytes(b"expected")
    files = {"body.txt": {"sha256": module.skillret.digest(content), "bytes": 8}}
    seal = {
        "status": "READY_FOR_EVALUATION_INPUT",
        "revision": module.REVISION,
        "files": files,
        "aggregate_sha256": module.sha256_json(files),
    }
    (tmp_path / "seal.json").write_bytes(module.skillret.canonical(seal))
    content.write_bytes(b"mutated!")
    with pytest.raises(ValueError, match="content mismatch"):
        module.verify_seal(tmp_path)


def test_missing_converted_file_rejected(tmp_path):
    seal = {
        "status": "READY_FOR_EVALUATION_INPUT",
        "revision": module.REVISION,
        "files": {"missing": {"bytes": 1, "sha256": "0" * 64}},
    }
    seal["aggregate_sha256"] = module.sha256_json(seal["files"])
    (tmp_path / "seal.json").write_bytes(module.skillret.canonical(seal))
    with pytest.raises(ValueError, match="coverage"):
        module.verify_seal(tmp_path)


def test_loaded_modules_originate_in_candidate():
    origins = module.candidate_origins()
    assert origins["magicite.eval.skillret"] == "src/magicite/eval/skillret.py"


def test_worker_metadata_emits_with_real_offline_guard(tmp_path):
    # Replace only conversion workload; exercise the real CLI/audit lifecycle.
    code = (
        "import importlib.util,sys; "
        + "s=importlib.util.spec_from_file_location('q',"
        + repr(str(ROOT / "scripts/qualify_skillret_corpus.py"))
        + "); "
        + "q=importlib.util.module_from_spec(s);s.loader.exec_module(q); "
        + "q.skillret.convert=lambda *a,**kw:{'status':'UNIT_FIXTURE','aggregate_sha256':'0'*64}; "
        + "sys.argv=['q','--worker','--raw',"
        + repr(str(tmp_path))
        + ",'--output',"
        + repr(str(tmp_path / "out"))
        + "]; "
        + "q.main()"
    )
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == "UNIT_FIXTURE"
    assert len(observed["source_commit"]) == 40
    assert observed["candidate_module_origins"]["magicite.eval.skillret"] == "src/magicite/eval/skillret.py"
