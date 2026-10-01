"""Prior policy governance evidence stays live across memoized mirror reads."""

from __future__ import annotations

import json

from magicite.core import policy_store as ps


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
