"""Pytest plugin: record exceptions captured by ``pytest.raises`` and node outcomes.

Loaded only by the adversarial trust corpus runner in a child pytest process
(``-p tests.support.raises_recorder``) so the corpus can prove that each
witness node really observes the declared fail-closed exception and message.
Writes JSON to ``MAGICITE_RAISES_LOG``: {"raises": {node: [[type, msg]]},
"outcomes": {node: "passed" | "failed" | "skipped"}}.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

_original_raises = pytest.raises
_current: dict[str, str | None] = {"node": None}
_raises: dict[str, list[list[str]]] = {}
_outcomes: dict[str, str] = {}


class _Recording:
    def __init__(self, context: Any):
        self.context = context

    def __enter__(self) -> Any:
        return self.context.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        suppressed = self.context.__exit__(*exc)
        node = _current["node"]
        if exc[1] is not None and node:
            _raises.setdefault(node, []).append([type(exc[1]).__name__, str(exc[1])[:500]])
        return suppressed


def _recording_raises(*args: Any, **kwargs: Any) -> Any:
    context = _original_raises(*args, **kwargs)
    if len(args) > 1 or "func" in kwargs:  # legacy callable form returns ExceptionInfo
        return context
    return _Recording(context)


pytest.raises = _recording_raises  # type: ignore[assignment]


def pytest_runtest_setup(item: pytest.Item) -> None:
    _current["node"] = item.nodeid


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" or report.outcome != "passed":
        previous = _outcomes.get(report.nodeid)
        if previous in (None, "passed"):
            _outcomes[report.nodeid] = report.outcome


def pytest_sessionfinish(session: pytest.Session) -> None:
    destination = os.environ.get("MAGICITE_RAISES_LOG")
    if destination:
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump({"raises": _raises, "outcomes": _outcomes}, handle, indent=1, sort_keys=True)
