"""AC-TH-08/10 marker transformation preserves bytes and grants nothing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from magicite.core.trust_artifacts import mark_artifact, require_enrollment_marker
from magicite.core.trust_custodian import CustodianError
from magicite.engram.parser import EngramParseError, parse_artifact, split_frontmatter

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/engram-v1/positive/sample-host-tooling.egr.md"


def test_marker_transform_preserves_body_and_source_signature_is_not_target_signature():
    source = FIXTURE.read_bytes()
    result = mark_artifact(source, registry_id="registry-one", relpath="sample.egr.md", actor="operator")
    assert split_frontmatter(result.target.decode())[1] == split_frontmatter(source.decode())[1]
    assert result.source == source
    assert result.lineage["source_digest"] != result.lineage["target_digest"]
    assert result.lineage["signature_provenance"]["scope"] == "source-only"
    assert result.lineage["signature_provenance"]["target_signature_valid"] is False
    assert result.lineage["grants_admission"] is False
    artifact, _ = parse_artifact(result.target.decode(), relpath="sample.egr.md")
    require_enrollment_marker(artifact, "registry-one")
    with pytest.raises(CustodianError):
        require_enrollment_marker(artifact, "other-registry")


def test_unmarked_active_artifact_is_rejected():
    artifact, _ = parse_artifact(FIXTURE.read_text(), relpath="sample.egr.md")
    with pytest.raises(CustodianError):
        require_enrollment_marker(artifact, "registry-one")


def test_exact_marker_transform_retry_does_not_change_target():
    first = mark_artifact(FIXTURE.read_bytes(), registry_id="r", relpath="sample.egr.md", actor="operator")
    second = mark_artifact(first.target, registry_id="r", relpath="sample.egr.md", actor="operator")
    assert second.target == first.target


def test_unknown_required_extensions_still_fail_closed():
    source = FIXTURE.read_text().replace(
        "extensions: {}", "extensions:\n  unknown.feature:\n    required: true"
    )
    with pytest.raises(EngramParseError):
        mark_artifact(source.encode(), registry_id="r", relpath="sample.egr.md", actor="operator")


def test_transform_record_has_no_admission_effect_and_is_immutable(tmp_path):
    import hashlib

    from magicite.core.trust import TrustDecision, default_policy
    from magicite.core.trust_custodian import CustodianStore, _bytes

    store = CustodianStore.create(tmp_path / "custody")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)
    result = mark_artifact(FIXTURE.read_bytes(), registry_id="r", relpath="sample.egr.md", actor="operator")
    try:
        fence = store.register_fence(
            "r", predecessor=store.read_current("r"), attempt_id="a", holder="lease", local_token=1
        )
        policy = default_policy()
        revoke = TrustDecision(
            decision_id="revoke",
            engram_id=result.lineage["engram_id"],
            content_digest=result.lineage["source_digest"],
            decision="revoke",
            source_channel="bundle_import",
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_digest=policy.digest(),
            actor="operator",
            timestamp="2026-09-30T00:00:00Z",
        ).to_dict()
        for identity, kind, payload in [
            ("revoke", "trust_decision", revoke),
            (
                "transform-" + hashlib.sha256(_bytes(result.lineage)).hexdigest(),
                "artifact_transform",
                result.lineage,
            ),
        ]:
            head = store.read_current("r")
            prepared = store.prepare_record(
                "r", fence=fence, expected_head=head, record_id=identity, kind=kind, payload=payload
            )
            store.commit_record("r", fence=fence, expected_head=head, record=prepared)
        decisions = [r["payload"] for r in store.committed_records("r") if r["kind"] == "trust_decision"]
        assert decisions == [revoke]
        changed = dict(result.lineage, grants_admission=True)
        with pytest.raises(CustodianError):
            store.prepare_record(
                "r",
                fence=fence,
                expected_head=store.read_current("r"),
                record_id=identity,
                kind="artifact_transform",
                payload=changed,
            )
        with pytest.raises(CustodianError):
            store.prepare_record(
                "r",
                fence=fence,
                expected_head=store.read_current("r"),
                record_id="new-wrapper",
                kind="artifact_transform",
                payload=result.lineage,
            )
    finally:
        store.close()


@pytest.mark.parametrize("ending", ["lf", "crlf", "mixed"])
def test_authenticated_loader_preserves_exact_enrolled_newlines(cfg, db_conn, ending):
    from magicite.core import trust_artifacts, writer_guard
    from magicite.engram.digests import routing_body_digest

    source = FIXTURE.read_bytes()
    if ending == "crlf":
        source = source.replace(b"\n", b"\r\n")
    elif ending == "mixed":
        yaml, body = split_frontmatter(source.decode())
        lines = body.split("\n")
        body = lines[0] + "\r\n" + lines[1] + "\r" + "\n".join(lines[2:])
        source = ("---\n" + yaml + "\n---\n" + body).encode()
    target = cfg.registry_dir / "enrolled-newlines.egr.md"
    with writer_guard.registry_writer_lease(cfg, db_conn).acquire():
        trust_artifacts.publish_new_artifact(cfg, target, source, actor="explicit-newline-test")
    raw = target.read_bytes()
    artifact = trust_artifacts.require_bound_artifact(cfg, target)
    body = split_frontmatter(raw.decode())[1]
    assert artifact.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.body_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert artifact.frontmatter.routing.body_digest == routing_body_digest(body)
    snapshot = writer_guard.journal_for(cfg).snapshot()
    assert any(
        record["kind"] == "artifact_transform"
        and record["payload"]["target_digest"] == artifact.content_sha256
        for record in snapshot.records
    )
    assert target.read_bytes() == raw


def test_authenticated_loader_rejects_newline_only_tamper(cfg, db_conn):
    from magicite.core import trust_artifacts, writer_guard

    target = cfg.registry_dir / "tampered-newlines.egr.md"
    with writer_guard.registry_writer_lease(cfg, db_conn).acquire():
        trust_artifacts.publish_new_artifact(cfg, target, FIXTURE.read_bytes(), actor="explicit-newline-test")
    before = trust_artifacts.require_bound_artifact(cfg, target)
    tampered = target.read_bytes().replace(b"\n", b"\r\n")
    target.write_bytes(tampered)
    after, _ = trust_artifacts.load_registry_artifact(cfg, target)
    assert after.id == before.id
    assert after.frontmatter.routing.body_digest == before.frontmatter.routing.body_digest
    with pytest.raises(CustodianError, match="authenticated transformed content identity"):
        trust_artifacts.require_bound_artifact(cfg, target)
    assert target.read_bytes() == tampered


def test_registry_loader_rejects_invalid_utf8_without_rewrite(cfg):
    from magicite.core.trust_artifacts import load_registry_artifact

    target = cfg.registry_dir / "invalid-utf8.egr.md"
    raw = b"---\ninvalid: \xff\n---\nbody\n"
    target.write_bytes(raw)
    with pytest.raises(UnicodeDecodeError):
        load_registry_artifact(cfg, target)
    assert target.read_bytes() == raw
