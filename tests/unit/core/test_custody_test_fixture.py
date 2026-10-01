"""Test fixture enrollment does not grant implicit application admission."""

from __future__ import annotations

import pytest

from magicite.config import Config
from magicite.core import registry, trust, writer_guard
from magicite.core.trust_custodian import CustodianError
from tests.support.custody_adapter import review_sources


def test_enrollment_requires_explicit_allowlisted_review(cfg, db_conn, embedder, review_fixture_artifacts):
    outcome = registry.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert outcome.ingested == 7
    assert all(row.decision == "pending" for row in trust.list_decisions(cfg))
    reviewed = review_fixture_artifacts("proton-ge-proton-downgrade")
    assert len(reviewed) == 1
    snapshot = trust.authenticated_snapshot(cfg)
    assert sum(row["decision"] == "admit" for row in snapshot.latest_by_engram.values()) == 1


def test_fixture_provider_does_not_enroll_other_project(cfg, tmp_path):
    with pytest.raises(CustodianError, match="enrollment required"):
        writer_guard.resolve_custody(Config(project_root=tmp_path / "unprovisioned"))


def test_review_helper_rejects_empty_or_changed_source(cfg, db_conn, embedder):
    registry.register(cfg, db_conn, embedder, path=".magicite/engrams")
    with pytest.raises(ValueError, match="allowlist"):
        review_sources(cfg, db_conn, sources={})
    from tests.conftest import TOY_ENGRAMS_DIR

    name = "proton-ge-proton-downgrade"
    raw = (
        (TOY_ENGRAMS_DIR / (name + ".egr.md"))
        .read_bytes()
        .replace(b"Identify the game's", b"Alter the game's")
    )
    with pytest.raises(ValueError, match="allowlisted"):
        review_sources(cfg, db_conn, sources={name: raw})
    assert all(
        row["decision"] == "pending" for row in trust.authenticated_snapshot(cfg).latest_by_engram.values()
    )
