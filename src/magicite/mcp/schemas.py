"""Pydantic in/out models for the 16-tool surface (spec §3.2-3.3).

``model_config = ConfigDict(extra="forbid")`` on **every** model, input and
output (AC-005). This module is pure data — no MCP import, no business
logic; ``magicite.mcp.bind_*`` translate between these and
``magicite.core``/``magicite.engram`` plain dataclasses.

Only fields spec §3.3 leaves genuinely open-ended (e.g. ``sharpen``'s
``proposed_changes``, the governance ``evidence`` blob) use a permissive
``dict[str, Any]`` rather than an invented sub-schema — the spec did not
freeze those shapes and this is not the milestone to invent them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_Forbid = ConfigDict(extra="forbid")


class MagiciteModel(BaseModel):
    model_config = _Forbid


# ── shared fragments ─────────────────────────────────────────────────────


class RouteContext(MagiciteModel):
    project_tag: str | None = None
    recent_failures: list[str] = Field(default_factory=list)
    user_prefs: list[str] = Field(default_factory=list)


class Candidate(MagiciteModel):
    rank: int
    id: str
    name: str
    intent_does: str
    intent_use_when: str
    score: float
    status: str
    exposure_count: int
    body_ref: str
    signal_tier_0: bool = True
    diagnostics: dict[str, float] = Field(default_factory=dict)


class EdgeOut(MagiciteModel):
    target: str
    type: str
    storage_strength: float
    provenance: str
    evidence_count: int = 0
    #: spec §3.3.1 call site 6 / AC-041: S_eff = max(storage_strength,
    #: w_authored(edge)), computed at read by core/edge_weight.py. Additive
    #: field -- storage_strength (the learned channel) is unchanged and
    #: still reports 0.0 for a never-potentiated declared edge.
    effective_strength: float = 0.0


class HistoryEntry(MagiciteModel):
    version: int
    event: str
    timestamp: str
    author: str
    note: str | None = None


class TierState(MagiciteModel):
    storage_strength: float
    storage_strength_effective_now: float
    retrieval_strength: float
    reliability: float
    live_tags: int = 0
    pending_dw: float = 0.0


class SkillIntrospect(MagiciteModel):
    id: str
    name: str
    status: str
    verification_status: str
    operation_execution_status: str | None = None
    storage_strength: float
    exposure_count: int
    outcome: dict[str, int]
    reliability: float


class ConsolidationInfo(MagiciteModel):
    id: str
    state: str
    phase: str | None = None
    stats: dict[str, Any] | None = None
    audit_report: dict[str, Any] | None = None


class RegistrySummary(MagiciteModel):
    counts_by_status: dict[str, int]
    detector: str
    last_sync: str | None
    last_consolidation: str | None
    registry_size: int
    embedding_model: str
    autonomous_mode: bool


class RegisteredEntryOut(MagiciteModel):
    id: str
    name: str
    origin: str
    status: str
    verification_status: str
    warnings: list[str] = Field(default_factory=list)


class ValidationErrorOut(MagiciteModel):
    path: str
    message: str


class DeadCandidate(MagiciteModel):
    id: str
    name: str
    last_routed: str | None
    retrieval_strength: float
    reason: str


class NucleateCandidate(MagiciteModel):
    proposal_id: str
    path_names: list[str]
    support: int
    mean_valence: float
    trace_ir: dict[str, Any] = Field(default_factory=dict)
    draft_skeleton: str


class SharpenChanges(MagiciteModel):
    procedures: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)


# ── 1. route ────────────────────────────────────────────────────────────


class RouteInput(MagiciteModel):
    query: str = Field(min_length=1, max_length=500)
    context: RouteContext | None = None
    k: int = Field(default=5, ge=1, le=20)
    session_id: str | None = None


class RouteOutput(MagiciteModel):
    candidates: list[Candidate]
    composition_plan: list[str]
    plan_confidence: float
    instructions: str
    session_id: str
    registry_size: int
    unresolved_context: list[str] = Field(default_factory=list)


# ── 2. load_skill_body ─────────────────────────────────────────────────


class LoadSkillBodyInput(MagiciteModel):
    name: str
    level: Literal["L2", "L3"] = "L2"
    max_bytes: int = Field(default=8192, ge=1)
    cursor: int = Field(default=0, ge=0)


class LoadSkillBodyOutput(MagiciteModel):
    name: str
    level: Literal["L2", "L3"]
    procedure: str
    pitfalls: str
    examples: str | None = None
    provenance: str | None = None
    exec_blocks_present: bool = False
    exec_blocks_warning: str | None = None
    truncated: bool = False
    next_offset: int | None = None


# ── 3. introspect ───────────────────────────────────────────────────────


class IntrospectInput(MagiciteModel):
    skill_id: str | None = None
    consolidation_id: str | None = None
    include_health: bool = False


class IntrospectOutput(MagiciteModel):
    skill: SkillIntrospect | None = None
    outbound_edges: list[EdgeOut] = Field(default_factory=list)
    inbound_edges: list[EdgeOut] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)
    silent_engram_flag: bool | None = None
    tier_state: TierState | None = None
    consolidation: ConsolidationInfo | None = None
    registry_summary: RegistrySummary | None = None
    health: dict[str, Any] | None = None


# ── 4. flag_dead ────────────────────────────────────────────────────────


class FlagDeadInput(MagiciteModel):
    window_days: int = Field(default=30, ge=1)
    limit: int = Field(default=50, ge=1, le=500)


class FlagDeadOutput(MagiciteModel):
    candidates: list[DeadCandidate]
    recommendation: str
    silent_pct: float


# ── 5. signal_use ───────────────────────────────────────────────────────


class SignalUseInput(MagiciteModel):
    skill_ids: list[str] = Field(min_length=1, max_length=20)
    session_id: str | None = None
    adapter_token: str | None = None
    request_id: str | None = None


class SignalUseOutput(MagiciteModel):
    tagged: list[str]
    co_activation_candidates: list[str]
    expires_at: str
    signal_tier: int
    capped: list[str] = Field(default_factory=list)
    note: str


# ── 6. signal_outcome ──────────────────────────────────────────────────


class SignalOutcomeInput(MagiciteModel):
    valence: float = Field(ge=-1.0, le=1.0)
    salience: float | None = Field(default=0.5, ge=0.0, le=1.0)
    skill_ids: list[str] | None = None
    session_id: str | None = None
    adapter_token: str | None = None
    request_id: str | None = None


class SignalOutcomeOutput(MagiciteModel):
    captured: int
    skills_credited: list[str]
    signal_tier: int
    consolidation_scheduled: bool
    note: str


# ── 7. session_end ──────────────────────────────────────────────────────


class SessionEndInput(MagiciteModel):
    session_id: str
    reason: str | None = None
    request_id: str | None = None


class SessionEndOutput(MagiciteModel):
    session_id: str
    closed: bool
    tags_expired: int
    captured_pending: int
    dream_run_id: str | None
    enqueued: bool


# ── 8. register ─────────────────────────────────────────────────────────


class RegisterInput(MagiciteModel):
    path: str
    format: Literal["auto", "egr", "skill"] = "auto"
    request_id: str | None = None


class RegisterOutput(MagiciteModel):
    ingested: int
    registered: list[RegisteredEntryOut]
    validation_errors: list[ValidationErrorOut]
    skipped_unchanged: int
    consolidation_scheduled: bool


# ── 9. sync ──────────────────────────────────────────────────────────────


class SyncInput(MagiciteModel):
    request_id: str | None = None


class SyncOutput(MagiciteModel):
    synced: int
    removed: list[str]
    validation_errors: list[ValidationErrorOut]
    dangling: list[str]
    detector: str
    consolidation_scheduled: bool


# ── 10. checkpoint ─────────────────────────────────────────────────────


class CheckpointInput(MagiciteModel):
    request_id: str | None = None


class CheckpointOutput(MagiciteModel):
    checkpointed: int
    modified_engrams: list[str]
    write_ratio: float
    timestamp: str


# ── 11. export ──────────────────────────────────────────────────────────


class ExportInput(MagiciteModel):
    out_dir: str
    min_status: Literal["consolidated", "promoted"] = "consolidated"
    request_id: str | None = None


class ExportOutput(MagiciteModel):
    exported: int
    target_dir: str
    format: str
    note: str


# ── 12. consolidate ────────────────────────────────────────────────────


class ConsolidateInput(MagiciteModel):
    manual_trigger: bool = False
    request_id: str | None = None


class ConsolidateOutput(MagiciteModel):
    consolidation_id: str
    enqueued: bool
    status: str
    estimated_start: str | None
    note: str


# ── 13. nucleate ────────────────────────────────────────────────────────


class NucleateInput(MagiciteModel):
    trace_ids: list[str] | None = None
    min_support: int = Field(default=5, ge=1)
    request_id: str | None = None


class NucleateOutput(MagiciteModel):
    candidates: list[NucleateCandidate]
    approval_ids: list[str]
    note: str


# ── 14. sharpen ────────────────────────────────────────────────────────


class SharpenInput(MagiciteModel):
    name: str
    proposed_changes: SharpenChanges | None = None
    request_id: str | None = None


class SharpenOutput(MagiciteModel):
    sharpened: bool = False
    version_bumped: str | None = None
    changes: list[str] = Field(default_factory=list)
    consolidation_scheduled: bool = False
    approval_id: str | None = None
    state: str | None = None
    requires_approval: bool = False


# ── 15. promote ────────────────────────────────────────────────────────


class PromoteInput(MagiciteModel):
    name: str
    request_id: str | None = None


class PromoteOutput(MagiciteModel):
    promoted: bool = False
    name: str
    new_status: str | None = None
    evidence: dict[str, Any] | None = None
    approval_id: str | None = None
    state: str | None = None
    requires_approval: bool = False


# ── 16. archive ────────────────────────────────────────────────────────


class ArchiveInput(MagiciteModel):
    name: str
    reason: str | None = None
    request_id: str | None = None


class ArchiveOutput(MagiciteModel):
    archived: bool = False
    name: str
    provenance_entry: str | None = None
    approval_id: str | None = None
    state: str | None = None
    requires_approval: bool = False


# ── wire-level error envelope (errors.py -> MCP structured content) ─────


class ErrorEnvelope(MagiciteModel):
    code: str
    message: str
    hint: str | None = None
    retryable: bool = False
