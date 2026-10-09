"""AC-S07-02: calibrated abstention obeys the frozen threshold manifest."""

from __future__ import annotations

from magicite.core import calibration as cal_mod
from magicite.core import fingerprint_key as fk
from magicite.core import routing_policy as policy_mod


def test_threshold_manifest(cfg) -> None:
    """GIVEN an unrelated query in the locked rejection set
    WHEN calibrated routing runs
    THEN the result SHALL obey the frozen abstention decision rule.
    """
    cfg.ensure_dirs()
    fk.set_fingerprint_key_override(b"\x11" * fk.KEY_BYTES)
    try:
        policy_id = policy_mod.POLICY_DENSE_V1
        policy_digest = policy_mod.compute_policy_digest(policy_id, cfg)
        config_digest = policy_mod.compute_config_digest(cfg)

        # Fit ONLY on the calibration split (relevant + locked no-match).
        key = fk.load_or_create_fingerprint_key(cfg)
        relevant_fp = fk.query_fingerprint("how to rollback proton", key=key)
        reject_query = cal_mod.DEFAULT_REJECTION_QUERIES[0]
        reject_fp = fk.query_fingerprint(reject_query, key=key)
        unrelated = "completely unrelated astronomy question about nebulae"
        unrelated_fp = fk.query_fingerprint(unrelated, key=key)

        examples = [
            cal_mod.CalibrationExample(
                query_fingerprint=relevant_fp,
                top_score=0.91,
                margin=0.40,
                label_relevant=True,
            ),
            cal_mod.CalibrationExample(
                query_fingerprint=reject_fp,
                top_score=0.55,
                margin=0.02,
                label_relevant=False,
            ),
            cal_mod.CalibrationExample(
                query_fingerprint=unrelated_fp,
                top_score=0.52,
                margin=0.01,
                label_relevant=False,
            ),
        ]
        artifact = cal_mod.fit_abstention(
            examples,
            cfg=cfg,
            policy_id=policy_id,
            policy_digest=policy_digest,
            config_digest=config_digest,
            rejection_queries=(reject_query, unrelated),
        )
        assert artifact.rule_id == cal_mod.FROZEN_ABSTENTION_RULE
        assert artifact.split == "calibration"
        assert reject_fp in artifact.rejection_query_fingerprints
        assert unrelated_fp in artifact.rejection_query_fingerprints
        # Provenance must never embed raw query text.
        provenance_blob = str(artifact.data_provenance)
        assert unrelated not in provenance_blob
        assert reject_query not in provenance_blob
        cal_mod.save_calibration(cfg, artifact)

        loaded = cal_mod.load_calibration(
            cfg,
            expected_policy_digest=policy_digest,
            expected_config_digest=config_digest,
        )
        assert loaded is not None
        assert loaded.digest == artifact.digest

        # Locked rejection-set query must abstain under the frozen rule.
        decision = cal_mod.decide_abstention(
            query_fingerprint=unrelated_fp,
            top_score=0.99,  # even with a strong raw score…
            margin=0.50,
            artifact=loaded,
            expected_policy_digest=policy_digest,
            expected_config_digest=config_digest,
        )
        assert decision.abstain is True
        assert "locked_rejection_set" in decision.reason_codes
        assert decision.calibrated is True
        assert decision.confidence_value is None

        # Compatible relevant query above thresholds may select.
        ok = cal_mod.decide_abstention(
            query_fingerprint=relevant_fp,
            top_score=0.95,
            margin=0.45,
            artifact=loaded,
            expected_policy_digest=policy_digest,
            expected_config_digest=config_digest,
        )
        assert ok.abstain is False
        assert ok.calibrated is True
        assert ok.confidence_value is None

        # Incompatible/stale calibration must clear — never stale probabilities.
        stale = cal_mod.decide_abstention(
            query_fingerprint=relevant_fp,
            top_score=0.95,
            margin=0.45,
            artifact=loaded,
            expected_policy_digest="0" * 64,
            expected_config_digest=config_digest,
        )
        assert stale.calibrated is False
        assert stale.confidence_value is None
        assert "uncalibrated" in stale.reason_codes
    finally:
        fk.set_fingerprint_key_override(None)


def test_pav_top1_correctness_and_ties():
    model, reason = cal_mod.fit_probability(
        [-3.0, -3.0, 2.0, 5.0], [True, False, False, True], calibration_input_digest="a" * 64, rank_depth=5
    )
    assert reason is None and model is not None
    assert model["x"] == [-3.0, 2.0, 5.0]
    assert model["y"] == [1 / 3, 1 / 3, 1.0]
    assert cal_mod.probability_at_margin(model, 3.5) == 2 / 3
    assert cal_mod.probability_at_margin(model, -100) == 1 / 3
    assert cal_mod.probability_at_margin(model, 100) == 1
    assert cal_mod.fit_probability([1.0], [True], calibration_input_digest="a" * 64, rank_depth=5)[0] is None


def test_v2_digest_legacy_compatibility_and_depth(cfg):
    import pytest

    from magicite.errors import InvalidInputError

    base = cal_mod.fit_abstention(
        [
            cal_mod.CalibrationExample("no", 0.1, 0.1, False),
            cal_mod.CalibrationExample("yes", 0.9, 0.8, True),
        ],
        cfg=cfg,
        policy_id="dense-v1",
        policy_digest="b" * 64,
        config_digest="c" * 64,
    )
    legacy = base.to_dict()
    assert "probability_model" not in legacy
    assert cal_mod.CalibrationArtifact.from_dict(legacy).to_dict() == legacy
    model, _ = cal_mod.fit_probability(
        [0.1, 0.8], [False, True], calibration_input_digest="a" * 64, rank_depth=5
    )
    fitted = cal_mod.with_probability(base, model)
    assert cal_mod.CalibrationArtifact.from_dict(fitted.to_dict()).digest == fitted.digest
    kwargs = dict(query_fingerprint="yes", top_score=0.9, margin=0.8, artifact=fitted)
    assert cal_mod.decide_abstention(**kwargs, rank_depth=5).confidence_value == 1
    assert cal_mod.decide_abstention(**kwargs, rank_depth=1).confidence_value is None
    assert cal_mod.decide_abstention(**{**kwargs, "top_score": 0.05}, rank_depth=5).confidence_value is None
    tampered = fitted.to_dict()
    tampered["probability_model"] = {**model, "y": [0.0, 0.7]}
    with pytest.raises(InvalidInputError):
        cal_mod.CalibrationArtifact.from_dict(tampered)
    downgraded = fitted.to_dict()
    downgraded["schema_version"] = "CalibrationArtifact/1"
    with pytest.raises(InvalidInputError):
        cal_mod.CalibrationArtifact.from_dict(downgraded)
