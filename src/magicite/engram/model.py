"""Pydantic v0.2 frontmatter + body models (docs/04, spec §2.5).

These are the in-memory representation ``engram/parser.py`` produces and
``engram/writer.py`` renders back to disk. They mirror
``engram/schema/engram-0.2.schema.json`` field-for-field; the JSON Schema
is the machine-readable validation authority (``engram/lint.py`` runs it),
these models are the typed, ergonomic view the rest of the codebase works
against.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SpecVersion = Literal["engram/0.2"]
Origin = Literal["authored", "imported", "distilled", "sharpened"]
EngramStatus = Literal["draft", "nascent", "probation", "consolidated", "promoted", "archived"]
VerificationStatus = Literal["pending", "verified", "quarantined"]
SynapseType = Literal["co_activation", "composes", "depends_on", "similar_to", "inhibits"]
SynapseProvenance = Literal["declared", "learned", "distilled", "derived"]

#: Routable per spec §5.1: status in {nascent, probation, consolidated, promoted}
#: AND verification_status == 'verified'.
ROUTABLE_STATUSES: frozenset[str] = frozenset(
    {"nascent", "probation", "consolidated", "promoted"}
)


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    does: str = Field(min_length=1)
    use_when: str = Field(min_length=1)
    not_when: str | None = None


class Triggers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class EmbeddingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    ref: str | None = None
    last_refreshed: str | None = None


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: int = 0
    failure: int = 0


class Plasticity(BaseModel):
    """Tier A: durable node state (Dream-checkpoint-only, spec §6.2 G3)."""

    model_config = ConfigDict(extra="forbid")

    storage_strength: float = 0.0
    exposure_count: int = 0
    outcome: Outcome = Field(default_factory=Outcome)
    last_applied: str | None = None
    excitability: float = 0.05
    last_checkpoint: str | None = None
    status: EngramStatus = "nascent"


class Synapse(BaseModel):
    """Tier B: one durable learned/declared edge (Dream-checkpoint-only)."""

    model_config = ConfigDict(extra="forbid")

    target: str
    type: SynapseType
    storage_strength: float = 0.0
    evidence_count: int = 0
    provenance: SynapseProvenance
    first_observed: str
    last_updated: str | None = None


class ProvenanceJournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    timestamp: str
    author: str
    event: str
    note: str | None = None
    summary_of_change: str | None = None
    signal_tier: str | None = None
    base_version: int | None = None


class InjectionRisk(BaseModel):
    model_config = ConfigDict(extra="allow")

    triggers: str | None = None
    pitfalls: str | None = None
    intent: str | None = None


class Trust(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Literal["authored", "imported", "distilled"] = "authored"
    verification_status: VerificationStatus = "pending"
    signer: str | None = None
    import_source: str | None = None
    injection_risk: InjectionRisk | None = None


class Exports(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_md: bool = True


class EngramFrontmatter(BaseModel):
    """The full v0.2 YAML frontmatter (docs/04 §Frontmatter Schema)."""

    model_config = ConfigDict(extra="allow")  # forward-compatible at the root, per the schema

    spec: SpecVersion = "engram/0.2"
    name: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    id: str = Field(pattern=r"^egr_[0-9a-f]{8}$")
    version: int = Field(ge=1, default=1)
    provenance: Origin
    parents: list[str] = Field(default_factory=list)

    intent: Intent
    triggers: Triggers
    context_affinity: list[str] = Field(default_factory=list)
    embedding: EmbeddingRef | None = None

    plasticity: Plasticity | None = None
    #: Tier A: the high-water mark ``plasticity.storage_strength`` has ever
    #: reached (``core/decay.py::archive_below_floor``'s eligibility gate,
    #: the M5 data-integrity fix). Deliberately a ROOT-level field, not a
    #: ``plasticity:`` sub-key: docs/04's Plasticity Block is an exact,
    #: frozen field-list illustration this change must not silently
    #: diverge from, while the root frontmatter is explicitly
    #: forward-compatible (``additionalProperties: true`` in the JSON
    #: schema; this model's own ``extra="allow"``). Dream-checkpoint-only
    #: in practice, same as every other Tier A field -- see
    #: ``core/dream.py::_build_checkpoint_candidate`` and
    #: ``core/decay.py::archive_one``.
    peak_storage_strength: float = Field(default=0.0, ge=0.0)
    synapses: list[Synapse] = Field(default_factory=list)

    needs: list[str] = Field(default_factory=list)
    yields: list[str] = Field(default_factory=list)
    composes: list[str] = Field(default_factory=list)
    inhibits: list[str] = Field(default_factory=list)
    affinity: list[str] = Field(default_factory=list)

    provenance_journal: list[ProvenanceJournalEntry] = Field(default_factory=list)
    trust: Trust | None = None
    exports: Exports | None = None


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_no: int
    text: str
    ok_count: int = 0
    total_count: int = 0
    fault_class: str | None = None


class PitfallEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    count: int = 1


class ExampleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positive: bool
    text: str
    note: str | None = None


class ExecBlock(BaseModel):
    """Fenced exec block, captured as inert text and never evaluated (spec §2.5)."""

    model_config = ConfigDict(extra="forbid")

    language: str
    text: str


class EngramBody(BaseModel):
    """Procedure | Pitfalls | Examples | Provenance sections (spec §2.5)."""

    model_config = ConfigDict(extra="forbid")

    procedure: list[ProcedureStep] = Field(default_factory=list)
    procedure_raw: str = ""  # unmatched/unstructured Procedure text, verbatim (register() rule)
    pitfalls: list[PitfallEntry] = Field(default_factory=list)
    examples: list[ExampleEntry] = Field(default_factory=list)
    provenance_lines: list[str] = Field(default_factory=list)
    exec_blocks: list[ExecBlock] = Field(default_factory=list)


class Engram(BaseModel):
    """The parsed, in-memory unit ``engram/parser.py`` hands to the rest of
    the codebase: frontmatter + body + the file-level metadata needed for
    dirty detection (spec §2.2's ``*_sha256``/``file_mtime_ns`` columns)."""

    model_config = ConfigDict(extra="forbid")

    frontmatter: EngramFrontmatter
    body: EngramBody
    path: str  # registry-relative
    content_sha256: str
    body_sha256: str
    file_mtime_ns: int

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def id(self) -> str:
        return self.frontmatter.id

    @property
    def routable(self) -> bool:
        plasticity = self.frontmatter.plasticity
        status = plasticity.status if plasticity else "nascent"
        verification = self.frontmatter.trust.verification_status if self.frontmatter.trust else "pending"
        return status in ROUTABLE_STATUSES and verification == "verified"
