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


def test_identity_preserved(toy_registry_dir: Path) -> None:
    """AC-S02-02: pure 0.2→1.0 transform twice keeps the stable id."""
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    parsed = parser_mod.parse_file(path, registry_root=toy_registry_dir)
    original_id = parsed.engram.id

    revision_map = {original_id: parsed.engram.frontmatter.version}
    # Pin a declared depends_on target if present via needs-only; synapses may be empty.
    first = transform_mod.transform_0_2_to_1_0(parsed.engram, revision_map=revision_map)
    second = transform_mod.transform_0_2_to_1_0(parsed.engram, revision_map=revision_map)

    assert first.engram.id == original_id
    assert second.engram.id == original_id
    assert first.engram.frontmatter.id == second.engram.frontmatter.id == original_id
    assert first.engram.frontmatter.spec == "engram/1.0"
    # Idempotent on identity fields across two runs.
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
    bad, _ = parser_mod.parse_artifact(
        invalid_range.read_text(encoding="utf-8"),
        relpath="negative/invalid-semver-range.egr.md",
    )
    range_result = schema_mod.validate_frontmatter_v1(bad.frontmatter)
    assert not range_result.ok
    assert any("semver" in e.lower() or "^" in e for e in range_result.errors)


def test_asset_containment_and_digest(tmp_path: Path) -> None:
    """AC-S02-04: traversal and changed bytes reject; matching digest passes."""
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

    with pytest.raises(assets_mod.AssetValidationError, match="traversal|absolute"):
        assets_mod.assert_assets_valid(
            {"../escape.sh": AssetDescriptor(sha256=digest, size=len(raw))},
            registry_root=registry,
            require_files=False,
        )

    with pytest.raises(assets_mod.AssetValidationError, match="absolute"):
        assets_mod.assert_assets_valid(
            {"/etc/passwd": AssetDescriptor(sha256=digest, size=len(raw))},
            registry_root=registry,
            require_files=False,
        )

    target.write_bytes(raw + b"\n# tampered\n")
    issues = assets_mod.validate_assets(ok_assets, registry_root=registry)
    assert any("digest mismatch" in i.reason for i in issues)


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

    # Schema-validate the positive fixture that embeds these shapes.
    positive = FIXTURES / "positive" / "sample-host-tooling.egr.md"
    artifact, _ = parser_mod.parse_artifact(
        positive.read_text(encoding="utf-8"), relpath="positive/sample-host-tooling.egr.md"
    )
    result = schema_mod.validate_frontmatter_v1(artifact.frontmatter)
    assert result.ok, result.errors
    assert artifact.frontmatter.capabilities is not None
    assert artifact.frontmatter.relations is not None
    assert artifact.frontmatter.capabilities.requires[0].kind == "host"
    assert artifact.frontmatter.relations.requires[0].id == "egr_b5320dfd"

    # Legacy transform must not invent before/supersedes from learned edges.
    path = toy_registry_dir / "engrams" / "proton-ge-proton-downgrade.egr.md"
    legacy = parser_mod.parse_file(path, registry_root=toy_registry_dir).engram
    # Inject a learned co_activation synapse onto a copy.
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
    # needs stay in legacy namespace, never typed capabilities.
    assert transformed.engram.frontmatter.legacy is not None
    assert "needs" in transformed.engram.frontmatter.legacy
    assert transformed.engram.frontmatter.capabilities == Capabilities()


def test_v1_roundtrip_render_preserves_procedure_raw() -> None:
    """Body procedure_raw and fences survive 1.0 render → parse_artifact."""
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
        "  body_digest: "
        '"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"\n'
        "origin:\n"
        "  channel: authored\n"
        "---\n"
        "## Procedure\n"
        "1. Numbered step.\n"
        "Unmatched prose retained verbatim.\n"
        "\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
    )
    artifact, doc = parser_mod.parse_artifact(text, relpath="freeform-v1.egr.md")
    assert "Unmatched prose" in artifact.body.procedure_raw
    assert artifact.body.exec_blocks[0].language == "bash"
    rendered = writer_mod.render_document_v1(artifact, doc)
    again, _ = parser_mod.parse_artifact(rendered, relpath="freeform-v1.egr.md")
    assert again.body.procedure_raw == artifact.body.procedure_raw
    assert again.body.exec_blocks[0].text == artifact.body.exec_blocks[0].text
    assert again.id == artifact.id


def test_render_as_refuses_v1_to_v02_downgrade() -> None:
    positive = FIXTURES / "positive" / "sample-host-tooling.egr.md"
    artifact, _ = parser_mod.parse_artifact(
        positive.read_text(encoding="utf-8"), relpath="positive/sample-host-tooling.egr.md"
    )
    with pytest.raises(ValueError, match="S03"):
        writer_mod.render_as(artifact, target_format="engram/0.2")


def test_metadata_and_asset_digests_are_stable() -> None:
    payload = {"id": "egr_a1b2c3d4", "name": "x", "signature": "drop-me", "version": 1}
    d1 = digests_mod.metadata_digest(payload)
    d2 = digests_mod.metadata_digest({"version": 1, "name": "x", "id": "egr_a1b2c3d4", "signature": "other"})
    assert d1 == d2  # signature stripped; key order irrelevant
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
