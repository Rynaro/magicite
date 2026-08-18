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


def test_adapter_secret_redacted_before_hashing() -> None:
    first = {"session_id": "s1", "adapter_token": "candidate-one"}
    second = {"session_id": "s1", "adapter_token": "candidate-two"}

    assert events_mod.args_digest(first) == events_mod.args_digest(second)
    assert events_mod.redact_arguments(first)["adapter_token"] == events_mod.REDACTED_ARGUMENT


def test_nested_adapter_secret_is_also_redacted() -> None:
    first = {"wrapper": {"adapter_token": "candidate-one"}}
    second = {"wrapper": {"adapter_token": "candidate-two"}}
    assert events_mod.args_digest(first) == events_mod.args_digest(second)


def test_persisted_event_cannot_verify_adapter_secret(db_conn) -> None:
    """AC-037: weak candidate secrets produce identical persisted evidence."""
    for candidate in ("candidate-one", "candidate-two"):
        events_mod.record_tool_call(
            db_conn,
            session_id="redaction-proof",
            tool="signal_use",
            arguments={"skill_ids": ["egr_x"], "adapter_token": candidate},
        )
    rows = db_conn.execute(
        "SELECT payload_json FROM eph_event WHERE session_id = ? ORDER BY id",
        ("redaction-proof",),
    ).fetchall()
    digests = [json.loads(str(row["payload_json"]))["args_sha256"] for row in rows]
    assert digests[0] == digests[1]


def test_record_tool_call_allows_a_null_session_id(db_conn) -> None:
    events_mod.record_tool_call(db_conn, session_id=None, tool="register", arguments={"path": "."})
    row = db_conn.execute("SELECT session_id FROM eph_event WHERE tool = 'register'").fetchone()
    assert row["session_id"] is None
