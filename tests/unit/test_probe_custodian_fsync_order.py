"""Parser tests for scripts/probe_custodian_fsync_order.py (AC-TH-05 strace witness).

Synthetic traces only: runs on any OS, no strace required. The real trace is
produced by .github/workflows/r3-evidence.yml on Linux.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
C = "/srv/probe/custody"
R = "/srv/probe/registry"


def _load():
    path = ROOT / "scripts" / "probe_custodian_fsync_order.py"
    spec = importlib.util.spec_from_file_location("probe_custodian_fsync_order", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def mark(line: int, text: str) -> str:
    size = len(text) + 1
    return f'200 12:00:00.{line:06d} write(9</srv/probe/markers.log>, "{text}\\n", {size}) = {size}'


def head(line: int) -> str:
    return f'200 12:00:00.{line:06d} renameat(4<{R}>, ".trust-9f00", 4<{R}>, "head.json") = 0'


def sync(line: int, name: str = "authority.sqlite", call: str = "fsync") -> str:
    return f"200 12:00:00.{line:06d} {call}(3<{C}/{name}>) = 0"


def trace(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def evaluate(text: str) -> dict:
    return probe.evaluate(text, custody_dir=C, registry_dir=R)


def test_correct_order_passes():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            sync(2, "journal.jsonl"),
            mark(3, "MGP:commit:begin"),
            sync(4, "authority.sqlite-journal", "fdatasync"),
            sync(5),
            mark(6, "MGP:commit:end"),
            head(7),
            mark(8, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "pass"
    assert report["checks"]["custodian_fsync_completes_before_head_replace"] is True
    assert report["checks"]["custodian_synced_files"] == ["authority.sqlite", "authority.sqlite-journal"]
    kinds = [e["kind"] for e in report["events"]]
    assert "client-head-replace" in kinds and "custodian-sqlite-sync" in kinds


def test_violated_order_head_before_custodian_fsync():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            mark(2, "MGP:commit:begin"),
            head(3),
            sync(4),
            mark(5, "MGP:commit:end"),
            mark(6, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "violated"
    assert report["checks"]["custodian_fsync_completes_before_head_replace"] is False


def test_unfinished_fsync_completing_after_rename_start_is_a_violation():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            mark(2, "MGP:commit:begin"),
            f"201 12:00:00.000003 fsync(3<{C}/authority.sqlite> <unfinished ...>",
            mark(4, "MGP:commit:end"),
            head(5),
            "201 12:00:00.000006 <... fsync resumed>) = 0",
            mark(7, "MGP:append:end"),
        )
    )
    # Completion (the resumed line) counts: it falls outside the commit window.
    assert report["verdict"] == "missing-events"


def test_fsync_outside_commit_window_does_not_count():
    # A prepare_record fsync before commit_record must not satisfy the check.
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            sync(2),
            mark(3, "MGP:commit:begin"),
            mark(4, "MGP:commit:end"),
            head(5),
            mark(6, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "missing-events"
    assert any("fsync" in item for item in report["missing"])


def test_missing_head_replace():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            mark(2, "MGP:commit:begin"),
            sync(3),
            mark(4, "MGP:commit:end"),
            mark(5, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "missing-events"
    assert any("head.json" in item for item in report["missing"])


def test_missing_markers():
    report = evaluate(trace(sync(1), head(2)))
    assert report["verdict"] == "missing-events"
    assert len(report["missing"]) == 4


def test_failed_syscalls_and_foreign_paths_are_ignored():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            mark(2, "MGP:commit:begin"),
            "200 12:00:00.000003 fsync(3</elsewhere/authority.sqlite>) = 0",
            f"200 12:00:00.000004 fsync(3<{C}/authority.sqlite>) = -1 EIO (Input/output error)",
            mark(5, "MGP:commit:end"),
            head(6),
            mark(7, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "missing-events"


def test_journal_unlink_before_head_replace_passes():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            mark(2, "MGP:commit:begin"),
            sync(3),
            f'200 12:00:00.000004 unlinkat(AT_FDCWD</>, "{C}/authority.sqlite-journal", 0) = 0',
            mark(5, "MGP:commit:end"),
            head(6),
            mark(7, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "pass"
    assert report["checks"]["custodian_journal_unlink_before_head_replace"] is True


def test_journal_unlink_after_head_replace_is_a_violation():
    report = evaluate(
        trace(
            mark(1, "MGP:append:begin"),
            mark(2, "MGP:commit:begin"),
            sync(3),
            head(4),
            f'200 12:00:00.000005 unlink("{C}/authority.sqlite-journal") = 0',
            mark(6, "MGP:commit:end"),
            mark(7, "MGP:append:end"),
        )
    )
    assert report["verdict"] == "violated"
    assert report["checks"]["custodian_journal_unlink_before_head_replace"] is False


def test_rename_and_renameat2_forms_are_recognised():
    for line in (
        f'200 12:00:00.000006 rename("{R}/.trust-1", "{R}/head.json") = 0',
        f'200 12:00:00.000006 renameat2(4<{R}>, ".trust-1", 4<{R}>, "head.json", 0) = 0',
    ):
        report = evaluate(
            trace(
                mark(1, "MGP:append:begin"),
                mark(2, "MGP:commit:begin"),
                sync(3),
                mark(4, "MGP:commit:end"),
                line,
                mark(7, "MGP:append:end"),
            )
        )
        assert report["verdict"] == "pass", line


def test_split_args_respects_quotes_and_fd_paths():
    args = probe.split_args('4</a,b>, ".trust-x,y", 4</a,b>, "head.json"')
    assert args == ["4</a,b>", '".trust-x,y"', "4</a,b>", '"head.json"']


@pytest.mark.parametrize("name", sorted(probe.SYNTHETIC_TRACES))
def test_builtin_synthetic_traces(name):
    text, expected = probe.SYNTHETIC_TRACES[name]
    assert probe.evaluate(text, custody_dir=probe._C, registry_dir=probe._R)["verdict"] == expected


def test_self_test_and_cli_analyze(tmp_path, capsys):
    assert probe.main(["--self-test"]) == 0
    text, _ = probe.SYNTHETIC_TRACES["violated-order"]
    trace_path = tmp_path / "trace.txt"
    trace_path.write_text(text)
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({"custody_dir": probe._C, "registry_dir": probe._R}))
    out = tmp_path / "fsync-order.json"
    assert (
        probe.main(["--analyze", str(trace_path), "--run-manifest", str(manifest), "--output", str(out)]) == 1
    )
    report = json.loads(out.read_text())
    assert report["verdict"] == "violated"
    assert {"strace_version", "kernel", "events"} <= set(report)


def test_run_scenario_executes_production_append(tmp_path):
    manifest = probe.run_scenario(tmp_path / "probe")
    assert manifest["acknowledged_decision"] == "revoke"
    markers = Path(manifest["marker_path"]).read_text().split()
    assert markers == ["MGP:append:begin", "MGP:commit:begin", "MGP:commit:end", "MGP:append:end"]
