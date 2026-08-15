"""``register``/``sync``/``checkpoint``/``export`` (spec §3.3 tools 8-11).

M1: ``register`` (native ``.egr.md`` *and* SKILL.md import, spec §5.3),
``sync`` (spec §2.6) and ``export`` (spec §5.4, the SKILL.md compile
target) are all implemented. M4: ``checkpoint`` is Dream's phase-7 writer
path (``core.dream.run_checkpoint_only``) run in isolation.
"""

from __future__ import annotations

from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    CheckpointInput,
    CheckpointOutput,
    ExportInput,
    ExportOutput,
    RegisteredEntryOut,
    RegisterInput,
    RegisterOutput,
    SyncInput,
    SyncOutput,
    ValidationErrorOut,
)


def _to_validation_errors(errors: list) -> list[ValidationErrorOut]:
    return [ValidationErrorOut(path=v.path, message=v.message) for v in errors]


@magicite_tool(
    risk="R2",
    side_effect="filesystem+durable_index",
    idempotent=True,
    input_model=RegisterInput,
    output_model=RegisterOutput,
    description="Ingest .egr.md (native) or SKILL.md (import) into the registry.",
)
def register(ctx: ToolContext, params: RegisterInput) -> RegisterOutput:
    outcome = registry_mod.register(ctx.cfg, ctx.conn, ctx.embedder, path=params.path, fmt=params.format)
    return RegisterOutput(
        ingested=outcome.ingested,
        registered=[
            RegisteredEntryOut(
                id=e.id,
                name=e.name,
                origin=e.origin,
                status=e.status,
                verification_status=e.verification_status,
                warnings=e.warnings,
            )
            for e in outcome.registered
        ],
        validation_errors=_to_validation_errors(outcome.validation_errors),
        skipped_unchanged=outcome.skipped_unchanged,
        consolidation_scheduled=outcome.consolidation_scheduled,
    )


@magicite_tool(
    risk="R2",
    side_effect="durable_index",
    idempotent=True,
    input_model=SyncInput,
    output_model=SyncOutput,
    description="Rebuild the durable index from the .egr.md registry (the rebuildable-index invariant).",
)
def sync(ctx: ToolContext, params: SyncInput) -> SyncOutput:
    outcome = registry_mod.sync(ctx.cfg, ctx.conn, ctx.embedder)
    return SyncOutput(
        synced=outcome.synced,
        removed=outcome.removed,
        validation_errors=_to_validation_errors(outcome.validation_errors),
        dangling=outcome.dangling,
        detector=outcome.detector,
        consolidation_scheduled=outcome.consolidation_scheduled,
    )


@magicite_tool(
    risk="R2",
    side_effect="filesystem",
    idempotent=True,
    input_model=CheckpointInput,
    output_model=CheckpointOutput,
    description="Idempotent DB -> file flush (Dream phase 7 in isolation).",
)
def checkpoint(ctx: ToolContext, params: CheckpointInput) -> CheckpointOutput:
    stats = dream_mod.run_checkpoint_only(ctx.cfg, ctx.conn)
    return CheckpointOutput(
        checkpointed=stats.checkpointed,
        modified_engrams=stats.modified_engrams,
        write_ratio=stats.write_ratio,
        timestamp=stats.timestamp,
    )


@magicite_tool(
    risk="R2",
    side_effect="filesystem",
    idempotent=True,
    input_model=ExportInput,
    output_model=ExportOutput,
    description="Render SKILL.md shims from consolidated+ engrams (the export compile target).",
)
def export(ctx: ToolContext, params: ExportInput) -> ExportOutput:
    outcome = registry_mod.export(ctx.cfg, ctx.conn, out_dir=params.out_dir, min_status=params.min_status)
    return ExportOutput(
        exported=outcome.exported,
        target_dir=outcome.target_dir,
        format=outcome.format,
        note=outcome.note,
    )
