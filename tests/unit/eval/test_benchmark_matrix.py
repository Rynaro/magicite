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


def _hardware_capacity_functions():
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts/qualify_performance_linux.sh"
    content = script.read_text()
    prefix = content.split("# Installed allocation is distinct", 1)[1]
    functions = prefix[prefix.index("def installed_memory_evidence") :].split(
        "# End installed allocation semantics.", 1
    )[0]
    namespace = {}
    exec(compile(functions, str(script), "exec"), namespace)
    return namespace["installed_memory_evidence"], namespace["allocation_failures"]


def test_reference_uses_installed_ram_and_logical_cpu_allocation():
    parse, failures = _hardware_capacity_functions()
    observation = {
        "exit": 0,
        "stdout": "Handle 0x0010, DMI type 17, 40 bytes\nMemory Device\n\tSize: 16 GB\n",
    }
    allocated, devices = parse(observation)
    assert allocated == 16 * 1024**3 and devices[0]["reported_size"] == "16 GB"
    # Usable MemTotal and physical cores remain observations, not allocation bounds.
    assert failures(allocated, "max", 4, None, usable=int(15.6 * 1024**3)) == []
    assert any("conflicts" in error for error in failures(allocated, "max", 4, None, usable=17 * 1024**3))
    assert failures(allocated, str(16 * 1024**3), 4, 4.0) == []


@pytest.mark.parametrize(
    ("allocated", "limit", "affinity", "quota", "reason"),
    [
        (None, "max", 4, None, "not substantiated"),
        (15 * 1024**3, "max", 4, None, "below original"),
        (16 * 1024**3, str(15 * 1024**3), 4, None, "restricts original"),
        (16 * 1024**3, None, 4, None, "limit unavailable"),
        (16 * 1024**3, "max", 3, None, "logical CPU"),
        (16 * 1024**3, "max", 4, 3.5, "logical CPU"),
    ],
)
def test_reference_refuses_unproved_or_restricted_allocation(allocated, limit, affinity, quota, reason):
    _, failures = _hardware_capacity_functions()
    assert any(reason in error for error in failures(allocated, limit, affinity, quota))


@pytest.mark.parametrize(
    "observation",
    [
        {"exit": 1, "stdout": "Memory Device\nSize: 16 GB\n"},
        {"exit": 0, "stdout": "advertised 16GB GitHub runner"},
        {"exit": 0, "stdout": "Memory Device\nSize: Unknown\n"},
        {"exit": 0, "stdout": "Memory Device\nSize: 16000000000 bytes\n"},
        {"exit": 0, "stdout": "Memory Device\nSize: No Module Installed\n"},
    ],
)
def test_reference_does_not_infer_ram_from_unknown_or_marketing_data(observation):
    parse, _ = _hardware_capacity_functions()
    assert parse(observation)[0] is None


def _all_hardware_functions():
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts/qualify_performance_linux.sh"
    functions = (
        script.read_text()
        .split("def installed_memory_evidence", 1)[1]
        .split("# End installed allocation semantics.", 1)[0]
    )
    namespace = {}
    exec(compile("def installed_memory_evidence" + functions, str(script), "exec"), namespace)
    return namespace


def test_reference_effective_limits_honor_finite_ancestor_restrictions():
    functions = _all_hardware_functions()
    rows = [
        {"memory_max": "max", "cpu_max": "max 100000", "global_root": False},
        {"memory_max": str(15 * 1024**3), "cpu_max": "350000 100000", "global_root": False},
        {"memory_max": None, "cpu_max": None, "global_root": True},
    ]
    memory, quota, errors = functions["effective_cgroup_limits"](rows)
    assert memory == str(15 * 1024**3) and quota == 3.5 and errors == []
    failures = functions["allocation_failures"](16 * 1024**3, memory, 4, quota)
    assert any("restricts original" in row for row in failures)
    assert any("logical CPU" in row for row in failures)
    assert functions["effective_cgroup_limits"]([])[2]
    assert functions["effective_cgroup_limits"]([{"global_root": False}])[2]


def test_native_arm_keeps_reference_comparisons_observational():
    outcome = _all_hardware_functions()["preflight_outcome"]
    failures = ["installed allocation unknown", "fewer than four CPU units"]
    assert outcome("aarch64", "Linux", failures, 20 * 1024**3) == ("ARM_OBSERVATION_READY", [])
    assert outcome("x86_64", "Linux", failures, 20 * 1024**3) == ("UNEVALUATED", failures)
    assert outcome("x86_64", "Linux", [], 20 * 1024**3) == ("REFERENCE_PREFLIGHT_PASSED", [])
    assert outcome("aarch64", "Darwin", [], 20 * 1024**3)[0] == "UNEVALUATED"
    assert outcome("aarch64", "Linux", [], 1 * 1024**3)[0] == "UNEVALUATED"


