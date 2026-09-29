"""Pure engram/0.2 → engram/1.0 transform for S03 (C1 / C8).

Preserves stable ``id``. Does not invent typed capabilities from prose
``needs``/``yields``, does not invent ``before``/``supersedes`` from learned
edges, and parks unsupported residual state under ``legacy`` (non-authoritative).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from magicite.engram.digests import routing_body_digest
from magicite.engram.model import Engram, Synapse
from magicite.engram.model_v1 import (
    Capabilities,
    EngramFrontmatterV1,
    EngramRevisionRef,
    EngramV1,
    IntentV1,
    Origin,
    ProvenanceJournalEntryV1,
    Relations,
    Risk,
    RoutingV1,
    VerificationStatusV1,
)

DiagnosticCode = Literal[
    "depends_on_unpinned",
    "composes_unmapped",
    "needs_unmapped",
    "yields_unmapped",
    "learned_edge_ignored",
    "prose_capability_ignored",
]


@dataclass(frozen=True)
class TransformDiagnostic:
    code: DiagnosticCode
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransformResult:
    """Pure transform output. ``diagnostics`` are migration signals for S03."""

    engram: EngramV1
    diagnostics: tuple[TransformDiagnostic, ...] = ()

    @property
    def ok_for_composition(self) -> bool:
        """False when mandatory relation pins could not be resolved (C1)."""
        blocking = {"depends_on_unpinned"}
        return not any(d.code in blocking for d in self.diagnostics)


def transform_0_2_to_1_0(
    engram: Engram,
    *,
    revision_map: dict[str, int] | None = None,
) -> TransformResult:
    """Map a parsed 0.2 engram to 1.0 without mutating the input.

    ``revision_map`` pins ``{id: integer_revision}`` from the migration
    snapshot for ``depends_on`` → ``relations.requires``. Missing targets
    become diagnostics (never guessed).
    """
    if engram.frontmatter.spec != "engram/0.2":
        raise ValueError(f"transform_0_2_to_1_0 expects engram/0.2, got {engram.frontmatter.spec!r}")

    revisions = revision_map or {}
    diagnostics: list[TransformDiagnostic] = []
    fm = engram.frontmatter

    # Normative routing.body_digest from the deterministic body render (LF-normalized).
    from magicite.engram.writer import render_body

    body_digest = routing_body_digest(render_body(engram.body))

    relations = Relations()
    for synapse in fm.synapses:
        _consume_synapse(synapse, revisions, relations, diagnostics)

    # Declared depends_on may also appear only as prose needs — never invent
    # typed capabilities from needs/yields (C1).
    if fm.needs:
        diagnostics.append(
            TransformDiagnostic(
                code="needs_unmapped",
                message="0.2 needs[] retained in legacy; not mapped to typed capabilities",
                detail={"needs": list(fm.needs)},
            )
        )
    if fm.yields:
        diagnostics.append(
            TransformDiagnostic(
                code="yields_unmapped",
                message="0.2 yields[] retained in legacy; not mapped to typed capabilities",
                detail={"yields": list(fm.yields)},
            )
        )
    if fm.composes:
        diagnostics.append(
            TransformDiagnostic(
                code="composes_unmapped",
                message="0.2 composes[] retained in legacy until reviewed (C1)",
                detail={"composes": list(fm.composes)},
            )
        )

    journal = [
        ProvenanceJournalEntryV1(
            version=e.version,
            timestamp=e.timestamp,
            author=e.author,
            event=e.event,
            note=e.note,
            summary_of_change=e.summary_of_change,
            signal_tier=e.signal_tier,
            base_version=e.base_version,
        )
        for e in fm.provenance_journal
    ]

    verification: VerificationStatusV1 = "pending"
    signer = None
    import_source = None
    if fm.trust is not None:
        verification = fm.trust.verification_status
        signer = fm.trust.signer
        import_source = fm.trust.import_source

    origin = Origin(
        channel=fm.provenance,
        verification_status=verification,
        signer=signer,
        import_source=import_source,
        content_hashes={
            "content_sha256": engram.content_sha256,
            "body_sha256": engram.body_sha256,
        },
        journal=journal,
    )

    legacy = _build_legacy(fm)

    frontmatter = EngramFrontmatterV1(
        spec="engram/1.0",
        name=fm.name,
        id=fm.id,
        version=fm.version,
        parents=list(fm.parents),
        intent=IntentV1(
            does=fm.intent.does,
            use_when=fm.intent.use_when,
            not_when=fm.intent.not_when,
        ),
        routing=RoutingV1(
            positive=list(fm.triggers.positive),
            negative=list(fm.triggers.negative),
            body_digest=body_digest,
        ),
        origin=origin,
        compatibility=None,
        capabilities=Capabilities(),
        relations=relations,
        risk=Risk(),
        assets={},
        extensions={},
        skill_md_source=fm.skill_md_source,
        legacy=legacy or None,
    )

    result = EngramV1(
        frontmatter=frontmatter,
        body=engram.body.model_copy(deep=True),
        path=engram.path,
        content_sha256=engram.content_sha256,
        body_sha256=engram.body_sha256,
        file_mtime_ns=engram.file_mtime_ns,
    )
    return TransformResult(engram=result, diagnostics=tuple(diagnostics))


def _consume_synapse(
    synapse: Synapse,
    revisions: dict[str, int],
    relations: Relations,
    diagnostics: list[TransformDiagnostic],
) -> None:
    if synapse.type == "depends_on" and synapse.provenance == "declared":
        pinned = revisions.get(synapse.target)
        if pinned is None:
            diagnostics.append(
                TransformDiagnostic(
                    code="depends_on_unpinned",
                    message=(
                        f"declared depends_on {synapse.target!r} lacks a revision pin "
                        "in the migration snapshot"
                    ),
                    detail={"target": synapse.target},
                )
            )
            return
        relations.requires.append(EngramRevisionRef(id=synapse.target, version=pinned))
        return

    if synapse.type == "depends_on":
        diagnostics.append(
            TransformDiagnostic(
                code="learned_edge_ignored",
                message="non-declared depends_on retained in legacy only",
                detail={"target": synapse.target, "provenance": synapse.provenance},
            )
        )
        return

    if synapse.provenance == "learned":
        diagnostics.append(
            TransformDiagnostic(
                code="learned_edge_ignored",
                message=f"learned synapse type={synapse.type!r} not mapped to normative relations",
                detail={"target": synapse.target, "type": synapse.type},
            )
        )


def _build_legacy(fm: Any) -> dict[str, Any]:
    legacy: dict[str, Any] = {}
    if fm.plasticity is not None:
        legacy["plasticity"] = fm.plasticity.model_dump(mode="json")
    if fm.peak_storage_strength:
        legacy["peak_storage_strength"] = fm.peak_storage_strength
    if fm.synapses:
        legacy["synapses"] = [s.model_dump(mode="json") for s in fm.synapses]
    if fm.needs:
        legacy["needs"] = list(fm.needs)
    if fm.yields:
        legacy["yields"] = list(fm.yields)
    if fm.composes:
        legacy["composes"] = list(fm.composes)
    if fm.inhibits:
        legacy["inhibits"] = list(fm.inhibits)
    if fm.affinity:
        legacy["affinity"] = list(fm.affinity)
    if fm.context_affinity:
        legacy["context_affinity"] = list(fm.context_affinity)
    if fm.embedding is not None:
        legacy["embedding"] = fm.embedding.model_dump(mode="json")
    if fm.exports is not None:
        legacy["exports"] = fm.exports.model_dump(mode="json")
    if fm.trust is not None and fm.trust.injection_risk is not None:
        legacy["injection_risk"] = fm.trust.injection_risk.model_dump(mode="json")
    return legacy
