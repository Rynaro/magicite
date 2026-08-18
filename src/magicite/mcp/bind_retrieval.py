"""``route`` + ``load_skill_body`` (spec §3.3 tools 1-2). Both R0, read-mostly.

AC-024: this module (transitively, via ``core.router``) MUST NOT import
``magicite.storage.durable`` or ``magicite.engram.writer``.
"""

from __future__ import annotations

from pathlib import Path

from magicite.core import router as router_mod
from magicite.engram import parser as parser_mod
from magicite.errors import NotFoundError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    Candidate,
    LoadSkillBodyInput,
    LoadSkillBodyOutput,
    RouteInput,
    RouteOutput,
)

_L2_EXEC_WARNING = (
    "exec blocks are returned as inert text; the HOST executes them, the server never does"
)


@magicite_tool(
    risk="R0",
    side_effect="none",
    signal_tier=0,
    idempotent=True,
    read_only=True,
    input_model=RouteInput,
    output_model=RouteOutput,
    description="Ranked skill candidates + composition plan for a query.",
)
def route(ctx: ToolContext, params: RouteInput) -> RouteOutput:
    context_dict = params.context.model_dump() if params.context else None
    outcome = router_mod.route(
        ctx.cfg,
        ctx.conn,
        ctx.embedder,
        query=params.query,
        context=context_dict,
        k=params.k,
        session_id=params.session_id,
    )
    return RouteOutput(
        candidates=[
            Candidate(
                rank=c.rank,
                id=c.id,
                name=c.name,
                intent_does=c.intent_does,
                intent_use_when=c.intent_use_when,
                score=c.score,
                status=c.status,
                exposure_count=c.exposure_count,
                body_ref=c.body_ref,
                signal_tier_0=c.signal_tier_0,
                diagnostics=c.diagnostics,
            )
            for c in outcome.candidates
        ],
        composition_plan=outcome.composition_plan,
        plan_confidence=outcome.plan_confidence,
        instructions=outcome.instructions,
        session_id=outcome.session_id,
        registry_size=outcome.registry_size,
        unresolved_context=outcome.unresolved_context,
    )


def _render_steps(steps: list) -> str:
    lines = []
    for step in sorted(steps, key=lambda s: s.step_no):
        lines.append(f"{step.step_no}. {step.text}")
    return "\n".join(lines)


def _render_pitfalls(pitfalls: list) -> str:
    return "\n".join(f"- {p.text}" for p in pitfalls)


def _render_examples(examples: list) -> str:
    return "\n".join(f"{'+' if e.positive else '-'} {e.text}" for e in examples)


def _render_provenance(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


@magicite_tool(
    risk="R0",
    side_effect="none",
    signal_tier=0,
    idempotent=True,
    read_only=True,
    input_model=LoadSkillBodyInput,
    output_model=LoadSkillBodyOutput,
    description="Progressive-disclosure body read for hosts without direct filesystem access.",
)
def load_skill_body(ctx: ToolContext, params: LoadSkillBodyInput) -> LoadSkillBodyOutput:
    row = ctx.conn.execute(
        "SELECT path, name FROM engram WHERE id = ? OR name = ?", (params.name, params.name)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no engram named or id'd {params.name!r}")

    file_path = Path(ctx.cfg.project_root) / row["path"]
    parsed = parser_mod.parse_file(file_path, registry_root=Path(ctx.cfg.project_root))
    body = parsed.engram.body

    sections: list[tuple[str, str]] = [
        ("procedure", _render_steps(body.procedure)),
        ("pitfalls", _render_pitfalls(body.pitfalls)),
    ]
    if params.level == "L3":
        sections.append(("examples", _render_examples(body.examples)))
        sections.append(("provenance", _render_provenance(body.provenance_lines)))

    budget = params.max_bytes
    rendered: dict[str, str | None] = {name: None for name, _ in sections}
    consumed = 0
    stream_offset = 0
    for name, text in sections:
        text_bytes = text.encode("utf-8")
        section_end = stream_offset + len(text_bytes)
        if section_end <= params.cursor:
            stream_offset = section_end
            continue
        if consumed >= budget:
            break
        local_start = max(params.cursor - stream_offset, 0)
        remaining = budget - consumed
        partial = text_bytes[local_start : local_start + remaining].decode(
            "utf-8", errors="ignore"
        )
        rendered[name] = partial
        consumed += len(partial.encode("utf-8"))
        stream_offset = section_end

    total_bytes = sum(len(text.encode("utf-8")) for _, text in sections)
    next_offset = min(params.cursor + consumed, total_bytes)
    truncated = next_offset < total_bytes

    exec_present = bool(body.exec_blocks) and params.level == "L3"

    return LoadSkillBodyOutput(
        name=row["name"],
        level=params.level,
        procedure=rendered.get("procedure") or "",
        pitfalls=rendered.get("pitfalls") or "",
        examples=rendered.get("examples"),
        provenance=rendered.get("provenance"),
        exec_blocks_present=exec_present,
        exec_blocks_warning=_L2_EXEC_WARNING if exec_present else None,
        truncated=truncated,
        next_offset=next_offset if truncated else None,
    )