def _provider_fixture():
    # Consistency oracle only; fixtures never constitute actual runner evidence.
    context = {
        "GITHUB_RUN_ID": "101",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "a" * 40,
        "checked_out_commit": "a" * 40,
        "GITHUB_ACTOR": "operator",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": "Rynaro/magicite",
        "GITHUB_REF": "refs/tags/qualification/e6-rc4-20261009-04",
        "GITHUB_WORKFLOW_REF": (
            "Rynaro/magicite/.github/workflows/performance-qualification.yml@"
            "refs/tags/qualification/e6-rc4-20261009-04"
        ),
        "RUNNER_NAME": "GitHub Actions 123",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
    }
    repository = {"full_name": "Rynaro/magicite", "private": False}
    run = {
        "id": 101,
        "head_sha": "a" * 40,
        "event": "push",
        "path": ".github/workflows/performance-qualification.yml",
        "run_attempt": 1,
        "actor": {"login": "operator"},
    }
    jobs = {
        "jobs": [
            {
                "run_id": 101,
                "head_sha": "a" * 40,
                "name": "E6 / ubuntu-24.04",
                "labels": ["ubuntu-24.04"],
                "runner_id": 123,
                "runner_name": "GitHub Actions 123",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            }
        ]
    }
    tag = {"ref": context["GITHUB_REF"], "object": {"type": "commit", "sha": "a" * 40}}
    return repository, run, jobs, tag, context


def test_standard_provider_identity_requires_actual_matching_public_job_inputs():
    functions = _all_hardware_functions()
    proof = _provider_fixture()
    sha = "4f959a553da1bbfc86d4f24167981fb76177bb14c39b01296bc0ab6b6c779c38"
    assert functions["provider_identity_errors"](*proof, "ubuntu-24.04", "x86_64", sha) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "private",
        "repository",
        "source",
        "event",
        "tag",
        "custom",
        "group",
        "runner",
        "label",
        "native",
        "doc",
    ],
)
def test_provider_identity_refuses_forged_or_inapplicable_class(mutation):
    functions = _all_hardware_functions()
    repository, run, jobs, tag, context = _provider_fixture()
    sha = "4f959a553da1bbfc86d4f24167981fb76177bb14c39b01296bc0ab6b6c779c38"
    if mutation == "private":
        repository["private"] = True
    elif mutation == "repository":
        repository["full_name"] = "someone/else"
    elif mutation == "source":
        run["head_sha"] = "b" * 40
    elif mutation == "event":
        run["event"] = "workflow_dispatch"
    elif mutation == "tag":
        tag["object"]["sha"] = "b" * 40
    elif mutation == "custom":
        jobs["jobs"][0]["labels"] = ["self-hosted"]
    elif mutation == "group":
        jobs["jobs"][0]["runner_group_id"] = 42
    elif mutation == "runner":
        jobs["jobs"][0]["runner_name"] = "foreign"
    elif mutation == "label":
        context["RUNNER_NAME"] = "foreign"
    elif mutation == "native":
        context["RUNNER_ARCH"] = "ARM64"
    else:
        sha = "0" * 64
    assert functions["provider_identity_errors"](
        repository, run, jobs, tag, context, "ubuntu-24.04", "x86_64", sha
    )


def _guest_storage_fixture():
    fs = {"source": "/dev/sda1", "fstype": "ext4", "maj:min": "8:1", "target": "/"}
    devices = [
        {
            "name": "sda",
            "type": "disk",
            "rota": True,
            "maj:min": "8:0",
            "children": [{"name": "sda1", "type": "part", "rota": True, "maj:min": "8:1"}],
        }
    ]
    return fs, devices


def test_provider_class_keeps_true_virtual_rotational_observation():
    functions = _all_hardware_functions()
    fs, devices = _guest_storage_fixture()
    mapping = functions["guest_local_mapping"]([fs], devices)
    assert mapping["rotational"] is True
    sysfs = {
        "source_is_block_device": True,
        "block_major_minor": "8:1",
        "sysfs_device_major_minor": "8:1",
        "queue_rotational": "1",
    }
    assert functions["guest_storage_errors"](mapping, mapping, mapping, sysfs) == []


@pytest.mark.parametrize("mutation", ["network", "overlay", "custom", "unknown", "unresolved"])
def test_guest_mapping_refuses_nonlocal_custom_or_unknown_volume(mutation):
    functions = _all_hardware_functions()
    fs, devices = _guest_storage_fixture()
    if mutation == "network":
        fs["fstype"] = "nfs"
    elif mutation == "overlay":
        fs["fstype"] = "overlay"
    elif mutation == "custom":
        fs["target"] = "/custom-volume"
    elif mutation == "unknown":
        fs.pop("maj:min")
    else:
        devices = []
    assert functions["guest_local_mapping"]([fs], devices) is None


