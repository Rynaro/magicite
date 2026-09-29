"""Pydantic Engram 1.0 frontmatter + artifact models (contracts.md C1).

Mirrors ``engram/schema/engram-1.0.schema.json`` field-for-field. The JSON
Schema is the machine-readable validation authority; these models are the
typed view S03–S09 consume. Distinct from the 0.2 models in ``model.py`` —
dual-reader code holds either representation, never a silent mix.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magicite.engram.model import EngramBody, SkillMdSourceSnapshot

SpecVersionV1 = Literal["engram/1.0"]
OriginChannel = Literal["authored", "imported", "distilled", "sharpened"]
VerificationStatusV1 = Literal["pending", "verified", "quarantined"]
OsName = Literal["linux", "macos", "windows"]
VersionScheme = Literal["semver", "pep440"]
CapabilityKindRequired = Literal["host", "artifact"]
FilesystemRisk = Literal["none", "read-project", "write-project"]
SubprocessMode = Literal["none", "declared-tools"]
NetworkMode = Literal["none", "optional", "required"]
SecretsRisk = Literal["none", "redacted", "raw"]

#: Extension namespaces the runtime understands at activation time.
#: Unknown keys with ``required: true`` fail closed (C1 / AC-S02-03).
KNOWN_EXTENSIONS: frozenset[str] = frozenset()


class VersionConstraint(BaseModel):
    """Explicit version constraint (C1). Unsupported schemes/ranges reject."""

    model_config = ConfigDict(extra="forbid")

    scheme: VersionScheme
    range: str = Field(min_length=1)


class HostRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: VersionConstraint | None = None


class Compatibility(BaseModel):
    """Optional environment dimensions. Missing dimension = unconstrained."""

    model_config = ConfigDict(extra="forbid")

    os: list[OsName] = Field(default_factory=list)
    languages: dict[str, VersionConstraint] = Field(default_factory=dict)
    frameworks: dict[str, VersionConstraint] = Field(default_factory=dict)
    package_managers: dict[str, VersionConstraint] = Field(default_factory=dict)
    hosts: list[HostRequirement] = Field(default_factory=list)

    @field_validator("languages", "frameworks", "package_managers", mode="before")
    @classmethod
    def _normalize_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {str(k).lower(): v for k, v in value.items()}


class RequiredCapability(BaseModel):
    """Host or artifact prerequisite. Missing ``kind`` is invalid (C1)."""

    model_config = ConfigDict(extra="forbid")

    kind: CapabilityKindRequired
    id: str = Field(min_length=1)
    version: VersionConstraint | None = None


class ProducedCapability(BaseModel):
    """Artifact capability only — skills cannot produce host grants (C1)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["artifact"] = "artifact"
    id: str = Field(min_length=1)
    version: str | None = None


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires: list[RequiredCapability] = Field(default_factory=list)
    produces: list[ProducedCapability] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)


class EngramRevisionRef(BaseModel):
    """Exact engram reference for relations.requires/before/supersedes (C1)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^egr_[0-9a-f]{8}$")
    version: int = Field(ge=1)


class Relations(BaseModel):
    """Normative graph edges. Never inferred from learned synapses (C1)."""

    model_config = ConfigDict(extra="forbid")

    requires: list[EngramRevisionRef] = Field(default_factory=list)
    before: list[EngramRevisionRef] = Field(default_factory=list)
    supersedes: list[EngramRevisionRef] = Field(default_factory=list)


class SubprocessRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SubprocessMode
    tools: list[str] = Field(default_factory=list)


class NetworkRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: NetworkMode
    destinations: list[str] = Field(default_factory=list)


class Risk(BaseModel):
    """Requested effects, never permission grants (C1)."""

    model_config = ConfigDict(extra="forbid")

    filesystem: FilesystemRisk = "none"
    subprocess: SubprocessRisk = Field(default_factory=lambda: SubprocessRisk(mode="none"))
    network: NetworkRisk = Field(default_factory=lambda: NetworkRisk(mode="none"))
    secrets: SecretsRisk = "none"


class RoutingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)
    body_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProvenanceJournalEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    timestamp: str
    author: str
    event: str
    note: str | None = None
    summary_of_change: str | None = None
    signal_tier: str | None = None
    base_version: int | None = None


class Origin(BaseModel):
    """Authorship/import descriptors. No author-controlled local trust (C1)."""

    model_config = ConfigDict(extra="forbid")

    channel: OriginChannel
    verification_status: VerificationStatusV1 = "pending"
    signer: str | None = None
    import_source: str | None = None
    content_hashes: dict[str, str] = Field(default_factory=dict)
    journal: list[ProvenanceJournalEntryV1] = Field(default_factory=list)


class AssetDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    media_type: str | None = None


class ExtensionValue(BaseModel):
    """Namespaced extension payload. ``required`` is the activation gate."""

    model_config = ConfigDict(extra="allow")

    required: bool = False


class IntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    does: str = Field(min_length=1)
    use_when: str = Field(min_length=1)
    not_when: str | None = None


class EngramFrontmatterV1(BaseModel):
    """Full engram/1.0 YAML frontmatter (C1)."""

    model_config = ConfigDict(extra="forbid")

    spec: SpecVersionV1 = "engram/1.0"
    name: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    id: str = Field(pattern=r"^egr_[0-9a-f]{8}$")
    version: int = Field(ge=1, default=1)
    parents: list[str] = Field(default_factory=list)

    intent: IntentV1
    routing: RoutingV1
    origin: Origin

    compatibility: Compatibility | None = None
    capabilities: Capabilities | None = None
    relations: Relations | None = None
    risk: Risk | None = None
    assets: dict[str, AssetDescriptor] = Field(default_factory=dict)
    extensions: dict[str, ExtensionValue] = Field(default_factory=dict)
    skill_md_source: SkillMdSourceSnapshot | None = None
    #: Non-authoritative residual 0.2 state (plasticity, synapses, prose needs…).
    legacy: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _extension_keys_look_namespaced(self) -> EngramFrontmatterV1:
        for key in self.extensions:
            if "." not in key:
                raise ValueError(f"extension key {key!r} must be namespaced (a.b)")
        return self


class EngramV1(BaseModel):
    """Parsed engram/1.0 artifact: frontmatter + shared body + file metadata."""

    model_config = ConfigDict(extra="forbid")

    frontmatter: EngramFrontmatterV1
    body: EngramBody
    path: str
    content_sha256: str
    body_sha256: str
    file_mtime_ns: int

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def id(self) -> str:
        return self.frontmatter.id
