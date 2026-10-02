"""Prior policy governance evidence stays live across memoized mirror reads."""

from __future__ import annotations

import json

import pytest

from magicite.core import policy_store as ps
from magicite.errors import InvalidInputError


def _write(path, op: str) -> None:
    path.write_text(json.dumps({"op": op, "state": "approved"}), encoding="utf-8")


def test_in_place_mirror_edit_is_seen_after_cached_negative(cfg) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    mirror = cfg.approvals_dir / "a1.json"
    _write(mirror, "review_approve")
    assert ps.prior_policy_governance_evidence(cfg) is False
    assert ps.prior_policy_governance_evidence(cfg) is False
    _write(mirror, "policy_activate")
    assert ps.prior_policy_governance_evidence(cfg) is True


def test_new_mirror_is_seen_and_removed_mirror_is_forgotten(cfg) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    _write(cfg.approvals_dir / "a1.json", "review_approve")
    assert ps.prior_policy_governance_evidence(cfg) is False
    rollback = cfg.approvals_dir / "a2.json"
    _write(rollback, "policy_rollback")
    assert ps.prior_policy_governance_evidence(cfg) is True
    rollback.unlink()
    assert ps.prior_policy_governance_evidence(cfg) is False
    assert str(rollback) not in ps._APPROVAL_OP_CACHE


def test_prune_survives_cache_mutation_during_scan(cfg, monkeypatch) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    _write(cfg.approvals_dir / "a1.json", "review_approve")
    armed = {"on": False}

    class _HookKey(str):
        # Hashed while the prune loop tests ``k not in seen``; mutates the
        # module cache at that moment (a concurrent writer's effect).
        def __hash__(self) -> int:
            if armed["on"]:
                armed["on"] = False
                ps._APPROVAL_OP_CACHE[str(cfg.approvals_dir / "late.json")] = ((0, 0, 0, 0, 0), False)
            return str.__hash__(self)

    stale = _HookKey(str(cfg.approvals_dir / "gone.json"))
    monkeypatch.setattr(ps, "_APPROVAL_OP_CACHE", {stale: ((0, 0, 0, 0, 0), False)})
    armed["on"] = True
    assert ps.prior_policy_governance_evidence(cfg) is False
    assert str(cfg.approvals_dir / "gone.json") not in ps._APPROVAL_OP_CACHE


@pytest.mark.parametrize("payload", [b"[]", b'"policy_activate"', b"7", b"null"])
def test_non_object_mirror_fails_closed_with_typed_error(cfg, payload: bytes) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    mirror = cfg.approvals_dir / "bad.json"
    mirror.write_bytes(payload)
    with pytest.raises(InvalidInputError):
        ps.prior_policy_governance_evidence(cfg)
    assert str(mirror) not in ps._APPROVAL_OP_CACHE


def test_invalid_utf8_mirror_fails_closed_with_typed_error(cfg) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    (cfg.approvals_dir / "bad.json").write_bytes(b'{"op": "\xff\xfe"}')
    with pytest.raises(InvalidInputError):
        ps.prior_policy_governance_evidence(cfg)


def test_truncated_json_mirror_is_still_skipped(cfg) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    (cfg.approvals_dir / "bad.json").write_text('{"op": ', encoding="utf-8")
    assert ps.prior_policy_governance_evidence(cfg) is False
