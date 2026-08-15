"""``obs/events.py``: the Tier-0 passive-inference capture path."""

from __future__ import annotations

import json

from magicite.obs import events as events_mod


def test_record_tool_call_appends_a_tier0_event(db_conn) -> None:
    events_mod.record_tool_call(
        db_conn, session_id="s1", tool="introspect", arguments={"skill_id": "egr_x"}
    )
    row = db_conn.execute(
        "SELECT tool, signal_tier, session_id, payload_json FROM eph_event WHERE tool = 'introspect'"
    ).fetchone()
    assert row is not None
    assert row["signal_tier"] == 0
    assert row["session_id"] == "s1"
    payload = json.loads(row["payload_json"])
    assert payload["args_sha256"] == events_mod.args_digest({"skill_id": "egr_x"})


def test_args_digest_is_order_independent_and_stable() -> None:
    a = events_mod.args_digest({"x": 1, "y": 2})
    b = events_mod.args_digest({"y": 2, "x": 1})
    assert a == b
    assert a == events_mod.args_digest({"x": 1, "y": 2})


def test_args_digest_differs_for_different_arguments() -> None:
    assert events_mod.args_digest({"x": 1}) != events_mod.args_digest({"x": 2})


def test_record_tool_call_allows_a_null_session_id(db_conn) -> None:
    events_mod.record_tool_call(db_conn, session_id=None, tool="register", arguments={"path": "."})
    row = db_conn.execute("SELECT session_id FROM eph_event WHERE tool = 'register'").fetchone()
    assert row["session_id"] is None
