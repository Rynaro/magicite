"""AC-S15-03 mechanical scripted path; independent operator evidence remains separate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def test_disposable_operator_tutorial(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("tutorial", root / "scripts/smoke_operator_tutorial.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run_tutorial(tmp_path)
    assert result["status"] == "pass"
    assert result["independent_operator"] == "UNEVALUATED"
    assert result["published_install"] == "UNEVALUATED"
    steps = {step["step"] for step in result["steps"]}
    assert {
        "generic-stdio-handshake",
        "external-import",
        "route-explain-body",
        "stale-body-denied",
        "reroute-remediation",
        "backup restore",
        "migration apply",
        "migration restore",
        "durable-route-and-feedback",
    } <= steps
    assert str(tmp_path) not in json.dumps(result)
