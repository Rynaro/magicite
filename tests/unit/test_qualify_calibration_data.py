"""Executable offline packet workflow; tests do not authenticate synthetic declarations."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "calibration_controls_runner", ROOT / "scripts/qualify_calibration_data.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_actual_runner_validate_freeze_guard_and_replay(tmp_path, monkeypatch, capsys):
    packet_root = tmp_path / "packet"
    shutil.copytree(ROOT / "tests/fixtures/calibration-data", packet_root)
    packet = packet_root / "packet.json"
    freeze = tmp_path / "freeze.json"
    # Unit-bound source gate; actual clean-candidate evidence runs separately.
    commit = runner.git("rev-parse", "HEAD")
    monkeypatch.setattr(runner, "clean_candidate", lambda c: runner.source_inputs())
    assert runner.main(["validate", str(packet)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["report"]["qualifying"] is False
    assert runner.main(["freeze", str(packet), "--output", str(freeze), "--source-commit", commit]) == 0
    capsys.readouterr()
    assert runner.main(["verify", str(freeze)]) == 0
    capsys.readouterr()
    assert runner.main(["open", str(freeze), "--purpose", "fixture-only"]) == 0
    opened = json.loads(capsys.readouterr().out)
    assert opened["qualifying"] is False and opened["authentic_readiness"] == "UNEVALUATED"
    receipt = runner.data.access_path(freeze).read_bytes()
    assert runner.main(["open", str(freeze), "--purpose", "fixture-only"]) == 1
    capsys.readouterr()
    assert runner.main(["open", str(freeze), "--purpose", "fixture-only", "--replay"]) == 0
    assert runner.data.access_path(freeze).read_bytes() == receipt
    capsys.readouterr()
    monkeypatch.setattr(runner, "source_inputs", lambda: {"changed": "0" * 64})
    assert runner.main(["open", str(freeze), "--purpose", "fixture-only", "--replay"]) == 1
    assert "source candidate binding" in capsys.readouterr().err


def test_runner_rejects_dirty_or_wrong_candidate_before_packet(tmp_path):
    with pytest.raises(ValueError, match="clean source"):
        runner.clean_candidate("0" * 40)
    assert not (tmp_path / "freeze.json").exists()


def test_runner_has_no_model_rank_fit_or_activation_calls():
    import ast

    tree = ast.parse((ROOT / "scripts/qualify_calibration_data.py").read_text())
    imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert all(
        not any(x in name for x in ("production", "embeddings", "core.calibration", "runner"))
        for name in imports
    )
    calls = {
        n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not calls & {"fit", "embed", "predict", "route", "activate", "save_calibration"}
