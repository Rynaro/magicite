"""``route`` + ``load_skill_body`` (spec §3.3 tools 1-2). Both R0, read-mostly.

AC-024: this module (transitively, via ``core.router``) MUST NOT import
``magicite.storage.durable`` or ``magicite.engram.writer``.

S11 owns the public RouteDecision/1 + Plan/1 projection and the C10 body
disclosure gate (stale_decision / missing_context without procedure bytes).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from magicite.core import eligibility as eligibility_mod
from magicite.core import policy_store as policy_store_mod
from magicite.core import router as router_mod
from magicite.core import routing_policy as policy_mod
from magicite.core import trust as trust_mod
from magicite.core.context import ArtifactInventoryEntry, HostFact, ServerPermissionPolicy
from magicite.core.context import RouteContext as CoreRouteContext
from magicite.engram import parser as parser_mod
from magicite.errors import InvalidInputError, NotFoundError
from magicite.mcp.registry import ToolContext, magicite_tool
from magicite.mcp.schemas import (
    Candidate,
    ConfidenceOut,
    ExclusionSummaryOut,
    LoadSkillBodyInput,
    LoadSkillBodyOutput,
    PlanDiagnosticOut,
    PlanEdgeOut,
    PlanNodeOut,
    PlanOut,
    RouteContext,
    RouteInput,
    RouteOutput,
)

_L2_EXEC_WARNING = "exec blocks are returned as inert text; the HOST executes them, the server never does"

#: Supported public route schema selectors (C8). Unknown → fail closed.
_SUPPORTED_ROUTE_SCHEMA_VERSIONS = frozenset(
    {
        None,
        "RouteOutput/1",
        "RouteDecision/1",
        router_mod.ROUTE_DECISION_SCHEMA,
    }
)


def _empty_body(
    *,
    name: str,
    level: str,
    status: str,
    missing_context: list[str] | None = None,
    reason_codes: list[str] | None = None,
) -> LoadSkillBodyOutput:
    return LoadSkillBodyOutput(
        name=name,
        level=level,  # type: ignore[arg-type]
        procedure="",
        pitfalls="",
        examples=None,
        provenance=None,
        exec_blocks_present=False,
        exec_blocks_warning=None,
        truncated=False,
        next_offset=None,
        status=status,  # type: ignore[arg-type]
        missing_context=list(missing_context or []),
        reason_codes=list(reason_codes or []),
    )


def _safe_procedure_ref(ref: str | None) -> str | None:
    """Public procedure refs must not leak absolute filesystem paths."""
    if ref is None or ref == "":
        return None
    if ref.startswith("/") or (len(ref) > 2 and ref[1] == ":"):
        return None
    return ref


def _project_plan(
    plan: Any,
    *,
    authoritative_digest: str | None,
    disclose_nodes: bool,
) -> PlanOut | None:
    """Project Plan/1. Invalid/abstained composition discloses diagnostics only."""
    if not disclose_nodes:
        authoritative_digest = None
    if plan is None and authoritative_digest is None:
        return None
    if plan is None:
        return PlanOut(plan_digest=authoritative_digest, nodes=[], edges=[], topological_order=[])

    # Defense: local recomputation must equal router decision.plan_digest.
    if authoritative_digest is not None:
        computed = router_mod._plan_identity_digest(plan)
        if computed != authoritative_digest:
            raise InvalidInputError(
                "plan_digest mismatch between decision and composed Plan/1",
                details={
                    "reason": "plan_digest_mismatch",
                    "decision_plan_digest": authoritative_digest,
                    "computed_plan_digest": computed,
                },
                hint="re-run route; do not trust a stale plan projection",
            )

    diagnostics = [
        PlanDiagnosticOut(
            code=str(getattr(d, "code", "")),
            message=str(getattr(d, "message", "")),
            subject_id=getattr(d, "subject_id", None),
            related_ids=list(getattr(d, "related_ids", ()) or ()),
        )
        for d in (getattr(plan, "diagnostics", ()) or ())
    ]

    status = getattr(plan, "status", None)
    if not disclose_nodes:
        # B5: never project status=valid / executable=True when nodes are withheld
        # (e.g. dangling-after-valid-compose abstention that still carries Plan).
        projected_status: Literal["valid", "invalid"] | None
        if status == "valid":
            projected_status = "invalid"
        else:
            projected_status = status
        return PlanOut(
            status=projected_status,
            nodes=[],
            edges=[],
            topological_order=[],
            diagnostics=diagnostics,
            plan_digest=authoritative_digest,
            snapshot_id=getattr(plan, "snapshot_id", None),
            policy_id=getattr(plan, "policy_id", None),
            policy_digest=None,
            executable=False,
            schema_version=getattr(plan, "schema_version", None),
        )

    topo = list(getattr(plan, "topological_order", ()) or ())
    by_id = {str(getattr(n, "engram_id", "")): n for n in (getattr(plan, "nodes", ()) or ())}
    ordered_nodes = [by_id[eid] for eid in topo if eid in by_id]

    nodes: list[PlanNodeOut] = []
    for node in ordered_nodes:
        risk = getattr(node, "risk", None)
        risk_out: dict[str, Any] | None
        if risk is None:
            risk_out = None
        elif hasattr(risk, "model_dump"):
            risk_out = risk.model_dump()
        elif hasattr(risk, "__dict__"):
            risk_out = {
                k: getattr(risk, k)
                for k in ("filesystem", "subprocess", "network", "secrets")
                if hasattr(risk, k)
            }
        elif isinstance(risk, dict):
            risk_out = dict(risk)
        else:
            risk_out = {"repr": str(risk)}
        nodes.append(
            PlanNodeOut(
                engram_id=str(getattr(node, "engram_id", "")),
                version=int(getattr(node, "version", 0) or 0),
                content_digest=str(getattr(node, "content_digest", "") or ""),
                procedure_ref=_safe_procedure_ref(getattr(node, "procedure_ref", None)),
                required_permissions=sorted(getattr(node, "required_permissions", ()) or ()),
                risk=risk_out,
            )
        )
    edges = [
        PlanEdgeOut(
            type=str(getattr(edge, "type", "")),
            src_id=str(getattr(edge, "src_id", "")),
            dst_id=str(getattr(edge, "dst_id", "")),
            optional=bool(getattr(edge, "optional", False)),
            capability_id=getattr(edge, "capability_id", None),
        )
        for edge in (getattr(plan, "edges", ()) or ())
    ]
    return PlanOut(
        status=status,
        nodes=nodes,
        edges=edges,
        topological_order=topo,
        diagnostics=diagnostics,
        plan_digest=authoritative_digest,
        snapshot_id=getattr(plan, "snapshot_id", None),
        policy_id=getattr(plan, "policy_id", None),
        policy_digest=getattr(plan, "policy_digest", None),
        executable=getattr(plan, "executable", None),
        schema_version=getattr(plan, "schema_version", None),
    )


def _calibration_status(decision: router_mod.RouteDecision | None) -> str | None:
    if decision is None:
        return None
    if decision.confidence.value is not None and decision.confidence.calibration_id:
        return "calibrated"
    if decision.calibration_digest:
        return "artifact_present_uncalibrated_confidence"
    return "uncalibrated"


def project_route_output(outcome: router_mod.RouteOutcome) -> RouteOutput:
    """Map ``RouteOutcome`` + Plan/1 onto the public schema.

    ``decision.plan_digest`` is the sole authoritative plan digest (C4/C5).
    ``host_verification_report`` stays null — router/S08 do not produce it on
    the route path (HostVerificationReport/1 lives in evaluation fixtures).
    """
    decision = outcome.decision
    plan = outcome.plan
    authoritative_digest = decision.plan_digest if decision is not None else None

    # Disclose nodes only for a selected, valid, executable plan.
    disclose_nodes = bool(
        decision is not None
        and decision.status == "selected"
        and plan is not None
        and getattr(plan, "status", None) == "valid"
        and getattr(plan, "executable", False)
    )
    if not disclose_nodes:
        authoritative_digest = None
    plan_out = _project_plan(
        plan,
        authoritative_digest=authoritative_digest,
        disclose_nodes=disclose_nodes,
    )

    exclusions: list[ExclusionSummaryOut] = []
    if decision is not None:
        exclusions = [
            ExclusionSummaryOut(engram_id=e.engram_id, reason_codes=list(e.reason_codes))
            for e in decision.exclusions
        ]

    confidence: ConfidenceOut | None = None
    if decision is not None:
        confidence = ConfidenceOut(
            value=decision.confidence.value,
            calibration_id=decision.confidence.calibration_id,
        )

    public_status: str | None = None
    if decision is not None:
        public_status = decision.status

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
        unresolved_context=list(outcome.unresolved_context),
        decision_id=decision.decision_id if decision else None,
        status=public_status,  # type: ignore[arg-type]
        selected_ids=list(decision.selected_ids) if decision else [],
        exclusions=exclusions,
        score_components=dict(decision.score_components) if decision else {},
        confidence=confidence,
        reason_codes=list(decision.reason_codes) if decision else [],
        missing_context=list(decision.missing_context) if decision else [],
        truncations=dict(decision.truncations) if decision else {},
        policy_id=(decision.policy_id if decision else outcome.policy_id) or None,
        policy_digest=(decision.policy_digest if decision else outcome.policy_digest) or None,
        policy_family=(decision.policy_family if decision else outcome.policy_family) or None,
        policy_source=decision.policy_source if decision else None,
        default_local_authorship_policy=(
            decision.default_local_authorship_policy if decision else None
        ),
        config_digest=decision.config_digest if decision else None,
        calibration_digest=decision.calibration_digest if decision else None,
        calibration_status=_calibration_status(decision),
        registry_digest=decision.registry_digest if decision else None,
        schema_digest=decision.schema_digest if decision else None,
        model_digest=decision.model_digest if decision else None,
        tokenizer_digest=decision.tokenizer_digest if decision else None,
        index_generation_id=decision.index_generation_id if decision else None,
        snapshot_id=decision.snapshot_id if decision else None,
        selected_content_digests=(
            dict(decision.selected_content_digests) if decision else {}
        ),
        selection_mechanism=decision.selection_mechanism if decision else None,
        propensity=dict(decision.propensity) if decision else {},
        explanation_version=decision.explanation_version if decision else None,
        operational_error=decision.operational_error if decision else None,
        fallback_identity=decision.fallback_identity if decision else None,
        decision_schema_version=decision.schema_version if decision else None,
        plan=plan_out,
        plan_digest=authoritative_digest,
        host_verification_report=None,
    )


def _active_policy_digest(cfg: Any) -> str | None:
    try:
        manifest = policy_store_mod.get_active_manifest(cfg)
    except InvalidInputError as exc:
        raise InvalidInputError(
            "policy store unavailable during body disclosure",
            details={
                "reason": "stale_decision",
                "policy_store_error": "policy_store_corrupt",
            },
            hint=str(exc),
        ) from exc
    if manifest is not None:
        return (
            policy_mod.bind_server_ceiling_digest(manifest.policy_digest, cfg)
            if manifest.policy_digest
            else policy_mod.compute_policy_digest(manifest.policy_id, cfg)
        )
    return policy_mod.compute_policy_digest(policy_mod.resolve_policy_id(cfg), cfg)


def _current_snapshot_id(conn: Any) -> str | None:
    """Resolve the live index snapshot the same way the router pins it (C10)."""
    _gen_id, snap_id, _schema_d, _tok_d, _reasons = router_mod.pin_index_identity(conn)
    return snap_id


def typed_route_context(context: RouteContext | None) -> CoreRouteContext:
    if context is None:
        return CoreRouteContext()
    return CoreRouteContext(
        languages=context.languages,
        frameworks=context.frameworks,
        package_managers=context.package_managers,
        platform=context.platform,
        host=HostFact(**context.host.model_dump()) if context.host else None,
        capabilities=context.capabilities,
        permission_grants=frozenset(context.permission_grants)
        if context.permission_grants is not None
        else None,
        allowed_tools=frozenset(context.allowed_tools) if context.allowed_tools is not None else None,
        artifact_inventory=tuple(ArtifactInventoryEntry(**a.model_dump()) for a in context.artifact_inventory)
        if context.artifact_inventory is not None
        else None,
        excluded_engram_ids=frozenset(context.excluded_engram_ids),
    )


def legacy_route_context(context: RouteContext | None) -> dict[str, Any] | None:
    return context.model_dump(include={"project_tag", "recent_failures", "user_prefs"}) if context else None


def validate_route_version(params: RouteInput) -> None:
    if params.schema_version not in _SUPPORTED_ROUTE_SCHEMA_VERSIONS:
        raise InvalidInputError("unsupported route schema version")


def _server_policy(cfg: Any, policy_digest: str) -> ServerPermissionPolicy:
    return ServerPermissionPolicy(
        allowed_permissions=frozenset(getattr(cfg, "allowed_permissions", ()) or ()),
        allowed_tools=frozenset(getattr(cfg, "allowed_tools", ()) or ()),
        policy_digest=policy_digest,
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
    validate_route_version(params)
    context_dict = legacy_route_context(params.context)
    outcome = router_mod.route(
        ctx.cfg,
        ctx.conn,
        ctx.embedder,
        query=params.query,
        context=context_dict,
        route_context=typed_route_context(params.context),
        server_policy=_server_policy(ctx.cfg, _active_policy_digest(ctx.cfg) or "policy-unavailable"),
        k=params.k,
        session_id=params.session_id,
    )
    return project_route_output(outcome)


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


def _refuse_stale(name: str, level: str, *, codes: list[str] | None = None) -> LoadSkillBodyOutput:
    return _empty_body(
        name=name,
        level=level,
        status="stale_decision",
        reason_codes=list(codes or ["stale_decision"]),
    )


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
        "SELECT id, path, name, content_sha256, status, verification_status, origin FROM engram "
        "WHERE id = ? OR name = ?",
        (params.name, params.name),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no engram named or id'd {params.name!r}")

    engram_id = str(row["id"])
    live_digest = str(row["content_sha256"] or "")

    # C2/C10: old clients that omit digests get missing_context — never a silent bypass.
    missing: list[str] = []
    if not params.expected_content_digest:
        missing.append("expected_content_digest")
    if not params.expected_policy_digest:
        missing.append("expected_policy_digest")
    if missing or not params.expected_policy_digest:
        return _empty_body(
            name=row["name"],
            level=params.level,
            status="missing_context",
            missing_context=missing,
            reason_codes=["missing_context"],
        )
    expected_policy_digest: str = params.expected_policy_digest

    if live_digest != params.expected_content_digest:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "content_digest_drift"])

    try:
        disclosure_snapshot = trust_mod.authenticated_snapshot(ctx.cfg)
        decisions = {key: trust_mod.TrustDecision.from_dict(value)
                     for key, value in disclosure_snapshot.latest_by_engram.items()}
        trust_policy = trust_mod.TrustPolicy.from_dict(disclosure_snapshot.policy)
        trust_view = router_mod._route_trust_view(
            ctx.cfg, row, cached_decision=decisions.get(engram_id), cached_policy=trust_policy
        )
    except trust_mod.TrustLedgerCorruptError:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "trust_unavailable"])
    except InvalidInputError:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "trust_unavailable"])

    if trust_view.content_digest != params.expected_content_digest:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "trust_digest_drift"])

    if not trust_view.admitted or trust_view.quarantined or not trust_view.origin_trusted:
        return _refuse_stale(
            row["name"],
            params.level,
            codes=["stale_decision", "not_admitted"],
        )

    active_policy = _active_policy_digest(ctx.cfg)
    if active_policy != expected_policy_digest:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "policy_digest_drift"])

    if params.expected_snapshot_id is not None:
        current_snapshot = _current_snapshot_id(ctx.conn)
        # Fail closed when the pin cannot be resolved or digests diverge (C10).
        if current_snapshot is None or current_snapshot != params.expected_snapshot_id:
            return _refuse_stale(
                row["name"],
                params.level,
                codes=["stale_decision", "snapshot_drift"],
            )

    # Re-evaluate typed facts and operator ceiling on every disclosure. A supplied
    # digest is an identity pin, not proof that compatibility or grants still hold.
    route_ctx = typed_route_context(params.context)
    policy_digest = expected_policy_digest
    server_policy = _server_policy(ctx.cfg, policy_digest)
    file_path = Path(ctx.cfg.project_root) / row["path"]
    elig_subject: eligibility_mod.EligibilitySubject
    try:
        raw_text = file_path.read_text(encoding="utf-8")
        artifact, _doc = parser_mod.parse_artifact(
            raw_text,
            relpath=str(row["path"]),
            registry_root=Path(ctx.cfg.project_root),
            admit=False,
            require_asset_files=False,
        )
        from magicite.engram.model_v1 import EngramFrontmatterV1, EngramV1

        if isinstance(artifact, (EngramV1, EngramFrontmatterV1)):
            elig_subject = eligibility_mod.subject_from_engram(artifact)
        else:
            elig_subject = eligibility_mod.EligibilitySubject(
                id=engram_id, version=int(getattr(artifact, "version", 1) or 1)
            )
    except Exception:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "body_unavailable"])

    # Bind disclosure to the same bytes parsed above, including a concurrent edit
    # after the trust view was captured. Never reopen the path for rendering.
    if artifact.content_sha256 != params.expected_content_digest:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "content_digest_drift"])

    elig = eligibility_mod.evaluate_eligibility(
        elig_subject,
        route_ctx,
        trust_view,
        server_policy,
        path="body",
        expected_content_digest=params.expected_content_digest,
    )
    if not elig.eligible:
        if elig.context_required:
            return _empty_body(
                name=row["name"],
                level=params.level,
                status="missing_context",
                missing_context=list(elig.missing_fields),
                reason_codes=list(elig.reason_codes),
            )
        return _refuse_stale(row["name"], params.level, codes=list(elig.reason_codes))

    body = artifact.body
    _, body_text = parser_mod.split_frontmatter(raw_text)
    # Preserve original prose order instead of reconstructing only numbered steps.
    # Keep executable blocks out of L2, as in the legacy body adapter.
    procedure_text = "\n".join(
        parser_mod._split_sections(parser_mod._EXEC_BLOCK_RE.sub("", body_text)).get("procedure", [])
    )
    sections: list[tuple[str, str]] = [
        ("procedure", procedure_text),
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
        partial = text_bytes[local_start : local_start + remaining].decode("utf-8", errors="ignore")
        rendered[name] = partial
        consumed += len(partial.encode("utf-8"))
        stream_offset = section_end

    total_bytes = sum(len(text.encode("utf-8")) for _, text in sections)
    next_offset = min(params.cursor + consumed, total_bytes)
    truncated = next_offset < total_bytes

    exec_present = bool(body.exec_blocks) and params.level == "L3"

    try:
        if trust_mod.authenticated_snapshot(ctx.cfg).head != disclosure_snapshot.head:
            return _refuse_stale(row["name"], params.level, codes=["stale_decision", "trust_head_drift"])
    except trust_mod.TrustLedgerCorruptError:
        return _refuse_stale(row["name"], params.level, codes=["stale_decision", "trust_unavailable"])

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
        status="ok",
    )
