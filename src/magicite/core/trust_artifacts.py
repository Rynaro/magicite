"""Pure, non-authorizing artifact transform for protected enrollment.

Original bytes remain caller-owned source provenance. Marker insertion changes
content identity and never carries a publisher signature or admission forward.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from magicite.core.trust_custodian import CustodianError, _bytes
from magicite.engram import parser, writer
from magicite.engram.digests import routing_body_digest
from magicite.engram.model import Engram
from magicite.engram.model_v1 import EngramV1
from magicite.engram.transform import transform_0_2_to_1_0

EXTENSION = "magicite.trust_journal"
TRANSFORM = "magicite-enrollment-marker/1"


@dataclass(frozen=True)
class MarkedArtifact:
    source: bytes
    target: bytes
    lineage: dict[str, Any]


def require_enrollment_marker(artifact: Any, registry_id: str) -> None:
    if not isinstance(artifact, EngramV1):
        raise CustodianError("active legacy artifact requires reviewed enrollment migration")
    extension = artifact.frontmatter.extensions.get(EXTENSION)
    if extension is None or extension.required is not True:
        raise CustodianError("active artifact lacks required enrollment marker")
    value = extension.model_dump(mode="json")
    if value != {"required": True, "registry_id": registry_id, "journal_version": "trust-journal/1"}:
        raise CustodianError("artifact enrollment marker mismatch")


def mark_artifact(
    source: bytes,
    *,
    registry_id: str,
    relpath: str,
    actor: str,
    revision_map: dict[str, int] | None = None,
    source_decision_ids: tuple[str, ...] = (),
    source_signature: dict[str, Any] | None = None,
) -> MarkedArtifact:
    if not registry_id or not actor:
        raise CustodianError("explicit enrollment and reviewer required")
    text = source.decode("utf-8")
    _, body = parser.split_frontmatter(text)
    artifact, doc = parser.parse_artifact(text, relpath=relpath, admit=True)
    original_format = artifact.frontmatter.spec
    if isinstance(artifact, Engram):
        result = transform_0_2_to_1_0(artifact, revision_map=revision_map)
        if not result.ok_for_composition:
            raise CustodianError("legacy transform requires reviewed relation reconciliation")
        artifact = result.engram
        doc = writer._fresh_doc_v1(artifact)
    if not isinstance(artifact, EngramV1):
        raise CustodianError("unsupported enrollment artifact")
    marker = {"required": True, "registry_id": registry_id, "journal_version": "trust-journal/1"}
    existing = doc.get("extensions", {}).get(EXTENSION)
    if existing is not None and dict(existing) != marker:
        raise CustodianError("artifact belongs to a different enrollment")
    if existing is not None and original_format == "engram/1.0":
        target = source
    else:
        if "extensions" not in doc:
            doc["extensions"] = {}
        doc["extensions"][EXTENSION] = marker
        # Preserve the exact source body, including raw SKILL prose. The pure
        # legacy transform supplies metadata only; never re-render that body.
        doc["routing"]["body_digest"] = routing_body_digest(body)
        target = ("---\n" + writer._dump_frontmatter_doc(doc) + "\n---\n" + body).encode("utf-8")
    transformed, _ = parser.parse_artifact(target.decode("utf-8"), relpath=relpath, admit=True)
    require_enrollment_marker(transformed, registry_id)
    if parser.split_frontmatter(target.decode())[1] != body:
        raise CustodianError("enrollment transform changed source body")
    config = {
        "registry_id": registry_id,
        "source_format": original_format,
        "revision_map": revision_map or {},
    }
    assets = {key: value.model_dump(mode="json") for key, value in artifact.frontmatter.assets.items()}
    lineage = {
        "schema": "ArtifactTransform/1",
        "engram_id": artifact.id,
        "source_digest": hashlib.sha256(source).hexdigest(),
        "target_digest": hashlib.sha256(target).hexdigest(),
        "transform_id": TRANSFORM,
        "configuration": config,
        "configuration_digest": hashlib.sha256(_bytes(config)).hexdigest(),
        "resources": assets,
        "source_decision_ids": list(source_decision_ids),
        "signature_provenance": {
            "scope": "source-only",
            "source": source_signature,
            "target_signature_valid": False,
        },
        "review": {"actor": actor, "status": "requires-target-review"},
        "grants_admission": False,
    }
    return MarkedArtifact(source, target, lineage)


def validate_transform_lineage(payload: dict[str, Any]) -> None:
    """Reject authorization claims or incomplete typed lineage before signing."""
    import re

    from magicite.engram.model_v1 import AssetDescriptor

    expected = {
        "schema",
        "engram_id",
        "source_digest",
        "target_digest",
        "transform_id",
        "configuration",
        "configuration_digest",
        "resources",
        "source_decision_ids",
        "signature_provenance",
        "review",
        "grants_admission",
    }
    if set(payload) - {"conversion_provenance"} != expected or payload["schema"] != "ArtifactTransform/1":
        raise CustodianError("invalid artifact transform schema")
    if (
        payload["transform_id"]
        not in {TRANSFORM, "magicite-authored-edit/1", "magicite-dream-checkpoint/1", "magicite-archive/1"}
        or payload["grants_admission"] is not False
    ):
        raise CustodianError("artifact transformation cannot grant admission")
    if not isinstance(payload["engram_id"], str) or not payload["engram_id"]:
        raise CustodianError("transform identity required")
    for field in ("source_digest", "target_digest", "configuration_digest"):
        if not isinstance(payload[field], str) or not re.fullmatch("[0-9a-f]{64}", payload[field]):
            raise CustodianError("invalid transform digest")
    conversion = payload.get("conversion_provenance")
    if conversion is not None:
        if (
            not isinstance(conversion, dict)
            or set(conversion) != {"format", "source_digest", "intermediate_digest"}
            or conversion["format"] != "SKILL.md"
            or not isinstance(conversion["source_digest"], str)
            or re.fullmatch("[0-9a-f]{64}", conversion["source_digest"]) is None
            or conversion["intermediate_digest"] != payload["source_digest"]
        ):
            raise CustodianError("invalid source conversion provenance")
    config = payload["configuration"]
    if (
        not isinstance(config, dict)
        or set(config) != {"registry_id", "source_format", "revision_map"}
        or not isinstance(config["registry_id"], str)
        or not config["registry_id"]
        or config["source_format"] not in {"engram/0.2", "engram/1.0"}
        or not isinstance(config["revision_map"], dict)
        or any(type(value) is not int or value < 1 for value in config["revision_map"].values())
        or hashlib.sha256(_bytes(config)).hexdigest() != payload["configuration_digest"]
    ):
        raise CustodianError("invalid transform configuration commitment")
    provenance = payload["signature_provenance"]
    if (
        not isinstance(provenance, dict)
        or set(provenance) != {"scope", "source", "target_signature_valid"}
        or provenance["scope"] != "source-only"
        or provenance["target_signature_valid"] is not False
    ):
        raise CustodianError("transformed bytes have no inherited publisher signature")
    source_signature = provenance["source"]
    if source_signature is not None:
        if (
            not isinstance(source_signature, dict)
            or set(source_signature) != {"signature_valid", "signer_fingerprint"}
            or type(source_signature["signature_valid"]) is not bool
            or not isinstance(source_signature["signer_fingerprint"], str)
            or re.fullmatch("[0-9a-f]{64}", source_signature["signer_fingerprint"]) is None
        ):
            raise CustodianError("invalid source signature provenance")
    if not isinstance(payload["source_decision_ids"], list) or any(
        not isinstance(value, str) or not value for value in payload["source_decision_ids"]
    ):
        raise CustodianError("invalid source decision provenance")
    review = payload["review"]
    if (
        not isinstance(review, dict)
        or set(review) != {"actor", "status"}
        or not isinstance(review["actor"], str)
        or not review["actor"]
        or review["status"] != "requires-target-review"
    ):
        raise CustodianError("explicit target review remains required")
    if not isinstance(payload["resources"], dict):
        raise CustodianError("invalid transform resource map")
    for path, value in payload["resources"].items():
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            raise CustodianError("invalid transform resource path")
        AssetDescriptor.model_validate(value)


def publish_new_artifact(
    cfg: Any,
    target: Any,
    source: bytes,
    *,
    actor: str,
    source_signature: dict[str, Any] | None = None,
    source_document: bytes | None = None,
) -> None:
    """Publish new intake through non-authorizing, exact-byte journal lineage.

    Existing unmarked identities require explicit reviewed legacy migration.
    This helper never runs from sync or silently retargets an old approval.
    """
    from pathlib import Path

    from magicite.core.trust_journal import _directory_fd, _read_file, _replace_file
    from magicite.core.writer_guard import bound_journal

    journal, held, fence = bound_journal(cfg)
    snapshot = journal.snapshot()
    target = Path(target)
    target.resolve().relative_to(cfg.registry_dir.resolve())
    artifact, _ = parser.parse_artifact(source.decode(), relpath=str(target), admit=True)
    try:
        require_enrollment_marker(artifact, journal.registry_id)
        marked = True
    except CustodianError:
        marked = False
    if artifact.id in snapshot.latest_by_engram and not marked:
        raise CustodianError("existing identity requires reviewed enrollment migration")
    if marked and any(
        record["kind"] == "artifact_transform"
        and record["payload"]["engram_id"] == artifact.id
        and record["payload"]["target_digest"] == hashlib.sha256(source).hexdigest()
        for record in snapshot.records
    ):
        held.assert_owned()
        with _directory_fd(target.parent, create=True) as directory:
            try:
                current = _read_file(directory, target.name)
            except FileNotFoundError:
                current = None
            if current != source:
                _replace_file(directory, target.name, source, held.assert_owned)
        held.assert_owned()
        return
    transformed = mark_artifact(
        source,
        registry_id=journal.registry_id,
        relpath=str(target),
        actor=actor,
        source_signature=source_signature,
    )
    held.assert_owned()
    if source_document is not None:
        transformed.lineage["conversion_provenance"] = {
            "format": "SKILL.md",
            "source_digest": hashlib.sha256(source_document).hexdigest(),
            "intermediate_digest": transformed.lineage["source_digest"],
        }
    for original in (source,) if source_document is None else (source, source_document):
        source_digest = hashlib.sha256(original).hexdigest()
        with _directory_fd(cfg.data_dir / "trust" / "sources", create=True) as directory:
            try:
                prior = _read_file(directory, source_digest)
            except FileNotFoundError:
                _replace_file(directory, source_digest, original, held.assert_owned)
            else:
                if prior != original:
                    raise CustodianError("source archive digest mismatch")
    journal.append(
        record_id="transform-" + hashlib.sha256(_bytes(transformed.lineage)).hexdigest(),
        kind="artifact_transform",
        payload=transformed.lineage,
        fence=fence,
        assert_owned=held.assert_owned,
    )
    with _directory_fd(target.parent, create=True) as directory:
        _replace_file(directory, target.name, transformed.target, held.assert_owned)
    held.assert_owned()


def require_bound_artifact(cfg: Any, path: Any) -> Any:
    """Sync/read guard: marker alone never establishes authenticated lineage."""
    from magicite.core.writer_guard import journal_for

    artifact, _ = parser.load_artifact_file(path, registry_root=cfg.project_root)
    snapshot = journal_for(cfg).snapshot()
    require_enrollment_marker(artifact, snapshot.head["registry_id"])
    if not any(
        record["kind"] == "artifact_transform"
        and record["payload"]["engram_id"] == artifact.id
        and record["payload"]["target_digest"] == artifact.content_sha256
        for record in snapshot.records
    ):
        raise CustodianError("artifact has no authenticated transformed content identity")
    return artifact


def publish_authored_edit(
    cfg: Any,
    target: Any,
    content: bytes,
    *,
    expected_source_digest: str,
    actor: str,
    transform_id: str = "magicite-authored-edit/1",
    destination: Any | None = None,
) -> None:
    """Explicit authored mutation with source CAS and no admission inheritance."""
    from pathlib import Path

    from magicite.core.trust_journal import _directory_fd, _read_file, _replace_file
    from magicite.core.writer_guard import bound_journal

    journal, held, fence = bound_journal(cfg)
    target = Path(target)
    target.resolve().relative_to(cfg.registry_dir.resolve())
    output = target if destination is None else Path(destination)
    if destination is not None:
        if transform_id != "magicite-archive/1":
            raise CustodianError("relocation requires explicit archive transform")
        output.resolve().relative_to(cfg.archive_dir.resolve())
    with _directory_fd(target.parent) as directory:
        source = _read_file(directory, target.name)
    if hashlib.sha256(source).hexdigest() != expected_source_digest:
        raise CustodianError("authored edit source changed")
    original = require_bound_artifact(cfg, target)
    artifact, _ = parser.parse_artifact(content.decode(), relpath=str(target), admit=True)
    require_enrollment_marker(artifact, journal.registry_id)
    if not isinstance(artifact, EngramV1):
        raise CustodianError("authored edit requires v1 artifact")
    if original.id != artifact.id:
        raise CustodianError("authored edit cannot replace artifact identity")
    lineage = mark_artifact(source, registry_id=journal.registry_id, relpath=str(target), actor=actor).lineage
    if transform_id not in {"magicite-authored-edit/1", "magicite-dream-checkpoint/1", "magicite-archive/1"}:
        raise CustodianError("unsupported artifact mutation")
    lineage["transform_id"] = transform_id
    lineage["target_digest"] = hashlib.sha256(content).hexdigest()
    lineage["resources"] = {
        key: value.model_dump(mode="json") for key, value in artifact.frontmatter.assets.items()
    }
    held.assert_owned()
    with _directory_fd(cfg.data_dir / "trust/sources", create=True) as directory:
        try:
            prior = _read_file(directory, expected_source_digest)
        except FileNotFoundError:
            _replace_file(directory, expected_source_digest, source, held.assert_owned)
        else:
            if prior != source:
                raise CustodianError("source archive digest mismatch")
    journal.append(
        record_id="transform-" + hashlib.sha256(_bytes(lineage)).hexdigest(),
        kind="artifact_transform",
        payload=lineage,
        fence=fence,
        assert_owned=held.assert_owned,
    )
    with _directory_fd(target.parent) as directory:
        if hashlib.sha256(_read_file(directory, target.name)).hexdigest() != expected_source_digest:
            raise CustodianError("authored edit source changed before publication")
    with _directory_fd(output.parent, create=True) as directory:
        _replace_file(directory, output.name, content, held.assert_owned)
    held.assert_owned()
