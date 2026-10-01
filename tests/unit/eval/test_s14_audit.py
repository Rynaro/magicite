"""Independent-audit regressions anchored to frozen E3/E4/E6."""

from math import nan

import pytest

from magicite.eval.envelopes import validate_envelope
from magicite.eval.host_tasks import HostTaskArmResult, evaluate_paired_host_tasks
from magicite.eval.metrics import BootstrapInterval, abstention_report
from magicite.eval.operator_cli import _demote_pass
from magicite.eval.profiles import PROFILE_SUPPORTED_10K, build_profile_result_skeleton
from magicite.eval.verdicts import holm_critical_slice_family


def test_nested_gate_pass_is_demoted_even_under_inconclusive_parent():
    result = _demote_pass({"status": "inconclusive", "details": [{"status": "pass"}]})
    assert result["details"][0]["status"] == "unevaluated"


@pytest.mark.parametrize("value", [nan, float("inf"), -1, True])
def test_invalid_budget_numbers_cannot_qualify(value):
    result = build_profile_result_skeleton(
        PROFILE_SUPPORTED_10K,
        provider="production",
        model_name="fake",
        model_digest=None,
        runner_label="unit",
    )
    result["measurements"] = dict.fromkeys(
        [
            "warm_route_p50_ms",
            "warm_route_p95_ms",
            "warm_route_p99_ms",
            "process_rss_gib",
            "index_gib",
            "cold_ready_s",
            "index_build_s",
            "index_build_peak_rss_gib",
            "payload_tokens",
            "cache_hit_rates",
            "truncations_fallbacks",
        ],
        value,
    )
    assert not validate_envelope(result, PROFILE_SUPPORTED_10K, mode="budget").ok


def test_host_timeout_remains_in_paired_denominator():
    rows = []
    for group in range(30):
        for task, outcomes in (("good", ("fail", "fail", "pass")), ("timed", ("pass", "pass", "timeout"))):
            for arm, outcome in zip(("no_skill", "selected_skill", "composed_plan"), outcomes, strict=True):
                rows.append(
                    HostTaskArmResult(f"{group}-{task}", str(group), arm, outcome, "verifier", "a" * 64)
                )
    report = evaluate_paired_host_tasks(rows, n_resamples=100)
    assert report.usefulness_delta_interval.point_estimate == 0
    assert report.usefulness_status != "pass"
    incomplete = rows[:-1]
    assert evaluate_paired_host_tasks(incomplete, n_resamples=100).usefulness_status == "unevaluated"


def test_one_sided_false_selection_wilson_reference():
    report = abstention_report(answerable_selected=[True] * 60, no_match_selected=[False] * 60)
    assert report.false_selection_wilson_high == pytest.approx(0.043147, abs=1e-6)


def test_holm_uses_p_values_and_step_down_stop():
    interval = BootstrapInterval(
        point_estimate=0, low=-0.01, high=0.01, n_groups=30, n_resamples=10000, seed=0
    )
    result = holm_critical_slice_family(
        [(name, interval) for name in ("a", "b", "c")], inferiority_p_values={"a": 0.01, "b": 0.03, "c": 0.04}
    )
    assert result.details["adjusted_p_values"] == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})
    assert [item["slice"] for item in result.details["failures"]] == ["a"]
    assert holm_critical_slice_family([("a", interval)]).status == "inconclusive"


