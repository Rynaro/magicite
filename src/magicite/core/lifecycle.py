"""The engram lifecycle FSM (spec §5.1): the ``status`` transition ladder,
the verification-status trust gate, and the DB-backed evidence gathering
``core/fitness.py``'s pure gate functions need but deliberately do not read
themselves.

**M4 shipped** :func:`apply` -- the narrow, pure evidence-bar guard AC-016
needs (unchanged below; every M4 test in ``tests/unit/core/test_lifecycle
.py`` still calls it with the same signature). **M5 adds** everything
around it: :func:`initial_verification_status` (the M5 security fix --
"verification status must be server-assigned, never read from
caller-authored content", see ``core/registry.py::_ingest_one``'s call
site), :func:`evaluate_upward_transition`/:func:`evaluate_revival` (the
DB-and-file-reading counterparts of :func:`apply` that
``mcp/bind_lifecycle.py::promote()`` actually calls), and
:func:`execute_sharpen` (spec §5.4's patch-apply-relint-reversion
semantics).

**Layering note.** ``core/fitness.py`` stays pure by design (its own
docstring: "nothing here reads a live DB row"); this module is where the
DB/file reads happen, evidence snapshots get built, and the fitness gates
get called. ``storage/durable.py::set_status()``/``set_verification_
status()`` remain guard-free primitives (the same convention M4's
``mark_archived()`` already established: the caller validates, the
storage function only writes) -- so the guard evaluation here always runs
*before* any caller reaches for those primitives, never inside them. This
is a deliberate, disclosed deviation from spec §5.1's literal "storage/
durable.py::set_status() calls it [apply()] first" phrasing, chosen for
consistency with the already-shipped ``mark_archived()`` precedent rather
than introducing a second, contradictory convention.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from magicite.config import Config
from magicite.core import fitness as fitness_mod
from magicite.core.decay_math import effective_value
from magicite.engram import ids as ids_mod
from magicite.engram import lint as lint_mod
from magicite.engram import parser as parser_mod
from magicite.engram import writer as writer_mod
from magicite.engram.lint import InjectionScanResult
from magicite.engram.model import PitfallEntry, ProcedureStep, ProvenanceJournalEntry, VerificationStatus
from magicite.errors import LintFailedError, NotFoundError, TransitionDeniedError
from magicite.storage import durable as durable_mod
from magicite.storage import lease as lease_mod

#: (from_status, to_status) pairs this minimal slice knows how to guard
#: (spec §5.1's FSM table, the three S/evidence-driven upward transitions
#: -- the ones AC-016's "evidence bar" language is about). Each fitness
#: gate has a deliberately different signature (spec §7.1: "each docs/07
#: gate at boundary values"), so dispatch is explicit per pair rather than
#: a single uniform ``gate(evidence, **kwargs)`` call.
_KNOWN_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {("nascent", "probation"), ("probation", "consolidated"), ("consolidated", "promoted")}
)


def apply(
    *,
    from_status: str,
    to_status: str,
    evidence: fitness_mod.Evidence,
    cfg: Config | None = None,
    **gate_kwargs: object,
) -> None:
    """Raise :class:`TransitionDeniedError` (naming the unmet guards) unless
    the ``(from_status, to_status)`` evidence bar is cleared. Returns
    ``None`` on success -- this function never mutates anything; it is
    the guard check the full FSM below calls before it does."""
    key = (from_status, to_status)
    if key not in _KNOWN_TRANSITIONS:
        raise ValueError(f"no evidence gate registered for {from_status!r} -> {to_status!r}")

    if key == ("nascent", "probation"):
        unmet = fitness_mod.nascent_to_probation_gate(**gate_kwargs)  # type: ignore[arg-type]
    elif key == ("probation", "consolidated"):
        unmet = fitness_mod.probation_to_consolidated_gate(evidence, cfg or Config())
    else:
        unmet = fitness_mod.consolidated_to_promoted_gate(evidence, **gate_kwargs)  # type: ignore[arg-type]

    if unmet:
        raise TransitionDeniedError(
            f"{from_status} -> {to_status} denied: evidence bar not cleared",
            unmet=unmet,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── the verification_status trust gate (M5 security fix #1) ─────────────


def initial_verification_status(
    *, origin: str, lint_ok: bool, scan: InjectionScanResult
) -> VerificationStatus:
    """The **only** legitimate producer of a fresh engram's
    ``verification_status``. ``core.registry.register()`` calls this for
    every ingested engram (native ``.egr.md`` and SKILL.md import alike,
    since the SKILL.md path also funnels through ``_ingest_one``) instead
    of ever reading ``engram.frontmatter.trust.verification_status`` --
    that field is caller-authored content (docs/06: "Injection-Surface
    Analysis... never trust"), and a caller declaring ``verified`` in its
    own frontmatter must never be taken at its word (the M5 security fix:
    an adversarial import claiming ``verification_status: verified``
    would otherwise become immediately routable, contradicting docs/06's
    trust gate).

    Priority order matches spec §5.1's FSM ("any -> quarantined ...
    none (safety direction is free)" outranks every other row):

    1. the injection scan flagged it -> ``quarantined``, unconditionally.
    2. ``origin`` is ``imported``/``distilled`` (docs/06 tiers 3-4: "Weak"/
       "Medium" trust, "must pass quarantine-on-import gate; promotion
       deferred") -> ``pending``, always -- reaching ``verified`` requires
       the explicit, manual ``pending -> verified`` review path (spec
       §5.1's own FSM row), which v1's tool surface does not yet expose
       (documented gap, ``docs/operations.md``).
    3. strict lint did not cleanly pass -> ``pending`` (defensive; in
       practice ``register()`` never reaches this branch for a ``strict``-
       profile failure, since that already aborts before any DB write).
    4. otherwise (``authored``/``sharpened``, lint-clean, scan-clean) ->
       ``verified`` -- docs/06's own definition of the state ("Passed all
       trust gates (quality check, lint, no injection risk)") independently
       re-derived by the server, never copied from the file.
    """
    if scan.quarantine_recommended:
        return "quarantined"
    if origin in ("imported", "distilled"):
        return "pending"
    if not lint_ok:
        return "pending"
    return "verified"


# ── DB-backed evidence gathering (core/fitness.py stays pure; this doesn't) ──


def _pass_rate(success: int, failure: int) -> float:
    total = success + failure
    return (success / total) if total else 0.0


def _distinct_sessions_for(conn: sqlite3.Connection, engram_id: str, *, since: str | None = None) -> int:
    """Best-effort proxy for spec §5.1's "distinct_sessions": the engram
    table carries no durable per-session counter (only aggregate success/
    failure counts), so this counts distinct ``eph_event.session_id``
    values recorded for the engram -- a Tier-C ledger, retained for
    ``cfg.retention_days`` (default 30) and then purged by Dream phase 3.
    This is therefore a *recency-windowed* approximation, not a permanent
    durable count; a durable ``distinct_sessions`` column is a reasonable
    M6+ follow-up flagged in the M5 report, not built here."""
    if since:
        row = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS n FROM eph_event "
            "WHERE engram_id = ? AND session_id IS NOT NULL AND ts > ?",
            (engram_id, since),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS n FROM eph_event "
            "WHERE engram_id = ? AND session_id IS NOT NULL",
            (engram_id,),
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def _recent_valences(conn: sqlite3.Connection, engram_id: str, *, limit: int = 5) -> tuple[float, ...]:
    rows = conn.execute(
        "SELECT capture_valence FROM eph_tag WHERE engram_id = ? AND subject_kind = 'node' "
        "AND captured_at IS NOT NULL ORDER BY captured_at DESC LIMIT ?",
        (engram_id, limit),
    ).fetchall()
    return tuple(
        float(r["capture_valence"]) for r in reversed(rows) if r["capture_valence"] is not None
    )


def gather_evidence(conn: sqlite3.Connection, cfg: Config, engram_row: sqlite3.Row) -> fitness_mod.Evidence:
    """Build a :class:`fitness.Evidence` snapshot from live DB state for
    ``engram_row`` (a full ``SELECT * FROM engram WHERE ...`` row).
    ``storage_strength`` is the *effective* (decayed) value, matching how
    every other read path (``core/decay.py``, ``core/router.py``) treats
    S -- lazy decay, materialised only by Dream (spec §6.1)."""
    now = _now()
    s_eff = effective_value(
        float(engram_row["storage_strength"]), engram_row["s_decayed_at"], now, cfg.lambda_s_per_day
    )
    return fitness_mod.Evidence(
        storage_strength=s_eff,
        pass_rate=_pass_rate(int(engram_row["success_count"]), int(engram_row["failure_count"])),
        distinct_sessions=_distinct_sessions_for(conn, str(engram_row["id"])),
        recent_valences=_recent_valences(conn, str(engram_row["id"])),
    )


# ── the full upward ladder (spec §5.1) ───────────────────────────────────

#: nascent -> probation -> consolidated -> promoted. ``archived ->
#: probation`` (revival) is handled separately (:func:`evaluate_revival`):
#: it is "manual, always" (never auto-executed, even under autonomous
#: mode) and uses a different evidence shape (sessions *since archival*,
#: not lifetime evidence).
UPWARD_TRANSITIONS: dict[str, str] = {
    "nascent": "probation",
    "probation": "consolidated",
    "consolidated": "promoted",
}


@dataclass(frozen=True)
class UpwardCheck:
    from_status: str
    to_status: str | None  # None => no upward transition is defined from from_status
    unmet: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.to_status is not None and not self.unmet


def check_injection_scan(project_root: Path, engram_row: sqlite3.Row) -> lint_mod.InjectionScanResult:
    """spec §5.1's "any -> quarantined" row is unconditional on the
    engram's current ``status`` -- unlike the upward ladder below, which
    only re-scans as a *side effect* of evaluating the nascent branch's
    own guard. ``mcp/bind_lifecycle.py::promote()`` calls this first, for
    every status, so a `draft`/`probation`/`consolidated` engram whose
    on-disk content was edited to add an exec block after registration is
    still caught -- not only a `nascent` one mid-evaluation of a different
    gate entirely."""
    file_path = project_root / str(engram_row["path"])
    parsed = parser_mod.parse_file(file_path, registry_root=project_root)
    return lint_mod.injection_scan(parsed.engram)


def evaluate_upward_transition(
    conn: sqlite3.Connection,
    cfg: Config,
    project_root: Path,
    engram_row: sqlite3.Row,
    *,
    scan: lint_mod.InjectionScanResult | None = None,
) -> UpwardCheck:
    """The DB/file-reading counterpart of :func:`apply`: resolves which
    upward transition (if any) applies to ``engram_row``'s current status,
    gathers the evidence that transition's gate needs, and evaluates it.
    Never mutates anything (same discipline as :func:`apply`) -- the
    caller (``mcp/bind_lifecycle.py::promote()``) decides what to do with
    the result. ``scan``, if the caller already ran
    :func:`check_injection_scan` (as ``promote()`` always does, spec §5.1
    "any -> quarantined" being unconditional on status), is reused instead
    of re-parsing the file a second time for the nascent branch below."""
    from_status = str(engram_row["status"])
    to_status = UPWARD_TRANSITIONS.get(from_status)
    if to_status is None:
        return UpwardCheck(
            from_status,
            None,
            unmet=[f"no upward lifecycle transition is defined from status {from_status!r}"],
        )

    if from_status == "nascent":
        # spec §5.1 guard: "reconstruction_ok∨n/a ∧ rubric≥8/12 ∧
        # injection_scan_clean". Reconstruction ships for distilled-origin
        # engrams only (spec §7.3); v1 has no distilled engrams yet
        # (nucleate()/core.distill lands in M6), so it is `n/a` (True) for
        # every origin this milestone can actually produce.
        file_path = project_root / str(engram_row["path"])
        parsed = parser_mod.parse_file(file_path, registry_root=project_root)
        engram = parsed.engram
        if scan is None:
            scan = lint_mod.injection_scan(engram)
        rubric = fitness_mod.structural_rubric_score(engram)
        scan_clean = not scan.quarantine_recommended
        unmet = fitness_mod.nascent_to_probation_gate(rubric_score=rubric, injection_scan_clean=scan_clean)
        evidence: dict[str, Any] = {
            "rubric_score": rubric,
            "rubric_min": 8,
            "injection_scan_clean": scan_clean,
            "quarantine_recommended": scan.quarantine_recommended,
        }
        return UpwardCheck(from_status, to_status, unmet, evidence)

    snapshot = gather_evidence(conn, cfg, engram_row)
    if from_status == "probation":
        unmet = fitness_mod.probation_to_consolidated_gate(snapshot, cfg)
    else:  # "consolidated" -> "promoted"
        raw_s = float(engram_row["storage_strength"])
        no_decay = snapshot.storage_strength >= raw_s - cfg.epsilon_write
        unmet = fitness_mod.consolidated_to_promoted_gate(snapshot, no_evidence_decay=no_decay)

    evidence = {
        "storage_strength": round(snapshot.storage_strength, 4),
        "pass_rate": round(snapshot.pass_rate, 4),
        "distinct_sessions": snapshot.distinct_sessions,
        "recent_valences": list(snapshot.recent_valences),
    }
    return UpwardCheck(from_status, to_status, unmet, evidence)


def evaluate_revival(conn: sqlite3.Connection, engram_row: sqlite3.Row) -> UpwardCheck:
    """spec §5.1: "archived -> probation | >=3 new successful sessions
    after a revival request | promote() | manual, always". "Successful"
    sessions since archival are approximated the same way
    :func:`_distinct_sessions_for` approximates lifetime sessions --
    distinct ``eph_event.session_id`` values recorded for this engram
    *after* it was archived (``engram.updated_at``, the timestamp
    ``storage.durable.mark_archived`` stamps)."""
    since = str(engram_row["updated_at"])
    sessions = _distinct_sessions_for(conn, str(engram_row["id"]), since=since)
    unmet = [] if sessions >= 3 else [f"distinct_sessions_since_archival {sessions} < 3"]
    return UpwardCheck("archived", "probation", unmet, {"distinct_sessions_since_archival": sessions})


# ── sharpen() execution semantics (spec §5.4) ────────────────────────────


@dataclass(frozen=True)
class SharpenExecutionResult:
    new_version: int
    changes: list[str]


def execute_sharpen(
    cfg: Config,
    conn: sqlite3.Connection,
    embedder: Any,
    *,
    name: str,
    proposed_changes: Any,
    actor: str,
) -> SharpenExecutionResult:
    """spec §5.4: re-read the file from disk (the file, not the DB, is the
    base) -> apply the patch -> re-lint -> version+=1 + journal -> atomic
    write -> re-embed. Raises :class:`LintFailedError` with the file left
    completely untouched if the patched document fails a strict re-lint
    ("fail -> approval state 'failed', file untouched, reason returned;
    nothing is half-applied") -- the caller (``mcp/bind_lifecycle.py``)
    maps that onto the approval's ``failed`` state. plasticity/synapses
    are never reassigned here (copied through byte-for-byte, spec §5.4
    step 6: "sharpening is authored state, never learned state").

    Deferred (documented gap, not built): §5.4 step 7's cross-run
    "sharpen_regression" detection -- it requires comparing a step's
    confidence across two Dream runs, which is a Dream-side follow-up, not
    something a single ``sharpen()`` call can evaluate.
    """
    # local imports: core.registry is the ingestion *orchestrator* and
    # importing it at module scope would make core.lifecycle depend on the
    # module that will, in a future milestone, plausibly want to depend on
    # lifecycle's FSM too -- keeping the coupling import-local avoids
    # baking in a direction that is not load-bearing today.
    from magicite.core import registry as registry_mod

    project_root = cfg.project_root.resolve()
    row = conn.execute(
        "SELECT path, verification_status FROM engram WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no engram named {name!r}")
    file_path = project_root / str(row["path"])
    # M5 security fix #1, closing a sharpen()-shaped regression of it: the
    # file's own `trust.verification_status` is caller-authored content and
    # must never be trusted (same rule `_ingest_one` enforces at register()
    # time) -- carry the DB's already-server-computed value through the
    # re-parse instead of letting a stale/forged on-disk value overwrite it
    # on the next `upsert_engram` below.
    current_verification_status = str(row["verification_status"])

    parsed = parser_mod.parse_file(file_path, registry_root=project_root)
    engram = parsed.engram
    if engram.frontmatter.trust is None:
        from magicite.engram.model import Trust

        engram.frontmatter.trust = Trust(verification_status=current_verification_status)  # type: ignore[arg-type]
    else:
        engram.frontmatter.trust = engram.frontmatter.trust.model_copy(
            update={"verification_status": current_verification_status}
        )
    changes: list[str] = []

    if proposed_changes is not None:
        for step_text in getattr(proposed_changes, "procedures", None) or []:
            next_no = max((s.step_no for s in engram.body.procedure), default=0) + 1
            engram.body.procedure.append(ProcedureStep(step_no=next_no, text=step_text))
            changes.append(f"procedure step {next_no} added")

        existing_triggers = {t.lower() for t in engram.frontmatter.triggers.positive}
        for trigger_text in getattr(proposed_changes, "triggers", None) or []:
            if trigger_text.lower() not in existing_triggers:
                engram.frontmatter.triggers.positive.append(trigger_text)
                existing_triggers.add(trigger_text.lower())
                changes.append(f"trigger added: {trigger_text!r}")

        for pitfall_text in getattr(proposed_changes, "pitfalls", None) or []:
            engram.body.pitfalls.append(PitfallEntry(text=pitfall_text, count=1))
            changes.append("pitfall added")

    lint_profile = "import" if engram.frontmatter.provenance == "imported" else "strict"
    lint_result = lint_mod.lint(engram, profile=lint_profile)  # type: ignore[arg-type]
    if lint_profile == "strict" and not lint_result.ok:
        msg = "; ".join(f"{i.rule}: {i.message}" for i in lint_result.errors)
        raise LintFailedError(f"sharpen() produced an invalid document for {name!r}: {msg}")

    old_version = engram.frontmatter.version
    new_version = old_version + 1
    engram.frontmatter.version = new_version
    engram.frontmatter.provenance_journal = [
        *engram.frontmatter.provenance_journal,
        ProvenanceJournalEntry(
            version=new_version,
            timestamp=_now(),
            author=actor,
            event="sharpened",
            base_version=old_version,
            summary_of_change="; ".join(changes) if changes else "no-op sharpen (no proposed_changes)",
        ),
    ]
    engram.body_sha256 = ids_mod.body_sha256(writer_mod.render_body(engram.body))

    # M5 data-integrity fix (same defect class as register()/sync()/
    # export(), spec §4.2): sharpen()'s execution is a third independent
    # writer of durable engram state and must not interleave with a
    # running Dream cycle -- the cross-process lease, not just G2's
    # in-process one.
    cross_lease = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path, conn=conn, holder=f"sharpen:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    )
    with cross_lease.acquire(), lease_mod.writer_lease(holder="sharpen"):
        # NOT `parsed.frontmatter_doc`: that round-trip carrier's own
        # ``render_frontmatter`` only ever refreshes version/plasticity/
        # synapses onto it (the Dream-checkpoint use case, where nothing
        # else about the document changed) -- reusing it here would
        # silently drop the very triggers/procedure/pitfalls patch this
        # function exists to apply. A fresh render (spec §2.5's
        # deterministic renderer, still byte-reproducible, AC-021) is the
        # correct choice for an operation that legitimately changes
        # content; it costs the pre-existing file's YAML comments, an
        # acceptable, disclosed trade-off for a sharpen -- unlike a
        # checkpoint, this is not supposed to look untouched.
        rendered = writer_mod.render_document(engram, None)
        engram.content_sha256 = ids_mod.content_sha256(rendered.encode("utf-8"))
        writer_mod.atomic_write(file_path, rendered)

        identity = registry_mod.identity_hash(engram)  # CR-8: drift-only, `id` itself never recomputed
        durable_mod.upsert_engram(conn, engram, identity_sha256=identity)
        durable_mod.wire_context_affinity(conn, engram)
        durable_mod.wire_declared_edges(conn, engram)
        registry_mod.embed_and_store(conn, embedder, engram)

    return SharpenExecutionResult(new_version=new_version, changes=changes)
