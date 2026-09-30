"""External corpus adapter (evaluation.md E2).

CI uses the offline tiny SkillRet-shaped fixture shipped under
``docs/evaluation/v1/fixtures/skillret-tiny/``. Full official download is an
explicit command that records license + digest metadata; it never runs as a
side effect of import or ordinary tests. Release-scale SkillRet execution
belongs to S14.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magicite.eval.digests import SCHEMA_CORPUS, sha256_json, sha256_path
from magicite.eval.manifests import ArtifactRef, CorpusManifest, QueryRecord, parse_corpus
from magicite.eval.validate import validate_corpus_manifest_data

#: Pin from evaluation.md / research/corrections.md — abstract counts must
#: not be hard-coded as item counts; acquisition records the real revision.
SKILLRET_SOURCE_URL = "https://arxiv.org/abs/2605.05726v3"
SKILLRET_SOURCE_CITATION = "arXiv:2605.05726v3"

DEFAULT_TINY_FIXTURE = (
    Path(__file__).resolve().parents[3] / "docs" / "evaluation" / "v1" / "fixtures" / "skillret-tiny"
)


@dataclass(frozen=True)
class ExternalAcquisitionRecord:
    source_url: str
    source_citation: str
    license: str
    dataset_revision: str
    archive_sha256: str
    acquired_at: str
    local_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_citation": self.source_citation,
            "license": self.license,
            "dataset_revision": self.dataset_revision,
            "archive_sha256": self.archive_sha256,
            "acquired_at": self.acquired_at,
            "local_path": self.local_path,
        }


def load_offline_skillret_fixture(root: Path | None = None) -> CorpusManifest:
    """Load the tiny offline adapter fixture (no network)."""
    base = root if root is not None else DEFAULT_TINY_FIXTURE
    manifest_path = base / "corpus_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate_corpus_manifest_data(data)
    if errors:
        raise ValueError("offline SkillRet fixture invalid: " + "; ".join(errors))
    return parse_corpus(data)


def record_external_download(
    *,
    archive_path: Path,
    license_name: str,
    dataset_revision: str,
    acquired_at: str,
    source_url: str = SKILLRET_SOURCE_URL,
    source_citation: str = SKILLRET_SOURCE_CITATION,
) -> ExternalAcquisitionRecord:
    """Record license + digest for an explicitly downloaded archive.

    Does not perform the download. Callers must fetch out-of-band, then
    pass the local archive path here.
    """
    if not archive_path.is_file():
        raise FileNotFoundError(f"external archive not found: {archive_path}")
    return ExternalAcquisitionRecord(
        source_url=source_url,
        source_citation=source_citation,
        license=license_name,
        dataset_revision=dataset_revision,
        archive_sha256=sha256_path(archive_path),
        acquired_at=acquired_at,
        local_path=str(archive_path),
    )


def build_tiny_corpus_manifest(
    *,
    corpus_id: str = "skillret-tiny-offline/1",
    queries: list[QueryRecord],
    license_name: str = "fixture-only-not-redistributable-as-skillret",
    dataset_revision: str = "offline-fixture-2026-09-29",
) -> CorpusManifest:
    """Helper for tests and fixture regeneration."""
    payload_queries = [q.to_dict() for q in queries]
    content_identity = sha256_json({"queries": payload_queries})
    artifacts = (
        ArtifactRef(
            path="queries.json",
            sha256=sha256_json(payload_queries),
            role="queries",
            byte_length=len(sha256_json(payload_queries)),
        ),
    )
    return CorpusManifest(
        corpus_id=corpus_id,
        artifacts=artifacts,
        queries=tuple(queries),
        label_origin="imported_official",
        dataset_revision=dataset_revision,
        license=license_name,
        content_identity_sha256=content_identity,
        schema=SCHEMA_CORPUS,
    )


def official_skillret_status() -> dict[str, Any]:
    """Release evidence status for the official SkillRet split.

    Always UNEVALUATED unless an operator-acquired archive digest is supplied
    out-of-band. Never fabricates PASS or metric numbers.
    """
    from magicite.eval.unevaluated import unevaluated_by_id

    item = unevaluated_by_id("skillret-official-final-split")
    return {
        "status": "UNEVALUATED",
        "source_url": SKILLRET_SOURCE_URL,
        "source_citation": SKILLRET_SOURCE_CITATION,
        "offline_fixture": str(DEFAULT_TINY_FIXTURE),
        "operator_command": item.operator_command,
        "manifest_or_digest": item.manifest_or_digest,
        "reason": item.reason,
    }


def verify_acquired_corpus_manifest(path: Path) -> tuple[CorpusManifest | None, list[str]]:
    """Load + validate an operator-acquired corpus manifest (no network)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_corpus_manifest_data(data)
    if errors:
        return None, errors
    return parse_corpus(data), []
