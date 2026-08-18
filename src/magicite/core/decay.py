"""Lazy R/S decay, materialisation policy, and decay-floor archival (spec
§4.3 Phase 3, §6.1 "Decay semantics", AC-033).

Three things live here, all Dream-phase-3 concerns:

1. :func:`effective_value` -- the pure exponential-decay formula
   (``V(t) = V0 * e^(-lambda*dt)``) shared by both R (fast, days-scale) and
   S (slow, weeks-scale) per §6.1.
2. :func:`materialize_retrieval` / :func:`materialize_storage_strength` --
   R is *always* materialised (Tier C, cheap, and closes the carry-forward
   noted in ``storage/ephemeral.py::bump_retrieval``'s docstring: "R
   accumulates undamped ... until core/decay.py exists" -- it exists now).
   S is materialised **only** when the decayed value moves more than
   ``epsilon_write`` or crosses a lifecycle threshold (§6.1: "which is what
   keeps the checkpoint write ratio under the docs/03 5% target") --
   everything else about S stays exactly as the file already has it, so an
   unmaterialised drift never shows up as a dirty engram at Phase 7.
3. :func:`archive_below_floor` -- AC-033: an engram whose S has decayed
   below ``floor_archived`` is moved (never deleted) to
   ``.magicite/archive/<date>-<name>.egr.md``, with its DB row flipped to
   ``status='archived'``. This is the one lifecycle transition Dream is
   explicitly a listed trigger for in spec §5.1's FSM table ("any(≠draft) →
   archived | S_effective < 0.2 (auto) | Dream, archive") that M4 actually
   performs -- unlike the FSM's other rows (nascent→probation etc.), which
   stay proposal/reporting-only pending M5's approval machinery (see
   ``core/fitness.py``/``core/lifecycle.py``'s module docstrings).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from magicite.config import Config
from magicite.core.decay_math import effective_value
from magicite.engram import ids as ids_mod
from magicite.engram import parser as parser_mod
from magicite.engram import writer as writer_mod
from magicite.engram.model import ROUTABLE_STATUSES, ProvenanceJournalEntry
from magicite.storage import durable as durable_mod

__all__ = [
    "effective_value",
    "RetrievalDecayStats",
    "materialize_retrieval",
    "StorageDecayStats",
    "materialize_storage_strength",
    "RetentionStats",
    "purge_retention",
    "ArchivedEntry",
    "archive_below_floor",
    "archive_one",
]


@dataclass(frozen=True)
class RetrievalDecayStats:
    rows_decayed: int


def materialize_retrieval(conn, cfg: Config, *, now: str) -> RetrievalDecayStats:  # noqa: ANN001
    """spec §6.1: "R ... Evaluated lazily at read time ...; materialised in
    Dream phase 3." Always writes (Tier C; no checkpoint-churn concern --
    that constraint applies only to the ``.egr.md`` files S governs)."""
    rows = conn.execute("SELECT engram_id, r, r_decayed_at FROM eph_retrieval").fetchall()
    for row in rows:
        new_r = effective_value(row["r"], row["r_decayed_at"], now, cfg.lambda_r_per_day)
        conn.execute(
            "UPDATE eph_retrieval SET r = ?, r_decayed_at = ? WHERE engram_id = ?",
            (new_r, now, row["engram_id"]),
        )
    return RetrievalDecayStats(rows_decayed=len(rows))


#: Lifecycle-relevant S boundaries (spec §6.1: "crosses a lifecycle
#: threshold" forces materialisation even under epsilon_write). 0.85 is the
#: consolidated->promoted S bar (spec §5.1); the others are named Config
#: fields already.
def _threshold_set(cfg: Config) -> tuple[float, ...]:
    return (cfg.floor_archived, cfg.theta_prune, cfg.theta_synapse, cfg.theta_consolidate_status, 0.85)


def _crosses_threshold(old: float, new: float, thresholds: tuple[float, ...]) -> bool:
    return any((old >= t) != (new >= t) for t in thresholds)


@dataclass(frozen=True)
class StorageDecayStats:
    engrams_materialized: int
    edges_materialized: int


def materialize_storage_strength(conn, cfg: Config, *, now: str) -> StorageDecayStats:  # noqa: ANN001
    """spec §6.1: S is materialised only when it "moves more than
    epsilon_write (0.05)" or "crosses a lifecycle threshold" -- lazy
    otherwise, which is R3's "lazy S materialisation" half of the
    checkpoint-churn defense (the other half, the state-derived dirty set,
    lives in ``core/dream.py``'s Phase 7)."""
    thresholds = _threshold_set(cfg)
    engrams_materialized = 0
    for row in conn.execute("SELECT id, storage_strength, s_decayed_at FROM engram").fetchall():
        old_s = float(row["storage_strength"])
        new_s = effective_value(old_s, row["s_decayed_at"], now, cfg.lambda_s_per_day)
        if abs(new_s - old_s) > cfg.epsilon_write or _crosses_threshold(old_s, new_s, thresholds):
            conn.execute(
                "UPDATE engram SET storage_strength = ?, s_decayed_at = ? WHERE id = ?",
                (new_s, now, row["id"]),
            )
            engrams_materialized += 1

    edges_materialized = 0
    edge_thresholds = (cfg.theta_prune, cfg.theta_synapse)
    edge_rows = conn.execute(
        "SELECT src_id, dst_name, type, storage_strength, s_decayed_at FROM edge"
    ).fetchall()
    for row in edge_rows:
        old_s = float(row["storage_strength"])
        new_s = effective_value(old_s, row["s_decayed_at"], now, cfg.lambda_s_per_day)
        if abs(new_s - old_s) > cfg.epsilon_write or _crosses_threshold(old_s, new_s, edge_thresholds):
            conn.execute(
                "UPDATE edge SET storage_strength = ?, s_decayed_at = ? "
                "WHERE src_id = ? AND dst_name = ? AND type = ?",
                (new_s, now, row["src_id"], row["dst_name"], row["type"]),
            )
            edges_materialized += 1

    return StorageDecayStats(engrams_materialized=engrams_materialized, edges_materialized=edges_materialized)


@dataclass(frozen=True)
class RetentionStats:
    events_deleted: int
    tags_deleted: int
    candidate_edges_deleted: int
    idempotency_rows_deleted: int = 0


def purge_retention(conn, cfg: Config, *, now: str) -> RetentionStats:  # noqa: ANN001
    """spec §6.1: "eph_event and consumed eph_tag rows older than
    retention_days (default 30) are deleted in phase 3; candidate edges idle
    >30 days are dropped. This is the only deletion in the system and it
    touches no learned durable state." M5 security fix #2 extends this: an
    ``eph_idempotency`` row past its real TTL (``mcp/app.py`` now writes
    ``expires_at = created_at + cfg.idempotency_ttl_s`` instead of an
    already-expired timestamp) is purged here too -- the fix's other half
    ("Rows are also written already-expired and never purged")."""
    cutoff = (datetime.fromisoformat(now) - timedelta(days=cfg.retention_days)).isoformat()
    events = conn.execute("DELETE FROM eph_event WHERE ts < ?", (cutoff,))
    tags = conn.execute(
        "DELETE FROM eph_tag WHERE consumed_run_id IS NOT NULL AND captured_at IS NOT NULL "
        "AND captured_at < ?",
        (cutoff,),
    )
    candidates = conn.execute("DELETE FROM eph_candidate_edge WHERE last_updated < ?", (cutoff,))
    idempotency = conn.execute(
        "DELETE FROM eph_idempotency WHERE expires_at < ? AND state = 'completed'", (now,)
    )
    return RetentionStats(
        events_deleted=events.rowcount,
        tags_deleted=tags.rowcount,
        candidate_edges_deleted=candidates.rowcount,
        idempotency_rows_deleted=idempotency.rowcount,
    )


@dataclass(frozen=True)
class ArchivedEntry:
    engram_id: str
    name: str
    old_path: str
    archive_path: str
    storage_strength: float


def archive_one(
    cfg: Config, conn, *, row, now: str, note: str, author: str = "dream-worker"  # noqa: ANN001
) -> ArchivedEntry | None:
    """The shared per-engram archival mechanics (file move + DB flip),
    factored out of :func:`archive_below_floor` so M5's explicit
    ``archive()`` tool (spec §5.1 "any(!=draft) -> archived | ... |
    operator call") can reuse the exact same file-write/DB-write sequence
    for an operator-named engram instead of a decay-floor-crossing one --
    see ``core/dream.py::archive_engram``, the only other caller. MUST be
    invoked from inside ``core.dream.checkpoint_phase()`` (G3: it writes
    the ``plasticity:`` block) and ``storage.lease.writer_lease()`` (G2)
    -- both asserted transitively by ``engram.writer.write_plasticity``/
    ``write_engram``, not re-asserted here. Returns ``None`` (skip, not an
    error) if the engram's file has already vanished from disk -- the same
    "``sync()`` reconciles the stale DB row, not Dream's job" carve-out
    :func:`archive_below_floor` always had.
    """
    project_root = cfg.project_root.resolve()
    file_path = project_root / row["path"]
    if not file_path.is_file():
        return None

    parsed = parser_mod.parse_file(file_path, registry_root=project_root)
    engram = parsed.engram
    s_eff = effective_value(row["storage_strength"], row["s_decayed_at"], now, cfg.lambda_s_per_day)
    plasticity = engram.frontmatter.plasticity
    base_plasticity = plasticity.model_copy() if plasticity is not None else None
    new_plasticity = (base_plasticity or _default_plasticity()).model_copy(
        update={"storage_strength": round(s_eff, 4), "status": "archived", "last_checkpoint": now}
    )

    candidate = engram.model_copy(deep=True)
    candidate.frontmatter.plasticity = new_plasticity
    # M7 conformance fix (parity with core/dream.py's checkpoint phase):
    # carry the DB's *current* high-water mark into the archived copy's
    # root-level peak_storage_strength -- the `row` this function receives
    # does not select that column (archive_below_floor's/archive_engram's
    # own SELECTs predate this field), so it is re-read here rather than
    # trusted from the possibly-stale value the file parse just returned.
    peak_row = conn.execute(
        "SELECT peak_storage_strength FROM engram WHERE id = ?", (engram.id,)
    ).fetchone()
    if peak_row is not None:
        candidate.frontmatter.peak_storage_strength = round(
            max(float(peak_row["peak_storage_strength"]), s_eff, engram.frontmatter.peak_storage_strength), 4
        )
    candidate.frontmatter.provenance_journal = [
        *candidate.frontmatter.provenance_journal,
        ProvenanceJournalEntry(
            version=engram.frontmatter.version,
            timestamp=now,
            author=author,
            event="archived",
            note=note,
            base_version=engram.frontmatter.version,
        ),
    ]

    archive_name = f"{now[:10]}-{engram.name}.egr.md"
    archive_path = cfg.archive_dir / archive_name

    # G3 gate: only reachable from inside core.dream.checkpoint_phase().
    writer_mod.write_plasticity(candidate)
    writer_mod.write_synapses(candidate)
    # Render once, write those exact bytes, hash those exact bytes -- same
    # content_sha256/body_sha256-drift fix as core/dream.py's checkpoint
    # phase (see that module's _phase7_checkpoint docstring comment); the
    # archived row is part of durable_projection() too (it is not filtered
    # by status), so a stale digest here would equally break the rebuild
    # invariant for any registry that has ever auto-archived an engram.
    final_text = writer_mod.render_document(candidate, parsed.frontmatter_doc)
    writer_mod.atomic_write(archive_path, final_text)
    new_content_sha256 = ids_mod.content_sha256(final_text.encode("utf-8"))
    new_body_sha256 = ids_mod.body_sha256(writer_mod.render_body(candidate.body))

    # The archive copy is durably on disk before we touch the registry
    # copy -- "never deleted" holds even if the process dies here.
    os.remove(file_path)

    durable_mod.mark_archived(
        conn,
        engram_id=engram.id,
        storage_strength=s_eff,
        archive_path=str(archive_path.relative_to(project_root)),
        content_sha256=new_content_sha256,
        body_sha256=new_body_sha256,
    )
    durable_mod.upsert_journal(conn, candidate)

    return ArchivedEntry(
        engram_id=engram.id,
        name=engram.name,
        old_path=row["path"],
        archive_path=str(archive_path.relative_to(project_root)),
        storage_strength=s_eff,
    )


def archive_below_floor(cfg: Config, conn, *, now: str) -> list[ArchivedEntry]:  # noqa: ANN001
    """AC-033: any routable engram whose *effective* S has decayed below
    ``floor_archived`` is moved -- never deleted -- to
    ``.magicite/archive/<date>-<name>.egr.md``, and its DB row flips to
    ``status='archived'``. Must run inside Dream's checkpoint phase (writes
    the ``plasticity:`` block via the G3-gated ``engram.writer`` functions)
    and inside the writer lease (G2, for the file move + DB write).
    """
    floor = cfg.floor_archived
    placeholders = ",".join("?" for _ in ROUTABLE_STATUSES)
    # docs/03 §Revival and Archival: "Skills *reaching* S < floor_archived
    # are moved to archived" -- AC-033 itself: "an engram whose effective
    # storage strength has *decayed* below floor_archived". Both phrasings
    # describe a value that *used to be* at or above the floor and has
    # since fallen under it -- not a brand-new engram's S=0 starting point
    # (spec §5.3 step 8: "plasticity defaults for new engrams: S=0").
    #
    # ``(success_count + failure_count) >= 3`` is the eligibility bar:
    # requiring the *same* minimum-evidence count spec §5.1 already uses
    # elsewhere (the probation->consolidated gate's "sessions >= 3",
    # `core/fitness.py`) before an engram is even considered for
    # archival. Two things this rules out, both real failure modes without
    # it: (a) ``last_applied IS NOT NULL`` alone -- M4's spacing-gate fix
    # (core/plasticity.py::compute_eta_eff_untiered) sets that on a
    # first-ever, zero-weight *observation* too (to anchor the next one's
    # spacing calc), so a brand-new, merely-observed-once engram would be
    # archival-eligible despite S never having moved; (b) ``> 0`` alone --
    # a skill's very first real commit is typically small (Phase 2's own
    # saturation/spacing damping), so "has any evidence at all" would
    # still archive it in the very same run its first weak positive
    # signal landed. docs/03 and AC-033 both describe an engram that
    # *reaches*/*has decayed* below the floor -- language for something
    # that accumulated real standing first, not a novice skill that never
    # had the chance.
    #
    # M5 data-integrity fix (defect confirmed by adversarial re-test): the
    # evidence-count gate above is *necessary but not sufficient*. It
    # distinguishes "has evidence" from "has none" -- but a skill can
    # honestly accumulate >=3 positive outcomes while its (saturation- and
    # spacing-damped, spec §4.3) S is *still climbing toward* the floor,
    # never having crossed it. Requiring `peak_storage_strength >=
    # floor_archived` too is what actually encodes "reaching"/"has
    # decayed below" -- a value that WAS at or above the floor and has
    # since fallen under it, not merely grown to some point below it.
    # Without this, ordinary successful use (no adversary required)
    # archives a skill the moment it happens to cross the >=3-outcomes
    # bar below S=0.2, which is a plain data-loss bug, not a security one.
    rows = conn.execute(
        f"SELECT id, name, path, version, storage_strength, s_decayed_at FROM engram "
        f"WHERE status IN ({placeholders}) AND (success_count + failure_count) >= 3 "
        f"AND peak_storage_strength >= ?",
        (*ROUTABLE_STATUSES, floor),
    ).fetchall()

    archived: list[ArchivedEntry] = []
    for row in rows:
        s_eff = effective_value(row["storage_strength"], row["s_decayed_at"], now, cfg.lambda_s_per_day)
        if s_eff >= floor:
            continue
        note = f"auto-archived: storage_strength {s_eff:.4f} < floor_archived {floor:.4f}"
        entry = archive_one(cfg, conn, row=row, now=now, note=note, author="dream-worker")
        if entry is not None:
            archived.append(entry)
    return archived


def _default_plasticity():  # noqa: ANN202
    from magicite.engram.model import Plasticity

    return Plasticity()
