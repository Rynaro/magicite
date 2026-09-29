"""AC-S02-* — Engram 1.0 schema, lossless transform, assets, relations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from magicite.engram import assets as assets_mod
from magicite.engram import digests as digests_mod
from magicite.engram import parser as parser_mod
from magicite.engram import schema_validate as schema_mod
from magicite.engram import transform as transform_mod
from magicite.engram import writer as writer_mod
from magicite.engram.model_v1 import (
    AssetDescriptor,
    Capabilities,
    EngramRevisionRef,
    ProducedCapability,
    Relations,
    RequiredCapability,
    VersionConstraint,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "engram-v1"


@pytest.fixture(autouse=True)
def _clear_schema_cache() -> None:
    schema_mod.clear_schema_cache()


def test_identity_preserved(toy_registry_dir: Path) -> None:
    """AC-S02-02: pure 0.2→1.0 transform twice keeps the stable id."""
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    parsed = parser_mod.parse_file(path, registry_root=toy_registry_dir)
    original_id = parsed.engram.id

    revision_map = {original_id: parsed.engram.frontmatter.version}
    first = transform_mod.transform_0_2_to_1_0(parsed.engram, revision_map=revision_map)
    second = transform_mod.transform_0_2_to_1_0(parsed.engram, revision_map=revision_map)

    assert first.engram.id == original_id
    assert second.engram.id == original_id
    assert first.engram.frontmatter.id == second.engram.frontmatter.id == original_id
    assert first.engram.frontmatter.spec == "engram/1.0"
    assert first.engram.frontmatter.model_dump(
        include={"id", "name", "version", "intent", "routing"}
    ) == second.engram.frontmatter.model_dump(include={"id", "name", "version", "intent", "routing"})


def test_required_unknown_rejected() -> None:
    """AC-S02-03: unknown mandatory extension or invalid version range fail closed."""
    unknown_path = FIXTURES / "negative" / "unknown-required-extension.egr.md"
    artifact, _doc = parser_mod.parse_artifact(
        unknown_path.read_text(encoding="utf-8"),
        relpath="negative/unknown-required-extension.egr.md",
    )
    result = schema_mod.validate_frontmatter_v1(artifact.frontmatter)
    assert not result.ok
    assert any("unknown required extension" in e for e in result.errors)

    invalid_range = FIXTURES / "negative" / "invalid-semver-range.egr.md"
    # Structural parse succeeds; admission rejects caret ranges (parse vs admit).
    bad, _ = parser_mod.parse_artifact(
        invalid_range.read_text(encoding="utf-8"),
        relpath="negative/invalid-semver-range.egr.md",
        admit=False,
    )
    assert bad.frontmatter.compatibility is not None
    with pytest.raises(parser_mod.EngramParseError, match="admission failed"):
        parser_mod.load_artifact(
            invalid_range.read_text(encoding="utf-8"),
            relpath="negative/invalid-semver-range.egr.md",
        )


def test_asset_containment_and_digest(tmp_path: Path) -> None:
    """AC-S02-04: traversal, digest mismatch, symlink escape, duplicates reject on load."""
    registry = tmp_path / "registry"
    good_src = FIXTURES / "assets" / "good" / "helper.sh"
    asset_dir = registry / "assets" / "good"
    asset_dir.mkdir(parents=True)
    target = asset_dir / "helper.sh"
    shutil.copyfile(good_src, target)
    raw = target.read_bytes()
    digest = digests_mod.asset_bytes_digest(raw)

    ok_assets = {
        "assets/good/helper.sh": AssetDescriptor(sha256=digest, size=len(raw), media_type="application/x-sh")
    }
    assert assets_mod.validate_assets(ok_assets, registry_root=registry) == []

    for fixture_name in ("asset-traversal.json", "asset-absolute.json", "asset-backslash.json"):
        data = json.loads((FIXTURES / "negative" / fixture_name).read_text(encoding="utf-8"))
        result = schema_mod.validate_frontmatter_dict(data, spec="engram/1.0")
        assert not result.ok, fixture_name
        assert any("assets." in e for e in result.errors), (fixture_name, result.errors)

    with pytest.raises(assets_mod.AssetValidationError, match="traversal|absolute"):
        assets_mod.assert_assets_valid(
            {"../escape.sh": AssetDescriptor(sha256=digest, size=len(raw))},
            registry_root=registry,
            require_files=False,
        )

    target.write_bytes(raw + b"\n# tampered\n")
    issues = assets_mod.validate_assets(ok_assets, registry_root=registry)
    assert any("digest mismatch" in i.reason for i in issues)
    target.write_bytes(raw)

    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = asset_dir / "escape-link.sh"
    link.symlink_to(outside)
    symlink_issues = assets_mod.validate_assets(
        {
            "assets/good/escape-link.sh": AssetDescriptor(
                sha256=digests_mod.asset_bytes_digest(outside.read_bytes()),
                size=outside.stat().st_size,
            )
        },
        registry_root=registry,
    )
    assert any("symlink" in i.reason for i in symlink_issues)

    case_issues = assets_mod.validate_assets(
        {
            "assets/good/helper.sh": AssetDescriptor(sha256=digest, size=len(raw)),
            "assets/good/Helper.sh": AssetDescriptor(sha256=digest, size=len(raw)),
        },
        registry_root=registry,
        require_files=False,
    )
    assert any("case-insensitive duplicate" in i.reason for i in case_issues)

    # `.` segments normalize away → duplicate normalized path
    norm_issues = assets_mod.validate_assets(
        {
            "assets/good/helper.sh": AssetDescriptor(sha256=digest, size=len(raw)),
            "assets/good/./helper.sh": AssetDescriptor(sha256=digest, size=len(raw)),
        },
        registry_root=registry,
        require_files=False,
    )
    assert any("duplicate normalized path" in i.reason for i in norm_issues)

    inner_link = asset_dir / "helper-via-link.sh"
    if inner_link.exists() or inner_link.is_symlink():
        inner_link.unlink()
    inner_link.symlink_to(target.resolve())
    same_issues = assets_mod.validate_assets(
        {
            "assets/good/helper.sh": AssetDescriptor(sha256=digest, size=len(raw)),
            "assets/good/helper-via-link.sh": AssetDescriptor(sha256=digest, size=len(raw)),
        },
        registry_root=registry,
    )
    assert any("duplicate resolve target" in i.reason for i in same_issues), same_issues

    body = "## Procedure\n1. x.\n\n## Pitfalls\n\n## Examples\n\n## Provenance\n"
    digest_body = digests_mod.routing_body_digest(body)
    bad_doc = (
        "---\n"
        "spec: engram/1.0\n"
        "name: loaded-bad-asset\n"
        "id: egr_bad10001\n"
        "version: 1\n"
        "intent:\n  does: x\n  use_when: y\n  not_when: z\n"
        "routing:\n  positive: [a, b, c]\n  negative: [d]\n"
        f'  body_digest: "{digest_body}"\n'
        "origin:\n  channel: authored\n"
        "assets:\n"
        "  ../escape.sh:\n"
        f'    sha256: "{digest}"\n'
        f"    size: {len(raw)}\n"
        "---\n"
        f"{body}"
    )
    with pytest.raises(parser_mod.EngramParseError, match="admission failed"):
        parser_mod.load_artifact(bad_doc, relpath="x.egr.md", registry_root=registry)


def test_shared_relation_fixtures(toy_registry_dir: Path) -> None:
    """AC-S02-05: host/artifact + exact-revision fixtures match C1; no learned inference."""
    shared = json.loads(
        (FIXTURES / "relations" / "shared-relation-fixtures.json").read_text(encoding="utf-8")
    )

    host_req = RequiredCapability.model_validate(shared["capabilities"]["requires_host"])
    art_req = RequiredCapability.model_validate(shared["capabilities"]["requires_artifact"])
    produced = ProducedCapability.model_validate(shared["capabilities"]["produces_artifact"])
    assert host_req.kind == "host"
    assert art_req.kind == "artifact"
    assert produced.kind == "artifact"
    assert produced.version == "1.0.0"

    relations = Relations.model_validate(shared["relations"])
    assert relations.requires == [EngramRevisionRef(id="egr_b5320dfd", version=1)]
    assert relations.before[0] == EngramRevisionRef(id="egr_aaaa0001", version=2)
    assert relations.supersedes == [EngramRevisionRef(id="egr_bbbb0001", version=4)]

    positive = FIXTURES / "positive" / "sample-host-tooling.egr.md"
    artifact, _ = parser_mod.load_artifact(
        positive.read_text(encoding="utf-8"), relpath="positive/sample-host-tooling.egr.md"
    )
    result = schema_mod.validate_frontmatter_v1(artifact.frontmatter)
    assert result.ok, result.errors
    assert artifact.frontmatter.capabilities is not None
    assert artifact.frontmatter.relations is not None
    assert artifact.frontmatter.capabilities.requires[0].kind == "host"
    assert artifact.frontmatter.relations.requires[0].id == "egr_b5320dfd"

    # Cross-check: positive egr.md relations == shared JSON relations
    assert artifact.frontmatter.relations.model_dump(mode="json") == shared["relations"]

    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    legacy = parser_mod.parse_file(path, registry_root=toy_registry_dir).engram
    from magicite.engram.model import Synapse

    legacy.frontmatter.synapses.append(
        Synapse(
            target="egr_dead0001",
            type="co_activation",
            provenance="learned",
            storage_strength=0.9,
            evidence_count=3,
            first_observed="2026-01-01T00:00:00Z",
        )
    )
    transformed = transform_mod.transform_0_2_to_1_0(legacy)
    assert transformed.engram.frontmatter.relations is not None
    assert transformed.engram.frontmatter.relations.before == []
    assert transformed.engram.frontmatter.relations.supersedes == []
    assert any(d.code == "learned_edge_ignored" for d in transformed.diagnostics)
    assert transformed.engram.frontmatter.legacy is not None
    assert "needs" in transformed.engram.frontmatter.legacy
    assert transformed.engram.frontmatter.capabilities == Capabilities()


def test_routing_body_digest_is_lf_normalized_and_distinct_from_ids() -> None:
    from magicite.engram import ids

    crlf = "## Procedure\r\n1. Step.\r\n"
    lf = "## Procedure\n1. Step.\n"
    assert digests_mod.routing_body_digest(crlf) == digests_mod.routing_body_digest(lf)
    assert digests_mod.routing_body_digest(lf) != ids.body_sha256(crlf)


def test_v1_roundtrip_render_preserves_procedure_raw() -> None:
    body = "## Procedure\n1. Numbered step.\nUnmatched prose retained verbatim.\n\n```bash\necho hi\n```\n"
    digest = digests_mod.routing_body_digest(body)
    text = (
        "---\n"
        "spec: engram/1.0\n"
        "name: freeform-v1\n"
        "id: egr_f00f00f0\n"
        "version: 1\n"
        "intent:\n"
        "  does: does freeform\n"
        "  use_when: when prose matters\n"
        "  not_when: never\n"
        "routing:\n"
        "  positive: [a, b, c]\n"
        "  negative: [d]\n"
        f'  body_digest: "{digest}"\n'
        "origin:\n"
        "  channel: authored\n"
        "---\n"
        f"{body}"
    )
    artifact, doc = parser_mod.load_artifact(text, relpath="freeform-v1.egr.md")
    assert "Unmatched prose" in artifact.body.procedure_raw
    assert artifact.body.exec_blocks[0].language == "bash"
    rendered = writer_mod.render_document_v1(artifact, doc)
    again, _ = parser_mod.load_artifact(rendered, relpath="freeform-v1.egr.md")
    assert again.body.procedure_raw == artifact.body.procedure_raw
    assert again.body.exec_blocks[0].text == artifact.body.exec_blocks[0].text
    assert again.id == artifact.id


def test_render_as_refuses_v1_to_v02_downgrade() -> None:
    positive = FIXTURES / "positive" / "sample-host-tooling.egr.md"
    artifact, _ = parser_mod.load_artifact(
        positive.read_text(encoding="utf-8"), relpath="positive/sample-host-tooling.egr.md"
    )
    with pytest.raises(ValueError, match="S03"):
        writer_mod.render_as(artifact, target_format="engram/0.2")


def test_metadata_and_asset_digests_are_stable() -> None:
    payload = {"id": "egr_a1b2c3d4", "name": "x", "signature": "drop-me", "version": 1}
    d1 = digests_mod.metadata_digest(payload)
    d2 = digests_mod.metadata_digest({"version": 1, "name": "x", "id": "egr_a1b2c3d4", "signature": "other"})
    assert d1 == d2
    assert digests_mod.asset_bytes_digest(b"abc") == digests_mod.sha256_hex(b"abc")


def test_declared_depends_on_pins_to_relations_requires(toy_registry_dir: Path) -> None:
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    engram = parser_mod.parse_file(path, registry_root=toy_registry_dir).engram
    from magicite.engram.model import Synapse

    engram.frontmatter.synapses.append(
        Synapse(
            target="egr_abcdef01",
            type="depends_on",
            provenance="declared",
            storage_strength=0.0,
            evidence_count=0,
            first_observed="2026-01-01T00:00:00Z",
        )
    )
    unpinned = transform_mod.transform_0_2_to_1_0(engram)
    assert not unpinned.ok_for_composition
    assert any(d.code == "depends_on_unpinned" for d in unpinned.diagnostics)

    pinned = transform_mod.transform_0_2_to_1_0(engram, revision_map={"egr_abcdef01": 7})
    assert pinned.ok_for_composition
    assert pinned.engram.frontmatter.relations is not None
    assert pinned.engram.frontmatter.relations.requires == [EngramRevisionRef(id="egr_abcdef01", version=7)]


def test_version_constraint_rejects_wildcard_and_or() -> None:
    from magicite.engram.version_constraints import VersionConstraintError, validate_version_constraint

    with pytest.raises(VersionConstraintError):
        validate_version_constraint(VersionConstraint(scheme="semver", range="1.x"))
    with pytest.raises(VersionConstraintError):
        validate_version_constraint(VersionConstraint(scheme="semver", range=">=1.0.0 || <2.0.0"))
    validate_version_constraint(VersionConstraint(scheme="semver", range=">=1.0.0,<2.0.0"))
    validate_version_constraint(VersionConstraint(scheme="pep440", range=">=3.11,<4"))


def test_render_frontmatter_v1_is_pure_for_body_digest() -> None:
    """Render must not mutate the input model; emitted YAML carries the digest."""
    positive = FIXTURES / "positive" / "sample-host-tooling.egr.md"
    artifact, _ = parser_mod.parse_artifact(
        positive.read_text(encoding="utf-8"),
        relpath="positive/sample-host-tooling.egr.md",
        admit=False,
    )
    stale = "0" * 64
    artifact.frontmatter.routing.body_digest = stale

    rendered = writer_mod.render_document_v1(artifact)
    assert artifact.frontmatter.routing.body_digest == stale

    _yaml, body_text = parser_mod.split_frontmatter(rendered)
    expected = digests_mod.routing_body_digest(body_text)
    again, _ = parser_mod.parse_artifact(rendered, relpath="positive/sample-host-tooling.egr.md")
    assert again.frontmatter.routing.body_digest == expected
    assert again.frontmatter.routing.body_digest != stale


def test_validate_frontmatter_rejects_traversal_asset_key() -> None:
    """BLOCKER: ../escape.sh must fail validate_frontmatter_v1 / dict."""
    fm = {
        "spec": "engram/1.0",
        "name": "escape-test",
        "id": "egr_esc00001",
        "version": 1,
        "intent": {"does": "x", "use_when": "y", "not_when": "z"},
        "routing": {
            "positive": ["a", "b", "c"],
            "negative": ["d"],
            "body_digest": "0" * 64,
        },
        "origin": {"channel": "authored"},
        "assets": {
            "../escape.sh": {"sha256": "0" * 64, "size": 0},
        },
    }
    result = schema_mod.validate_frontmatter_dict(fm, spec="engram/1.0")
    assert not result.ok
    assert any(".." in e or "assets" in e for e in result.errors)