def test_authentic_manifest_ingests_full_bodies_and_rejects_tampering(tmp_path, embedder, custody_for):
    import importlib.util
    import json
    from pathlib import Path

    from magicite.config import Config
    from magicite.eval.digests import sha256_path
    from magicite.eval.external import recompute_content_identity_sha256
    from magicite.storage import db

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("matrix_audit", root / "scripts/run_benchmark_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    refs = []
    names = ["proton-verify-installation", "proton-clean-install", "steam-prefix-access"]
    for name in names:
        target = corpus_dir / f"{name}.egr.md"
        target.write_bytes((root / "tests/fixtures/toy-registry/engrams" / target.name).read_bytes())
        refs.append(
            {
                "path": target.name,
                "sha256": sha256_path(target),
                "role": "skill",
                "byte_length": target.stat().st_size,
            }
        )
    from io import StringIO

    from ruamel.yaml import YAML

    from magicite.engram import parser

    asset = corpus_dir / "assets/helper.txt"
    asset.parent.mkdir()
    asset.write_text("resource used by the real v1 body")
    raw_v1 = (root / "tests/fixtures/engram-v1/positive/sample-host-tooling.egr.md").read_text()
    yaml_text, body = parser.split_frontmatter(raw_v1)
    frontmatter = parser.load_frontmatter_doc(yaml_text)
    frontmatter["compatibility"] = {"os": ["linux"]}
    frontmatter["capabilities"] = {"requires": [], "produces": [], "alternatives": [], "conflicts_with": []}
    frontmatter["relations"] = {"requires": [], "before": [], "supersedes": []}
    frontmatter["risk"] = {
        "filesystem": "none",
        "subprocess": {"mode": "none", "tools": []},
        "network": {"mode": "none", "destinations": []},
        "secrets": "none",
    }
    frontmatter["assets"] = {
        "assets/helper.txt": {"sha256": sha256_path(asset), "size": asset.stat().st_size}
    }
    stream = StringIO()
    YAML().dump(frontmatter, stream)
    v1 = corpus_dir / "sample-host-tooling.egr.md"
    v1.write_text("---\n" + stream.getvalue() + "---\n" + body)
    for target, role in ((v1, "skill"), (asset, "asset")):
        refs.append(
            {
                "path": str(target.relative_to(corpus_dir)),
                "sha256": sha256_path(target),
                "byte_length": target.stat().st_size,
                "role": role,
            }
        )
    data = json.loads((root / "docs/evaluation/v1/fixtures/skillret-tiny/corpus_manifest.json").read_text())
    data["artifacts"] = refs
    data["queries"] = data["queries"][:1]
    query = data["queries"][0]
    query["relevance"] = {"sample-host-tooling": 1}
    query["accepted_plans"] = [["sample-host-tooling"]]
    query["query_text"] = "Prepare a host toolchain for Proton prefix repair"
    query["compatibility_context"] = {
        "platform": "linux",
        "excluded_engram_ids": ["egr_4a57b485", "egr_9f0e8e70", "egr_f0b27d6a"],
    }
    data["content_identity_sha256"] = recompute_content_identity_sha256(data["queries"])
    manifest = corpus_dir / "corpus_manifest.json"
    manifest.write_text(json.dumps(data))
    import tarfile

    from magicite.eval.operator_cli import cmd_acquire_skillret

    archive = tmp_path / "archive.tar"
    with tarfile.open(archive, "w") as tar:
        for source in corpus_dir.rglob("*"):
            if source.is_file():
                tar.add(source, arcname=str(source.relative_to(corpus_dir)))
    acquired = tmp_path / "acquired/corpus.json"
    cmd_acquire_skillret(
        archive=archive,
        expected_sha256=sha256_path(archive),
        license_name="fixture-only",
        revision="test",
        corpus_json=tmp_path / "missing/corpus_manifest.json",
        output=acquired,
    )
    assert (acquired.parent / "assets/helper.txt").read_bytes() == asset.read_bytes()
    cfg = Config(project_root=tmp_path / "registry")
    cfg.ensure_dirs()
    custody_for(cfg)
    conn = db.connect(cfg.db_path)
    try:
        metadata, queries = module._build_registry_from_corpus_manifest(
            conn,
            corpus_manifest_path=acquired,
            model_name=embedder.model_name,
            dim=embedder.dim,
            embedder=embedder,
            cfg=cfg,
        )
        assert metadata["n_candidates"] == 4  # Includes unlabeled distractors.
        assert metadata["actual_artifacts"] is True
        assert queries
        assert conn.execute("SELECT count(*) FROM engram").fetchone()[0] == 4
        assert (cfg.registry_dir / "assets/helper.txt").read_bytes() == asset.read_bytes()
        _, outcome = module._timed_route(cfg, conn, embedder, query=queries[0], session_id="asset-probe")
        assert outcome.decision.status == "selected", (
            outcome.decision.reason_codes,
            outcome.decision.exclusions,
        )
        assert "egr_a1b2c3d4" in outcome.decision.selected_ids
        (acquired.parent / refs[0]["path"]).write_text("tampered")
        with pytest.raises(ValueError, match="digest"):
            module._build_registry_from_corpus_manifest(
                conn,
                corpus_manifest_path=acquired,
                model_name=embedder.model_name,
                dim=embedder.dim,
                embedder=embedder,
                cfg=cfg,
            )
    finally:
        conn.close()


def test_ga_requires_both_matching_complete_evidence_strata():
    from copy import deepcopy

    from magicite.eval.envelopes import compute_ga_eligibility

    profile = PROFILE_SUPPORTED_10K
    result = build_profile_result_skeleton(
        profile,
        provider="production",
        model_name="actual-model",
        model_digest="a" * 64,
        runner_label="dedicated-linux-amd64-4c-16g",
    )
    from magicite.core.index_generation import TOKENIZER_ID

    result["fingerprint"].update(
        payload_tokenizer=TOKENIZER_ID,
        payload_unit="lexical-word-tokens",
        payload_surface="RouteOutput/1 JSON",
        payload_aggregation="max-measured-responses",
    )
    result["status"] = "measured"
    for state in result["cache_states"].values():
        state.update(measured=True, latency_ms=1)
    result["measurements"] = {
        "warm_route_p50_ms": 1,
        "warm_route_p95_ms": 1,
        "warm_route_p99_ms": 1,
        "process_rss_gib": 0.5,
        "index_gib": 0.5,
        "cold_ready_s": 1,
        "index_build_s": 1,
        "index_build_peak_rss_gib": 0.5,
        "payload_tokens": 10,
        "cache_hit_rates": {"route_index": 0.9},
        "truncations_fallbacks": [],
        "warm_durations_s": [0.001] * 1000,
    }
    result["repetition_results"] = [
        dict(
            process_id=i,
            measured_queries=1000,
            warmup_queries=50,
            status="measured",
            measurements=deepcopy(result["measurements"]),
        )
        for i in (1, 2, 3)
    ]
    result["corpus"] = dict(
        kind="manifest",
        actual_artifacts=True,
        artifact_inventory_sha256="b" * 64,
        manifest_sha256="c" * 64,
        content_identity_sha256="d" * 64,
        n_candidates=10000,
        corpus_id="licensed-v1",
        license="MIT",
        dataset_revision="v1",
    )
    synthetic = deepcopy(result)
    synthetic["corpus"]["kind"] = "synthetic"
    args = dict(
        provider="production",
        profile_ga_support_claim=True,
        corpus_kind="manifest",
        envelope_mode="budget",
        envelope_budget_ok=True,
        corpus_path="/data/corpus.json",
        corpus_license="MIT",
        n_candidates=10000,
        profile_corpus_artifacts=10000,
        measured_result=result,
        support_runs=[synthetic],
    )
    assert compute_ga_eligibility(**args) == (True, [])
    tokenizer = synthetic["fingerprint"].pop("payload_tokenizer")
    assert compute_ga_eligibility(**args)[0] is False
    synthetic["fingerprint"]["payload_tokenizer"] = "different-tokenizer"
    assert compute_ga_eligibility(**args)[0] is False
    synthetic["fingerprint"]["payload_tokenizer"] = tokenizer
    synthetic["fingerprint"]["payload_surface"] = "internal-dataclass"
    assert compute_ga_eligibility(**args)[0] is False
    synthetic["fingerprint"]["payload_surface"] = "RouteOutput/1 JSON"
    synthetic["profile"]["profile_id"] = "small-100"
    assert compute_ga_eligibility(**args)[0] is False
    synthetic["profile"]["profile_id"] = "supported-10k"
    synthetic["fingerprint"]["model_digest"] = "c" * 64
    assert compute_ga_eligibility(**args)[0] is False
    synthetic["fingerprint"]["model_digest"] = "a" * 64
    result["corpus"]["corpus_id"] = "copied-fixture"
    assert compute_ga_eligibility(**args)[0] is False


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "device", "backslash", "duplicate"])
def test_archive_refuses_nonregular_or_nonportable_members(tmp_path, kind):
    import io
    import tarfile

    from magicite.eval.operator_cli import _safe_extract

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        member = tarfile.TarInfo("safe" if kind != "backslash" else "bad\\name")
        if kind == "hardlink":
            member.type = tarfile.LNKTYPE
            member.linkname = "safe"
        elif kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "/tmp"
        elif kind == "device":
            member.type = tarfile.CHRTYPE
        tar.addfile(member)
        if kind == "duplicate":
            tar.addfile(member)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as tar, pytest.raises(ValueError):
        _safe_extract(tar, tmp_path)


def test_corpus_context_keeps_typed_host_facts_and_permissions():
    from magicite.eval.query_context import corpus_route_context

    ctx = corpus_route_context(
        {
            "platform": "linux",
            "host": {"id": "cursor", "version": "1.0"},
            "permission_grants": ["filesystem.read-project"],
            "artifact_inventory": [{"id": "artifact.prefix", "version": "1.0"}],
            "no_match": False,
        }
    )
    assert ctx.platform == "linux"
    assert ctx.host.id == "cursor"
    assert ctx.artifact_inventory[0].id == "artifact.prefix"
    assert ctx.permission_grants == frozenset({"filesystem.read-project"})