def test_created_workload_mapping_and_sysfs_conflicts_refuse():
    functions = _all_hardware_functions()
    fs, devices = _guest_storage_fixture()
    mapping = functions["guest_local_mapping"]([fs], devices)
    sysfs = {
        "source_is_block_device": True,
        "block_major_minor": "8:1",
        "sysfs_device_major_minor": "8:1",
        "queue_rotational": "0",
    }
    assert functions["guest_storage_errors"](mapping, mapping, mapping, sysfs)
    sysfs["queue_rotational"] = "1"
    other = deepcopy(mapping)
    other["filesystem"]["maj:min"] = "8:17"
    assert functions["guest_storage_errors"](mapping, other, mapping, sysfs)
    assert functions["guest_storage_errors"](mapping, None, mapping, sysfs)


def test_public_input_acquisition_refuses_wrong_pinned_bytes(tmp_path, monkeypatch):
    import importlib.util
    import io
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts/prepare_performance_corpus.py"
    spec = importlib.util.spec_from_file_location("e6_pinned_input", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    response = io.BytesIO(b"bad")
    response.url = "https://raw.githubusercontent.com/official/immutable"
    response.status = 200
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: response)
    payload = tmp_path / "bad-license.html"
    with pytest.raises(ValueError, match="digest/length mismatch"):
        module.acquire(response.url, payload, "0" * 64, 3)
    assert payload.read_bytes() == b"bad"


def test_custody_subprocess_failure_preserves_public_streams_and_stage(tmp_path):
    import ast
    import json
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    source = (Path(__file__).resolve().parents[3] / "scripts/qualify_performance_linux.sh").read_text()
    program = source.split("<<'EXECUTE'\n", 1)[1].split("\nEXECUTE", 1)[0]
    tree = ast.parse(program)
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "command"
    )
    fake = SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(
            returncode=2, stdout="public reply", stderr="project not readable"
        ),
        CalledProcessError=subprocess.CalledProcessError,
    )
    namespace = {
        "base": tmp_path,
        "command_sequence": 0,
        "py": "/installed/python",
        "project_gid": 41013,
        "env": {},
        "subprocess": fake,
        "out": tmp_path,
        "json": json,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "approved-custody-command", "exec"), namespace)
    with pytest.raises(subprocess.CalledProcessError) as error:
        namespace["command"](41011, ["profile", "--project-root", "/owned/project"])
    assert error.value.returncode == 2 and error.value.stderr == "project not readable"
    receipt = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert receipt["stage"] == "profile" and receipt["uid"] == 41011 and receipt["extra_groups"] == [41013]
    assert (tmp_path / receipt["stdout_file"]).read_text() == "public reply"
    assert (tmp_path / receipt["stderr_file"]).read_text() == "project not readable"
    assert "environment" not in receipt and "private_store" not in receipt


def test_shipped_empty_bootstrap_lock_is_accepted_without_changing_bytes(tmp_path):
    from magicite.config import Config
    from magicite.storage import db

    module = _matrix_module()
    cfg = Config(project_root=tmp_path.resolve())
    cfg.ensure_dirs()
    db.connect(cfg.db_path).close()
    cfg.dream_lock_path.write_bytes(b"")
    cfg.dream_lock_path.chmod(0o600)
    assert module._clean_project_root(cfg.project_root, protected=False)["project_root"] == str(
        cfg.project_root
    )
    assert cfg.dream_lock_path.read_bytes() == b""


@pytest.mark.parametrize("kind", ["nonempty", "directory", "symlink", "group-writable"])
def test_bootstrap_lock_exception_refuses_nonempty_nonregular_alias_or_writable(tmp_path, kind):
    from magicite.config import Config

    module = _matrix_module()
    cfg = Config(project_root=tmp_path.resolve())
    cfg.ensure_dirs()
    if kind == "directory":
        cfg.dream_lock_path.mkdir()
    elif kind == "symlink":
        cfg.dream_lock_path.symlink_to(tmp_path / "outside-missing-target")
    else:
        cfg.dream_lock_path.write_bytes(b"residue" if kind == "nonempty" else b"")
        if kind == "group-writable":
            cfg.dream_lock_path.chmod(0o660)
    with pytest.raises(ValueError, match="bootstrap writer lock"):
        module._clean_project_root(cfg.project_root, protected=False)
    if kind == "nonempty":
        assert cfg.dream_lock_path.read_bytes() == b"residue"
