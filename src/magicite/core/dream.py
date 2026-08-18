"""The Dream consolidation worker (spec §4): seven phases, run-level
idempotency, the ``synapses:``/``plasticity:`` checkpoint procedure, and
the enqueue/dedup machinery ``consolidate()``/``session_end()`` share.

**Where each phase's input actually comes from (read this before touching
Phase 2).** Phase 2 (Potentiate) computes Δw for ``engram.storage_strength``
and ``edge.storage_strength`` **exclusively** from captured, unconsumed
``eph_tag`` rows (``storage.ephemeral.pending_captured_node_tags``/
``pending_captured_edge_tags``) -- never from ``eph_event``. This is
deliberate, not an oversight: ``eph_tag`` capture is gated by
``core/signals.py``'s per-skill-per-session cap (``cfg.
per_skill_session_cap``, R1's poisoning-cost bound) at *set* time;
``eph_event`` is the Tier-0 passive-inference ledger (spec §3.3), uncapped
by design (every tool call appends one row), and spec's own Tier gate
(AC-014, ``core/plasticity.py::apply()``) already refuses Tier-0 deltas
against S outright. Mining ``eph_event`` for S would route *around* both
of those defenses at once. ``tests/unit/core/test_dream.py::
test_eph_event_flooding_cannot_move_storage_strength`` proves this
directly: 100 unauthenticated, uncapped ``eph_event`` rows with
``valence=+1.0`` move nothing.

**Run-level idempotency (AC-020).** A run is a pure function of
``(durable state, captured-but-unconsumed eph_tag rows)``. Phase 2 marks
every tag it reads ``consumed_run_id=<this run>`` (``storage.ephemeral.
mark_tag_consumed``); a second run with zero new captures therefore finds
zero pending tags, computes zero Δw, materialises zero S/R drift beyond
what decay's own epsilon gate already suppressed, and Phase 7's dirty-set
comparison (render the current DB-state candidate, diff its bytes against
the file already on disk) finds zero engrams dirty -- zero files written.
Determinism (AC-021) falls out of the same design: Phase 7's candidate
render uses ``engram.writer.render_document`` (already byte-deterministic,
spec §2.5), and every phase before it processes rows in a fixed order
(``ORDER BY captured_at, id``), so a re-run over the identical input
produces the identical output.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from magicite.config import Config
from magicite.core import audit as audit_mod
from magicite.core import decay as decay_mod
from magicite.core import distill as distill_mod
from magicite.core import plasticity as plasticity_mod
from magicite.engram import ids as ids_mod
from magicite.engram import parser as parser_mod
from magicite.engram import writer as writer_mod
from magicite.engram.model import (
    Engram,
    EngramStatus,
    Outcome,
    Plasticity,
    ProvenanceJournalEntry,
    Synapse,
)
from magicite.errors import BusyError, NotFoundError, TransitionDeniedError
from magicite.storage import ephemeral as ephemeral_mod
from magicite.storage import lease as lease_mod


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_run_id() -> str:
    return f"dream_{uuid.uuid4().hex[:12]}"


# ── G3: the Dream-context marker (spec §6.2) ─────────────────────────────


@contextmanager
def checkpoint_phase() -> Iterator[None]:
    """G3 (spec §6.2): the **only** function in the codebase that sets the
    Dream-context marker (``storage.lease._DREAM_CONTEXT``,
    statically verified by ``tests/unit/test_g3_enforcement.py``). Used to
    bracket every step that writes ``plasticity:``/``synapses:`` blocks --
    Phase 3's decay-floor archival move and Phase 7's checkpoint loop."""
    with lease_mod.dream_context():
        yield


def assert_dream_context() -> None:
    lease_mod.assert_dream_context()


# ── Enqueue / dedup (spec §4.1, AC-032) ──────────────────────────────────


@dataclass(frozen=True)
class EnqueueOutcome:
    consolidation_id: str | None
    enqueued: bool
    status: str  # "queued" | "throttled" | "disabled"
    note: str


def enqueue(
    conn: sqlite3.Connection, cfg: Config, *, trigger: str, apply_min_interval: bool = False
) -> EnqueueOutcome:
    """Writer-connection-only: a single-row read + conditional insert into
    the *operational* ``consolidation_run`` table (spec §2.4 -- not Tier
    A/B learned state, so it needs no G2/G3 lease, only exclusion from the
    hot path, which comes from using the writer connection). Called from
    (a) the ``consolidate()`` tool (no min-interval throttle: "Explicit
    tool call | ... | always on") and (b) ``mcp/app.py``'s dispatcher after
    a successful ``session_end`` call (``apply_min_interval=True``: "Session
    end | ... | on, 300s floor", spec §4.1) -- ``core/session.py::
    session_end()`` itself never opens a writer connection (it is one of
    G1's seven hot-path-only tools) and is unchanged by M4; the real
    enqueue write happens here, through Dream's own lease-free (operational
    table) path, not by reaching around G1.

    Dedup (both callers): if a run is already ``queued``/``running``,
    returns *that* run's id, ``enqueued=False`` -- spec §4.1: "at most one
    queued and one running run. consolidate() while a run is
    queued/running returns that run's id (idempotent)". AC-032's own
    scenario (two ``session_end`` calls inside the debounce window) is
    exactly this dedup path.
    """
    existing = conn.execute(
        "SELECT id FROM consolidation_run WHERE state IN ('queued', 'running') "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if existing is not None:
        return EnqueueOutcome(
            consolidation_id=str(existing["id"]),
            enqueued=False,
            status="queued",
            note="a Dream run is already queued/running; returning its id (idempotent)",
        )

    if apply_min_interval:
        last = conn.execute(
            "SELECT finished_at FROM consolidation_run WHERE state IN ('succeeded', 'failed') "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if last is not None and last["finished_at"]:
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(str(last["finished_at"]))).total_seconds()
            if elapsed < cfg.dream_min_interval_s:
                return EnqueueOutcome(
                    consolidation_id=None,
                    enqueued=False,
                    status="throttled",
                    note=(
                        f"last Dream run finished {elapsed:.0f}s ago; below "
                        f"dream.min_interval_s={cfg.dream_min_interval_s}"
                    ),
                )

    run_id = _new_run_id()
    conn.execute(
        "INSERT INTO consolidation_run (id, trigger, state, watermark_event_id) VALUES (?, ?, 'queued', 0)",
        (run_id, trigger),
    )
    return EnqueueOutcome(consolidation_id=run_id, enqueued=True, status="queued", note="enqueued")


# ── Phase 1: Replay ───────────────────────────────────────────────────────


@dataclass
class TraceIR:
    node_tags: list[sqlite3.Row] = field(default_factory=list)
    edge_tags: list[sqlite3.Row] = field(default_factory=list)
    candidate_edges: list[sqlite3.Row] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "captured_node_tags": len(self.node_tags),
            "captured_edge_tags": len(self.edge_tags),
            "candidate_edges_seen": len(self.candidate_edges),
        }


def _phase1_replay(conn: sqlite3.Connection) -> TraceIR:
    """spec §4.3 Phase 1: reads ``eph_event`` (not mined for S in M4, see
    module docstring), ``eph_tag`` (captured), ``eph_session`` -- builds the
    in-memory TraceIR Phase 2 consumes. No writes."""
    return TraceIR(
        node_tags=ephemeral_mod.pending_captured_node_tags(conn),
        edge_tags=ephemeral_mod.pending_captured_edge_tags(conn),
        candidate_edges=conn.execute(
            "SELECT src_id, dst_id, type, evidence_count FROM eph_candidate_edge WHERE type = 'co_activation'"
        ).fetchall(),
    )


# ── Phase 2: Potentiate ───────────────────────────────────────────────────


def _dt_seconds(last_applied: str | None, at: str) -> float | None:
    if not last_applied:
        return None
    try:
        delta = datetime.fromisoformat(str(at)) - datetime.fromisoformat(str(last_applied))
    except ValueError:
        return None
    return max(delta.total_seconds(), 0.0)


def _prune_learned_edges(conn: sqlite3.Connection, cfg: Config) -> list[tuple[str, str, str]]:
    """spec §4.3 Phase 2: "prune: S_edge < theta_prune (0.10) for >=3
    consecutive runs -> archive row, drop from synapses". Declared edges
    (author intent) are exempt -- only ``provenance='learned'`` edges are
    ever pruned; a dropped row simply stops existing (never persisted to a
    file to begin with unless it had already crossed theta_synapse, so
    "drop from synapses" is automatic once the row is gone)."""
    rows = conn.execute(
        "SELECT src_id, dst_name, type, storage_strength, below_prune_runs FROM edge "
        "WHERE provenance = 'learned'"
    ).fetchall()
    pruned: list[tuple[str, str, str]] = []
    for r in rows:
        key = (r["src_id"], r["dst_name"], r["type"])
        if float(r["storage_strength"]) < cfg.theta_prune:
            new_count = int(r["below_prune_runs"]) + 1
            if new_count >= 3:
                conn.execute(
                    "DELETE FROM edge WHERE src_id = ? AND dst_name = ? AND type = ?", key
                )
                pruned.append(key)
            else:
                conn.execute(
                    "UPDATE edge SET below_prune_runs = ? WHERE src_id = ? AND dst_name = ? AND type = ?",
                    (new_count, *key),
                )
        elif int(r["below_prune_runs"]) != 0:
            conn.execute(
                "UPDATE edge SET below_prune_runs = 0 WHERE src_id = ? AND dst_name = ? AND type = ?", key
            )
    return pruned


def _phase2_potentiate(
    cfg: Config, conn: sqlite3.Connection, trace: TraceIR, *, run_id: str
) -> dict[str, Any]:
    """spec §4.3 Phase 2: folds captured ``eph_tag`` rows into real
    ``engram.storage_strength``/``edge.storage_strength`` updates via
    ``core/plasticity.py``'s tier-gated, saturation/spacing-bounded
    ``compute_dw_magnitude`` + ``apply``, promotes ``eph_candidate_edge``
    existence into real ``edge`` rows, and prunes consistently-weak learned
    edges. Every processed tag is marked consumed (AC-020)."""
    committed_nodes = 0
    committed_edges = 0
    dominant_tier: dict[str, int] = {}

    for tag in trace.node_tags:
        engram_id = str(tag["engram_id"])
        row = conn.execute(
            "SELECT storage_strength FROM engram WHERE id = ?", (engram_id,)
        ).fetchone()
        if row is None:
            ephemeral_mod.mark_tag_consumed(conn, int(tag["id"]), run_id=run_id)
            continue

        current_s = float(row["storage_strength"])
        valence = float(tag["capture_valence"] or 0.0)
        capture_weight = float(tag["capture_weight"] or 0.0)
        tier = int(tag["signal_tier"])
        # M6 defect fix (write_ratio churn, R3's <5% target): the spacing
        # dt input is read from the Tier-C anchor (never the file-mirrored
        # `engram.last_applied`) -- see storage.ephemeral.
        # get_potentiation_anchor's docstring.
        anchor = ephemeral_mod.get_potentiation_anchor(conn, engram_id)
        magnitude = plasticity_mod.compute_dw_magnitude(
            eta=cfg.eta,
            w_max=cfg.w_max,
            tau_spacing_hours=cfg.tau_spacing_hours,
            current_w=current_s,
            dt_since_last_update_s=_dt_seconds(anchor, str(tag["captured_at"])),
            mean_outcome=valence,
            capture_weight=capture_weight,
        )
        delta = plasticity_mod.PlasticityDelta(tier=tier, target="S", magnitude=magnitude)
        new_s = plasticity_mod.apply(current_s, delta)
        dw = new_s - current_s
        if abs(dw) > cfg.theta_dw_commit:
            success_inc = 1 if valence > 0 else 0
            failure_inc = 1 if valence < 0 else 0
            conn.execute(
                "UPDATE engram SET storage_strength = ?, "
                "peak_storage_strength = MAX(peak_storage_strength, ?), last_applied = ?, "
                "success_count = success_count + ?, failure_count = failure_count + ? WHERE id = ?",
                (new_s, new_s, tag["captured_at"], success_inc, failure_inc, engram_id),
            )
            committed_nodes += 1
            dominant_tier[engram_id] = max(dominant_tier.get(engram_id, 0), tier)
        # Advance the Tier-C spacing anchor unconditionally -- including
        # (especially) when this observation did not itself commit a
        # material dw (e.g. its dt_since_last_update_s was None -- the
        # very first observation, spacing=0 by design, see core/
        # plasticity.py::compute_eta_eff_untiered). Without this, the
        # anchor would stay null forever and every future observation
        # would also see dt=None, permanently blocking potentiation for
        # this engram. Unlike the pre-M6 behaviour, this NEVER touches
        # the file-mirrored `engram.last_applied` column on a
        # non-committing observation -- that column only advances inside
        # the commit branch above, so a checkpoint candidate whose S
        # (and everything else spec §4.5's dirty() enumerates) did not
        # actually change renders byte-identical to the file on disk,
        # instead of differing on `last_applied` alone.
        ephemeral_mod.set_potentiation_anchor(conn, engram_id, str(tag["captured_at"]))
        ephemeral_mod.mark_tag_consumed(conn, int(tag["id"]), run_id=run_id)

    for tag in trace.edge_tags:
        src_id = str(tag["edge_src"])
        dst_id = str(tag["edge_dst"])
        edge_type = str(tag["edge_type"])
        dst_row = conn.execute("SELECT name FROM engram WHERE id = ?", (dst_id,)).fetchone()
        if dst_row is None:
            ephemeral_mod.mark_tag_consumed(conn, int(tag["id"]), run_id=run_id)
            continue
        dst_name = str(dst_row["name"])

        edge_row = conn.execute(
            "SELECT storage_strength, last_updated, evidence_count FROM edge "
            "WHERE src_id = ? AND dst_name = ? AND type = ?",
            (src_id, dst_name, edge_type),
        ).fetchone()
        if edge_row is None:
            now_edge = _now()
            conn.execute(
                "INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at, "
                "evidence_count, provenance, first_observed, dangling) "
                "VALUES (?, ?, ?, ?, 0.0, ?, 0, 'learned', ?, 0)",
                (src_id, dst_name, dst_id, edge_type, now_edge, now_edge),
            )
            current_s, last_updated, evidence_count = 0.0, None, 0
        else:
            current_s = float(edge_row["storage_strength"])
            last_updated = edge_row["last_updated"]
            evidence_count = int(edge_row["evidence_count"])

        valence = float(tag["capture_valence"] or 0.0)
        capture_weight = float(tag["capture_weight"] or 0.0)
        tier = int(tag["signal_tier"])
        magnitude = plasticity_mod.compute_dw_magnitude(
            eta=cfg.eta,
            w_max=cfg.w_max,
            tau_spacing_hours=cfg.tau_spacing_hours,
            current_w=current_s,
            dt_since_last_update_s=_dt_seconds(last_updated, str(tag["captured_at"])),
            mean_outcome=valence,
            capture_weight=capture_weight,
        )
        delta = plasticity_mod.PlasticityDelta(tier=tier, target="S", magnitude=magnitude)
        new_s = plasticity_mod.apply(current_s, delta)
        # Unconditional write, unlike the node branch's split: on a
        # first-ever (spacing=0) observation this is a numeric no-op for
        # storage_strength (new_s == current_s) but still advances
        # last_updated/evidence_count, which is exactly the anchor-
        # establishment behaviour the node branch above achieves with an
        # explicit else. A brand-new edge row, in particular, MUST get its
        # anchor set on this very first pass (it has no other write path).
        conn.execute(
            "UPDATE edge SET storage_strength = ?, last_updated = ?, s_decayed_at = ?, evidence_count = ? "
            "WHERE src_id = ? AND dst_name = ? AND type = ?",
            (new_s, tag["captured_at"], tag["captured_at"], evidence_count + 1, src_id, dst_name, edge_type),
        )
        if abs(new_s - current_s) > cfg.theta_dw_commit:
            committed_edges += 1
        ephemeral_mod.mark_tag_consumed(conn, int(tag["id"]), run_id=run_id)

    promoted = 0
    for cand in trace.candidate_edges:
        src_id, dst_id, edge_type = str(cand["src_id"]), str(cand["dst_id"]), str(cand["type"])
        dst_row = conn.execute("SELECT name FROM engram WHERE id = ?", (dst_id,)).fetchone()
        if dst_row is None:
            continue
        dst_name = str(dst_row["name"])
        exists = conn.execute(
            "SELECT 1 FROM edge WHERE src_id = ? AND dst_name = ? AND type = ?", (src_id, dst_name, edge_type)
        ).fetchone()
        if exists is None:
            now_p = _now()
            conn.execute(
                "INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at, "
                "evidence_count, provenance, first_observed, dangling) "
                "VALUES (?, ?, ?, ?, 0.0, ?, 0, 'learned', ?, 0)",
                (src_id, dst_name, dst_id, edge_type, now_p, now_p),
            )
            promoted += 1

    pruned = _prune_learned_edges(conn, cfg)

    return {
        "committed_nodes": committed_nodes,
        "committed_edges": committed_edges,
        "promoted_candidates": promoted,
        "pruned_edges": len(pruned),
        "dominant_tier": dominant_tier,
    }


# ── Phase 4: Renormalise ──────────────────────────────────────────────────


def _phase4_renormalise(conn: sqlite3.Connection, cfg: Config) -> dict[str, int]:
    """docs/03 §Key Update Rules 4 / spec §4.3 Phase 4 (homeostasis):
    "Total weight <- sum of all edge weights; if total > bound, scale all
    by bound/total; preserve relative strength ratios." Applied per
    source-node (docs/03: "prevent any single skill from monopolizing all
    activation" is a per-node statement), bounded by ``cfg.w_max`` -- the
    same metaplastic-saturation ceiling Phase 2's Δw formula already treats
    as the natural "full" weight, so no new tunable is introduced.

    Excludes ``provenance = 'derived'`` (``similar_to`` kNN) edges: they
    are not Dream's learned weights at all -- ``sync()`` fully rebuilds
    them from raw cosine similarity on every call (``storage.durable.
    replace_similar_to_edges``), so scaling them here would just be undone
    by the next ``sync()``, and homeostasis is a statement about weight
    that *grows through repeated potentiation* (spec Approach commitment
    2: "writers are earned, not assumed"), which a freshly-recomputed
    similarity score never does.
    """
    rows = conn.execute(
        "SELECT src_id, dst_name, type, storage_strength FROM edge "
        "WHERE storage_strength > 0 AND provenance != 'derived'"
    ).fetchall()
    by_src: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_src.setdefault(str(r["src_id"]), []).append(r)

    scaled_nodes = 0
    for edges in by_src.values():
        total = sum(float(e["storage_strength"]) for e in edges)
        if total > cfg.w_max:
            factor = cfg.w_max / total
            for e in edges:
                new_s = float(e["storage_strength"]) * factor
                conn.execute(
                    "UPDATE edge SET storage_strength = ? WHERE src_id = ? AND dst_name = ? AND type = ?",
                    (new_s, e["src_id"], e["dst_name"], e["type"]),
                )
            scaled_nodes += 1
    return {"nodes_scaled": scaled_nodes}


# ── Phase 5: Distil ─────────────────────────────────────────────────────


def _phase5_distill(cfg: Config, conn: sqlite3.Connection, *, run_id: str) -> dict[str, Any]:
    """spec §4.3 Phase 5: "TraceIR frequent paths -> approval rows
    (op='nucleate') + candidates payload; (-- proposals only)". Delegates
    to ``core/distill.py::run_distillation`` -- the same function
    ``nucleate()`` (the manual trigger, ``mcp/bind_dream.py``) calls, so
    Dream's automatic pass and an operator's on-demand one are exactly one
    implementation. Proposal-only (CR-3): writes ``approval`` rows,
    never an engram."""
    outcome = distill_mod.run_distillation(
        cfg, conn, proposed_by="dream-worker", session_ids=None
    )
    return {
        "candidates": len(outcome.candidates),
        "approval_ids": outcome.approval_ids,
        "note": outcome.note,
    }


# ── Phase 7: Checkpoint ────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointStats:
    checkpointed: int
    modified_engrams: list[str]
    write_ratio: float
    timestamp: str
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpointed": self.checkpointed,
            "modified_engrams": self.modified_engrams,
            "write_ratio": round(self.write_ratio, 4),
            "timestamp": self.timestamp,
        }


@dataclass
class _CheckpointCandidate:
    engram: Engram
    source_engram: Engram
    frontmatter_doc: Any
    path: Path
    changed: bool


def _build_synapses(conn: sqlite3.Connection, cfg: Config, engram_id: str) -> list[Synapse]:
    """spec §4.5: ``syn = [edge for edge in edges(src=e) if edge.provenance
    != 'derived' and (edge.provenance == 'declared' or (edge.
    storage_strength >= theta_synapse and edge.evidence_count >= 3))]``.
    Dangling declared edges are kept (spec §4.5: "written through verbatim
    and stay inert until the target registers")."""
    rows = conn.execute(
        "SELECT dst_name, type, storage_strength, evidence_count, provenance, first_observed, last_updated "
        "FROM edge WHERE src_id = ? AND provenance != 'derived' ORDER BY type, dst_name",
        (engram_id,),
    ).fetchall()
    out: list[Synapse] = []
    for r in rows:
        provenance = str(r["provenance"])
        if provenance != "declared" and (
            float(r["storage_strength"]) < cfg.theta_synapse or int(r["evidence_count"]) < 3
        ):
            continue
        out.append(
            Synapse(
                target=str(r["dst_name"]),
                type=r["type"],
                storage_strength=round(float(r["storage_strength"]), 4),
                evidence_count=int(r["evidence_count"]),
                provenance=provenance,  # type: ignore[arg-type]
                first_observed=str(r["first_observed"]),
                last_updated=r["last_updated"],
            )
        )
    return out


def _build_checkpoint_candidate(
    cfg: Config, conn: sqlite3.Connection, project_root: Path, engram_row: sqlite3.Row
) -> _CheckpointCandidate | None:
    """Renders "what the file would look like if written now, from current
    DB state" and compares it against "the as-parsed file, re-rendered
    through the identical pipeline" -- **not** the raw original bytes.

    That distinction matters: a fixture/hand-authored ``.egr.md`` may omit
    an empty ``synapses:`` key entirely (the pydantic model defaults it to
    ``[]``), but ``render_document`` always emits the key explicitly.
    Diffing a freshly-rendered candidate against raw original bytes would
    therefore flag *every* untouched engram as "dirty" on its very first
    Dream run purely from that syntactic normalisation -- exactly the kind
    of false-positive churn R3 exists to prevent. Rendering *both* sides
    through the same pipeline means only genuine semantic differences (S,
    status, synapses content, outcome counts, ...) show up as a diff.

    R3's "state-derived dirty set": dirtiness is a pure function of current
    state, never a transient "did phase 2 touch this row" flag, so a
    re-run with unchanged state always re-derives the same (empty) answer
    (AC-020).
    """
    file_path = project_root / str(engram_row["path"])
    if not file_path.is_file():
        return None
    parsed = parser_mod.parse_file(file_path, registry_root=project_root)
    engram = parsed.engram

    db_row = conn.execute("SELECT * FROM engram WHERE id = ?", (engram.id,)).fetchone()
    if db_row is None:
        return None

    existing_plasticity = engram.frontmatter.plasticity
    new_plasticity = Plasticity(
        storage_strength=round(float(db_row["storage_strength"]), 4),
        exposure_count=int(db_row["exposure_count"]),
        outcome=Outcome(success=int(db_row["success_count"]), failure=int(db_row["failure_count"])),
        last_applied=db_row["last_applied"],
        excitability=round(float(db_row["excitability"]), 4),
        last_checkpoint=existing_plasticity.last_checkpoint if existing_plasticity else None,
        status=cast(EngramStatus, str(db_row["status"])),
    )

    candidate = engram.model_copy(deep=True)
    candidate.frontmatter.plasticity = new_plasticity
    # M7 conformance fix: checkpoint the DB's current high-water mark into
    # the file's own root-level `peak_storage_strength` (see
    # engram/model.py's docstring on that field) -- without this, the
    # value has no file representation at all and a `skill-graph.db`
    # rebuild silently resets it to whatever S the rebuild happens to see,
    # defeating archive_below_floor's peak-based eligibility gate.
    candidate.frontmatter.peak_storage_strength = round(float(db_row["peak_storage_strength"]), 4)
    candidate.frontmatter.synapses = _build_synapses(conn, cfg, engram.id)

    # Render the as-parsed original through the same pipeline first (see
    # docstring) -- render_document mutates frontmatter_doc's plasticity/
    # synapses/version keys in place, but each call's returned string is
    # captured immediately, so reusing one CommentedMap sequentially here
    # is safe.
    original_rendered = writer_mod.render_document(engram, parsed.frontmatter_doc)
    candidate_text = writer_mod.render_document(candidate, parsed.frontmatter_doc)
    changed = candidate_text != original_rendered
    return _CheckpointCandidate(
        engram=candidate,
        source_engram=engram,
        frontmatter_doc=parsed.frontmatter_doc,
        path=file_path,
        changed=changed,
    )


def _phase7_checkpoint(
    cfg: Config,
    conn: sqlite3.Connection,
    project_root: Path,
    *,
    dominant_tier: dict[str, int] | None = None,
    checkpoint_at: str | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> CheckpointStats:
    dominant_tier = dominant_tier or {}
    rows = conn.execute("SELECT id, name, path, status FROM engram ORDER BY id").fetchall()
    total = len(rows)
    # A resumed run must render the same bytes as its uninterrupted
    # counterpart.  In particular, provenance timestamps cannot move merely
    # because the process restarted after atomic_write() but before the DB
    # checkpoint committed.  The orchestrator therefore pins this to the
    # run's durable started_at value.
    now = checkpoint_at or _now()
    modified: list[str] = []

    with checkpoint_phase():
        for row in rows:
            if row["status"] == "archived":
                continue  # relocated by Phase 3's decay-floor archival; nothing to checkpoint here
            built = _build_checkpoint_candidate(cfg, conn, project_root, row)
            if built is None:
                continue
            if not built.changed:
                source_plasticity = built.source_engram.frontmatter.plasticity
                # Recovery case: atomic_write() landed this run's exact file
                # before process death rolled back the enclosing SQLite
                # transaction.  Reconcile the DB mirror from that file and
                # count it exactly as the uninterrupted phase did.  A normal
                # unchanged file (including a pure exposure delta) has a
                # different/empty last_checkpoint and remains untouched.
                if source_plasticity is not None and source_plasticity.last_checkpoint == now:
                    final_text = built.path.read_text(encoding="utf-8")
                    conn.execute(
                        "UPDATE engram SET exposure_count = ?, last_checkpoint = ?, "
                        "content_sha256 = ?, body_sha256 = ? WHERE id = ?",
                        (
                            source_plasticity.exposure_count,
                            now,
                            ids_mod.content_sha256(final_text.encode("utf-8")),
                            ids_mod.body_sha256(writer_mod.render_body(built.source_engram.body)),
                            row["id"],
                        ),
                    )
                    conn.execute(
                        "UPDATE eph_bookkeeping SET exposure_delta = 0 WHERE engram_id = ?",
                        (row["id"],),
                    )
                    modified.append(str(row["name"]))
                continue

            candidate = built.engram
            # Exposure delta is folded in *only* when the engram is
            # otherwise dirty (see module docstring / decay.py: pure
            # exposure/route traffic must never by itself force a
            # checkpoint write, or the <5% churn target (R3) would be
            # defeated by ordinary routing).
            delta_row = conn.execute(
                "SELECT exposure_delta FROM eph_bookkeeping WHERE engram_id = ?", (row["id"],)
            ).fetchone()
            exposure_delta = int(delta_row["exposure_delta"]) if delta_row is not None else 0
            final_exposure = candidate.frontmatter.plasticity.exposure_count + exposure_delta  # type: ignore[union-attr]
            candidate.frontmatter.plasticity = candidate.frontmatter.plasticity.model_copy(  # type: ignore[union-attr]
                update={"exposure_count": final_exposure, "last_checkpoint": now}
            )

            tier = dominant_tier.get(str(row["id"]))
            journal_entry = ProvenanceJournalEntry(
                version=candidate.frontmatter.version,
                timestamp=now,
                author="dream-worker",
                event="consolidated",
                note=(
                    f"Dream checkpoint: S={candidate.frontmatter.plasticity.storage_strength:.4f}, "
                    f"status={candidate.frontmatter.plasticity.status}"
                ),
                signal_tier=(str(tier) if tier is not None else None),
                base_version=candidate.frontmatter.version,
            )
            # If process death happened after the atomic file replacement,
            # the file already contains this exact entry while the enclosing
            # SQLite phase transaction was rolled back.  Do not append it a
            # second time during recovery.
            if journal_entry not in candidate.frontmatter.provenance_journal:
                candidate.frontmatter.provenance_journal = [
                    *candidate.frontmatter.provenance_journal,
                    journal_entry,
                ]

            writer_mod.write_plasticity(candidate)  # G3 gate
            writer_mod.write_synapses(candidate)  # G3 gate

            # M7 conformance fix: render once, write those exact bytes, and
            # hash those exact bytes -- rather than write_engram() (which
            # re-renders internally) followed by a stale DB content_sha256/
            # body_sha256 left over from *before* this checkpoint mutated
            # the frontmatter (S, status, synapses, peak_storage_strength,
            # provenance_journal, ...). Before this fix, only exposure_count/
            # last_checkpoint were kept in sync here, so engram.content_sha256
            # silently drifted from the file's real digest on every single
            # checkpoint write -- and since durable_projection() (the AC-009
            # rebuild-invariant diff) includes content_sha256/body_sha256,
            # that drift alone would make a post-Dream rebuild fail the
            # invariant even once the edge/peak fixes above are in place: a
            # freshly-rebuilt DB re-parses the file and computes the file's
            # *actual*, current digest, which would never match the stale
            # value this row carried before the rebuild.
            final_text = writer_mod.render_document(candidate, built.frontmatter_doc)
            writer_mod.atomic_write(built.path, final_text)
            if fault_hook is not None:
                fault_hook(f"checkpoint:file:{row['name']}")
            new_content_sha256 = ids_mod.content_sha256(final_text.encode("utf-8"))
            new_body_sha256 = ids_mod.body_sha256(writer_mod.render_body(candidate.body))

            conn.execute(
                "UPDATE engram SET exposure_count = ?, last_checkpoint = ?, "
                "content_sha256 = ?, body_sha256 = ? WHERE id = ?",
                (final_exposure, now, new_content_sha256, new_body_sha256, row["id"]),
            )
            if delta_row is not None:
                conn.execute(
                    "UPDATE eph_bookkeeping SET exposure_delta = 0 WHERE engram_id = ?", (row["id"],)
                )
            modified.append(str(row["name"]))

    write_ratio = (len(modified) / total) if total else 0.0
    return CheckpointStats(
        checkpointed=len(modified),
        modified_engrams=sorted(modified),
        write_ratio=write_ratio,
        timestamp=now,
        total=total,
    )


def run_checkpoint_only(cfg: Config, conn: sqlite3.Connection) -> CheckpointStats:
    """spec §3.3 tool 10: "checkpoint() = Dream phase 7 in isolation" --
    the standalone entry point ``mcp/bind_registry.py::checkpoint()`` calls.
    Acquires the same cross-process lease Dream's full ``run()`` does (a
    concurrent full Dream run and a bare ``checkpoint()`` call must not
    interleave writes to the same files)."""
    cfg.ensure_dirs()
    project_root = cfg.project_root.resolve()
    cross_lease = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path, conn=conn, holder=f"checkpoint:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    )
    with cross_lease.acquire():
        with lease_mod.writer_lease(holder="checkpoint"):
            return _phase7_checkpoint(cfg, conn, project_root)


# ── Explicit archive (M5; spec §5.1 "any(!=draft) -> archived | ... | operator call") ──

#: The FSM's own restriction: "any(!=draft)" -- `draft` was never routable
#: to begin with, and `archived` is not a legal *source* of this same
#: transition (revival is the *separate* `archived -> probation` row).
NOT_ARCHIVABLE_STATUSES: frozenset[str] = frozenset({"draft", "archived"})


def archive_engram(
    cfg: Config, conn: sqlite3.Connection, *, name: str, reason: str | None, actor: str
) -> decay_mod.ArchivedEntry:
    """The ``archive()`` tool's actual execution path (spec §5.1's
    "operator call" trigger) -- reuses :func:`core.decay.archive_one`, the
    same file-move/DB-flip mechanics ``archive_below_floor`` (Dream's own
    auto-archive) already uses, so there is exactly one way an engram ever
    becomes ``archived`` on disk. G3 (the ``plasticity:`` block) requires
    :func:`checkpoint_phase`; this is the second, non-decay-floor caller of
    it (the module docstring's "the `checkpoint()` tool and `magicite dream
    --once` reach learned state through that same function" now has a
    third member: the ``archive()`` tool).
    """
    row = conn.execute(
        "SELECT id, name, path, version, storage_strength, s_decayed_at, status FROM engram WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no engram named {name!r}")
    if row["status"] in NOT_ARCHIVABLE_STATUSES:
        raise TransitionDeniedError(
            f"cannot archive {name!r}: status {row['status']!r} is not a legal source "
            "for the any(!=draft) -> archived transition",
            unmet=[f"status {row['status']!r} is excluded (draft was never routable; "
                   "archived is already archived)"],
        )

    now = _now()
    note = f"explicit archive: {reason}" if reason else "explicit archive (no reason given)"
    cfg.ensure_dirs()
    # M5 data-integrity fix (same defect class as register()/sync()/
    # export(), spec §4.2): the explicit archive() path is a second,
    # independent writer of durable engram state and must not interleave
    # with a running Dream cycle either -- the cross-process lease, not
    # just G2's in-process one.
    cross_lease = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path, conn=conn, holder=f"archive-tool:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    )
    with cross_lease.acquire():
        with lease_mod.writer_lease(holder="archive-tool"):
            with checkpoint_phase():
                entry = decay_mod.archive_one(cfg, conn, row=row, now=now, note=note, author=actor)
    if entry is None:
        raise NotFoundError(f"{name!r}'s file is missing on disk; cannot archive")
    return entry


# ── The orchestrator ───────────────────────────────────────────────────────


@dataclass
class DreamRunResult:
    run_id: str
    state: str
    trigger: str
    stats: dict[str, Any]
    watermark_event_id: int
    checkpoint_write_ratio: float
    modified_engrams: list[str]
    archived_engrams: list[str]
    started_at: str
    finished_at: str


def _claim_run(conn: sqlite3.Connection, *, trigger: str) -> sqlite3.Row:
    """Claim the oldest recoverable consolidation before creating one.

    The cross-process lease has already excluded a live owner at this point,
    so a ``running`` row is evidence of process death, not concurrent work.
    A ``failed`` row whose phase cursor is still present is similarly
    resumable.  Reclaiming either row (rather than minting a replacement)
    preserves the consolidation identity and its completed-phase ledger.
    """
    existing = conn.execute(
        "SELECT * FROM consolidation_run "
        "WHERE state = 'running' OR (state = 'failed' AND phase IS NOT NULL) "
        "OR state = 'queued' "
        "ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, rowid "
        "LIMIT 1"
    ).fetchone()
    if existing is not None:
        return existing
    run_id = _new_run_id()
    conn.execute(
        "INSERT INTO consolidation_run (id, trigger, state, watermark_event_id) VALUES (?, ?, 'queued', 0)",
        (run_id, trigger),
    )
    created = conn.execute("SELECT * FROM consolidation_run WHERE id = ?", (run_id,)).fetchone()
    assert created is not None
    return created


_PHASES: tuple[str, ...] = (
    "replay",
    "potentiate",
    "decay",
    "renormalise",
    "distill",
    "audit",
    "checkpoint",
)


def _trace_to_json(trace: TraceIR) -> dict[str, list[dict[str, Any]]]:
    return {
        "node_tags": [dict(row) for row in trace.node_tags],
        "edge_tags": [dict(row) for row in trace.edge_tags],
        "candidate_edges": [dict(row) for row in trace.candidate_edges],
    }


def _trace_from_json(payload: dict[str, Any]) -> TraceIR:
    # Phase 2 only relies on Mapping-style ``row[\"column\"]`` access, so
    # plain dictionaries are a process-independent representation of the
    # frozen replay input.
    return TraceIR(
        node_tags=list(payload.get("node_tags", [])),
        edge_tags=list(payload.get("edge_tags", [])),
        candidate_edges=list(payload.get("candidate_edges", [])),
    )


def _decode_run_state(row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any]]:
    persisted = json.loads(str(row["stats_json"])) if row["stats_json"] else {}
    recovery = persisted.pop("_recovery", {})
    return persisted, recovery


def _encoded_run_state(stats: dict[str, Any], recovery: dict[str, Any]) -> str:
    return json.dumps({**stats, "_recovery": recovery}, default=str, sort_keys=True)


def _guarded_execute(
    lease: lease_mod.CrossProcessLease,
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> sqlite3.Cursor:
    """Fence every direct orchestration write with the current token."""
    lease.assert_owned()
    return conn.execute(sql, parameters)


def _phase_transaction(
    conn: sqlite3.Connection,
    lease: lease_mod.CrossProcessLease,
    *,
    run_id: str,
    phase: str,
    next_phase: str | None,
    stats: dict[str, Any],
    recovery: dict[str, Any],
    execute: Callable[[], None],
    fault_hook: Callable[[str], None] | None,
) -> None:
    """Run one phase and its cursor advance as one fenced DB commit.

    SQLite rolls back every intra-phase database effect after abrupt process
    termination.  File writes use atomic replacement and are deterministic;
    phase 7 can therefore replay a prefix after restart.  The ownership check
    immediately before COMMIT is the decisive fencing guard: a stale worker
    never publishes a phase checkpoint.
    """
    lease.assert_owned()
    conn.execute("BEGIN IMMEDIATE")
    try:
        execute()
        if fault_hook is not None:
            fault_hook(f"phase:{phase}:after-write")
        lease.assert_owned()
        if next_phase is None:
            _guarded_execute(
                lease,
                conn,
                "UPDATE consolidation_run SET state = 'succeeded', phase = NULL, "
                "finished_at = ?, watermark_event_id = ?, stats_json = ?, error = NULL "
                "WHERE id = ?",
                (
                    str(recovery["finished_at"]),
                    int(recovery["watermark_to"]),
                    json.dumps(stats, default=str, sort_keys=True),
                    run_id,
                ),
            )
        else:
            _guarded_execute(
                lease,
                conn,
                "UPDATE consolidation_run SET state = 'running', phase = ?, stats_json = ?, "
                "error = NULL WHERE id = ?",
                (next_phase, _encoded_run_state(stats, recovery), run_id),
            )
        lease.assert_owned()
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    if fault_hook is not None:
        fault_hook(f"phase:{phase}:committed")


def _last_successful_watermark(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT watermark_event_id FROM consolidation_run WHERE state = 'succeeded' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return int(row["watermark_event_id"]) if row is not None else 0


def _max_event_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(id) AS m FROM eph_event").fetchone()
    return int(row["m"]) if row is not None and row["m"] is not None else 0


def run(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    trigger: str = "manual",
    fault_hook: Callable[[str], None] | None = None,
) -> DreamRunResult:
    """The seven-phase orchestrator (spec §4.3). ``conn`` MUST be an
    unrestricted writer connection (``storage.authorizer.writer_connection``)
    -- Dream both reads/writes ``eph_*`` tables directly (Tier C) and
    durable state (Tier A/B), all within one cross-process-lease-guarded
    run.

    AC-025: if another Dream run (in this process or another) already holds
    the lease, this raises :class:`~magicite.errors.BusyError` before
    touching a single row -- "the second attempt SHALL return busy without
    writing any durable state".
    """
    cfg.ensure_dirs()
    project_root = cfg.project_root.resolve()
    cross_lease = lease_mod.CrossProcessLease(
        lock_path=cfg.dream_lock_path, conn=conn, holder=f"dream:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    )

    with cross_lease.acquire():
        with lease_mod.writer_lease(holder="dream-worker"):
            cross_lease.assert_owned()
            conn.execute("BEGIN IMMEDIATE")
            try:
                claimed = _claim_run(conn, trigger=trigger)
                run_id = str(claimed["id"])
                started_at = str(claimed["started_at"] or _now())
                stats, recovery = _decode_run_state(claimed)
                current_phase = str(claimed["phase"] or "replay")
                if current_phase not in _PHASES:
                    current_phase = "replay"
                recovery.setdefault("version", 1)
                recovery.setdefault("started_at", started_at)
                _guarded_execute(
                    cross_lease,
                    conn,
                    "UPDATE consolidation_run SET state = 'running', phase = ?, started_at = ?, "
                    "finished_at = NULL, error = NULL, stats_json = ? WHERE id = ?",
                    (current_phase, started_at, _encoded_run_state(stats, recovery), run_id),
                )
                cross_lease.assert_owned()
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

            try:
                start_index = _PHASES.index(current_phase)
                for phase in _PHASES[start_index:]:
                    next_index = _PHASES.index(phase) + 1
                    next_phase = _PHASES[next_index] if next_index < len(_PHASES) else None

                    def execute_phase(phase_name: str = phase) -> None:
                        nonlocal stats, recovery
                        if phase_name == "replay":
                            prev_watermark = _last_successful_watermark(conn)
                            now_watermark = _max_event_id(conn)
                            trace = _phase1_replay(conn)
                            recovery["trace"] = _trace_to_json(trace)
                            recovery["watermark_from"] = prev_watermark
                            recovery["watermark_to"] = now_watermark
                            stats["replay"] = {
                                **trace.stats,
                                "watermark_from": prev_watermark,
                                "watermark_to": now_watermark,
                            }
                        elif phase_name == "potentiate":
                            trace = _trace_from_json(dict(recovery.get("trace", {})))
                            phase_stats = _phase2_potentiate(cfg, conn, trace, run_id=run_id)
                            recovery["dominant_tier"] = phase_stats.pop("dominant_tier")
                            stats["potentiate"] = phase_stats
                        elif phase_name == "decay":
                            now_iso = str(recovery["started_at"])
                            retrieval = decay_mod.materialize_retrieval(conn, cfg, now=now_iso)
                            storage = decay_mod.materialize_storage_strength(conn, cfg, now=now_iso)
                            with checkpoint_phase():
                                archived_entries = decay_mod.archive_below_floor(cfg, conn, now=now_iso)
                            retention = decay_mod.purge_retention(conn, cfg, now=now_iso)
                            stats["decay"] = {
                                "retrieval_rows_decayed": retrieval.rows_decayed,
                                "engrams_s_materialized": storage.engrams_materialized,
                                "edges_s_materialized": storage.edges_materialized,
                                "archived": [entry.name for entry in archived_entries],
                                "retention": {
                                    "events_deleted": retention.events_deleted,
                                    "tags_deleted": retention.tags_deleted,
                                    "candidate_edges_deleted": retention.candidate_edges_deleted,
                                },
                            }
                        elif phase_name == "renormalise":
                            stats["renormalise"] = _phase4_renormalise(conn, cfg)
                        elif phase_name == "distill":
                            stats["distill"] = _phase5_distill(cfg, conn, run_id=run_id)
                        elif phase_name == "audit":
                            report = audit_mod.run_audit(cfg, conn, run_id=run_id)
                            stats["audit"] = report.stats()
                        else:
                            checkpoint = _phase7_checkpoint(
                                cfg,
                                conn,
                                project_root,
                                dominant_tier=dict(recovery.get("dominant_tier", {})),
                                checkpoint_at=str(recovery["started_at"]),
                                fault_hook=fault_hook,
                            )
                            stats["checkpoint"] = checkpoint.to_dict()
                            recovery["finished_at"] = _now()

                    _phase_transaction(
                        conn,
                        cross_lease,
                        run_id=run_id,
                        phase=phase,
                        next_phase=next_phase,
                        stats=stats,
                        recovery=recovery,
                        execute=execute_phase,
                        fault_hook=fault_hook,
                    )
                    if next_phase is not None:
                        cross_lease.heartbeat()
            except Exception as exc:
                finished_at = _now()
                try:
                    _guarded_execute(
                        cross_lease,
                        conn,
                        "UPDATE consolidation_run SET state = 'failed', finished_at = ?, error = ? "
                        "WHERE id = ?",
                        (finished_at, str(exc), run_id),
                    )
                except BusyError:
                    # Lost ownership is itself the reason the phase could not
                    # publish.  A newer token must remain the only writer.
                    pass
                raise

            checkpoint_payload = stats["checkpoint"]
            finished_at = str(recovery["finished_at"])

    return DreamRunResult(
        run_id=run_id,
        state="succeeded",
        trigger=trigger,
        stats=stats,
        watermark_event_id=int(recovery["watermark_to"]),
        checkpoint_write_ratio=float(checkpoint_payload["write_ratio"]),
        modified_engrams=list(checkpoint_payload["modified_engrams"]),
        archived_engrams=list(stats["decay"]["archived"]),
        started_at=started_at,
        finished_at=finished_at,
    )
