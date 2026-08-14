from __future__ import annotations

from magicite.engram import ids


def test_new_engram_id_is_deterministic() -> None:
    payload = ids.identity_routing_payload(
        "sample", "does x", "use when y", "not when z", ["a", "b", "c"], ["d"]
    )
    first = ids.new_engram_id(payload)
    second = ids.new_engram_id(payload)
    assert first == second
    assert ids.is_valid_engram_id(first)


def test_new_engram_id_changes_with_intent() -> None:
    base = ids.identity_routing_payload(
        "sample", "does x", "use y", "not z", ["a", "b", "c"], ["d"]
    )
    changed = ids.identity_routing_payload(
        "sample", "does DIFFERENT", "use y", "not z", ["a", "b", "c"], ["d"]
    )
    assert ids.new_engram_id(base) != ids.new_engram_id(changed)


def test_content_and_body_sha_are_distinct_digests() -> None:
    content = ids.content_sha256(b"whole file bytes")
    body = ids.body_sha256("body only text")
    assert content != body
    assert len(content) == 64
    assert len(body) == 64


def test_is_valid_engram_id_rejects_malformed() -> None:
    assert not ids.is_valid_engram_id("not-an-id")
    assert not ids.is_valid_engram_id("egr_zzzzzzzz")
    assert not ids.is_valid_engram_id("egr_1234")
    assert ids.is_valid_engram_id("egr_deadbeef")
