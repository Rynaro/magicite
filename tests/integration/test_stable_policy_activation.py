"""AC-S07-05: durable policy store CAS activation without S10 learning."""

from __future__ import annotations

import json

import pytest

from magicite.core import fingerprint_key as fk
from magicite.core import policy_store as ps
from magicite.core import routing_policy as policy_mod
from magicite.errors import InvalidInputError, NotFoundError
from magicite.storage import lease as lease_mod


def _manifest(cfg, *, policy_id: str, digest: str | None = None, family: str = "stable") -> ps.PolicyManifest:
    d = digest or policy_mod.compute_policy_digest(policy_id, cfg)
    return ps.PolicyManifest(
        policy_id=policy_id,
        policy_digest=d,
        policy_family=family,  # type: ignore[arg-type]
        config_digest=policy_mod.compute_config_digest(cfg),
        calibration_digest=None,
        index_generation_id="gen_fixture",
        snapshot_id="snap_fixture",
        selection="cosine_similarity" if policy_id == policy_mod.POLICY_DENSE_V1 else "hybrid_rrf",
    )


def test_activation_without_learning(cfg) -> None:
    """GIVEN S10 is not installed and reviewed simple/hybrid policy artifacts exist
    WHEN activation and rollback use the expected-current API
    THEN the active digest SHALL follow exactly the approved compare-and-swap transitions
    including stale-current rejection.
    """
    cfg.ensure_dirs()
    fk.set_fingerprint_key_override(b"\x22" * fk.KEY_BYTES)
    try:
        dense = _manifest(cfg, policy_id=policy_mod.POLICY_DENSE_V1)
        hybrid_digest = "b" * 64
        hybrid = ps.PolicyManifest(
            policy_id="hybrid-rrf-v1",
            policy_digest=hybrid_digest,
            policy_family="stable",
            config_digest=policy_mod.compute_config_digest(cfg),
            calibration_digest=None,
            index_generation_id="gen_hybrid",
            snapshot_id="snap_hybrid",
            selection="hybrid_rrf",
        )

        # Hybrid cannot claim PASS without a real empirical run — record UNEVALUATED.
        evidence = ps.retain_simple_incumbent_evidence(
            incumbent_policy_id=dense.policy_id,
            incumbent_digest=dense.policy_digest,
            hybrid_status="unevaluated",
            evidence="no external corpus available in S07 worktree; hybrid UNEVALUATED",
        )
        assert evidence["default_remains_simple_incumbent"] is True
        with pytest.raises(InvalidInputError):
            ps.retain_simple_incumbent_evidence(
                incumbent_policy_id=dense.policy_id,
                incumbent_digest=dense.policy_digest,
                hybrid_status="pass",
                evidence="fabricated",
            )

        ps.register_evaluated(cfg, dense, evaluation_status="pass", evidence="fixture simple incumbent")
        ps.register_evaluated(
            cfg,
            hybrid,
            evaluation_status="unevaluated",
            evidence="UNEVALUATED: no SkillRet run in this environment",
        )

        dense_approval = ps.approve(cfg, dense.policy_digest, actor="reviewer")
        # Unevaluated hybrid may be approved only after explicit evaluation status —
        # raise it to inconclusive (failed promotion) then approve for CAS tests.
        ps.register_evaluated(
            cfg,
            hybrid,
            evaluation_status="inconclusive",
            evidence="inconclusive paired comparison; retain simple incumbent",
        )
        hybrid_approval = ps.approve(cfg, hybrid.policy_digest, actor="reviewer")

        # Activate dense from empty incumbent.
        st = ps.activate(
            cfg,
            expected_current=None,
            candidate_digest=dense.policy_digest,
            approval_id=dense_approval,
        )
        assert st.active_digest == dense.policy_digest
        assert st.prior_digest is None

        # Stale expected-current must reject.
        with pytest.raises(InvalidInputError, match="stale expected_current"):
            ps.activate(
                cfg,
                expected_current=None,
                candidate_digest=hybrid.policy_digest,
                approval_id=hybrid_approval,
            )

        # CAS to hybrid (still reviewed; promotion evidence inconclusive).
        st2 = ps.activate(
            cfg,
            expected_current=dense.policy_digest,
            candidate_digest=hybrid.policy_digest,
            approval_id=hybrid_approval,
        )
        assert st2.active_digest == hybrid.policy_digest
        assert st2.prior_digest == dense.policy_digest

        # Exact rollback to previous approved incumbent + matching manifests.
        st3 = ps.rollback(
            cfg,
            expected_current=hybrid.policy_digest,
            prior_digest=dense.policy_digest,
        )
        assert st3.active_digest == dense.policy_digest
        active = ps.get_active_manifest(cfg)
        assert active is not None
        assert active.policy_id == policy_mod.POLICY_DENSE_V1
        assert active.index_generation_id == "gen_fixture"
        assert active.config_digest == dense.config_digest

        # Rollback to missing / unreviewed artifact fails closed.
        with pytest.raises(NotFoundError):
            ps.rollback(
                cfg,
                expected_current=dense.policy_digest,
                prior_digest="f" * 64,
            )

        ghost = ps.PolicyManifest(
            policy_id="ghost",
            policy_digest="c" * 64,
            policy_family="stable",
            config_digest=dense.config_digest,
            calibration_digest=None,
            index_generation_id=None,
            snapshot_id=None,
            selection="cosine_similarity",
            reviewed=False,
            evaluation_status="pass",
        )
        ps.register_evaluated(cfg, ghost, evaluation_status="pass", evidence="x")
        with pytest.raises(InvalidInputError):
            ps.activate(
                cfg,
                expected_current=dense.policy_digest,
                candidate_digest=ghost.policy_digest,
                approval_id="nope",
            )

        # Corruption / torn integrity fails closed.
        path = ps.policy_store_path(cfg)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["active_digest"] = "tampered"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(InvalidInputError, match="integrity MAC"):
            ps.status(cfg)

        # Concurrent activation from another thread must see BusyError.
        path.unlink()
        ps.register_evaluated(cfg, dense, evaluation_status="pass", evidence="restore")
        approval = ps.approve(cfg, dense.policy_digest, actor="reviewer")
        ps.activate(
            cfg,
            expected_current=None,
            candidate_digest=dense.policy_digest,
            approval_id=approval,
        )
        ps.register_evaluated(
            cfg,
            hybrid,
            evaluation_status="inconclusive",
            evidence="restore hybrid",
        )
        hybrid_approval2 = ps.approve(cfg, hybrid.policy_digest, actor="reviewer")

        import threading

        from magicite.errors import BusyError

        err_box: list[BaseException] = []

        def _other_activate() -> None:
            try:
                ps.activate(
                    cfg,
                    expected_current=dense.policy_digest,
                    candidate_digest=hybrid.policy_digest,
                    approval_id=hybrid_approval2,
                )
            except BaseException as exc:  # noqa: BLE001 — capture for assertion
                err_box.append(exc)

        with lease_mod.writer_lease(holder="holder-a"):
            t = threading.Thread(target=_other_activate)
            t.start()
            t.join(timeout=5)
        assert err_box, "concurrent activate should fail under held lease"
        assert isinstance(err_box[0], BusyError)
    finally:
        fk.set_fingerprint_key_override(None)
