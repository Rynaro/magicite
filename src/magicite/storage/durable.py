"""ONLY module allowed to write non-``eph_`` tables (spec §1 layout, §6.2 G2).

Every public function here calls :func:`magicite.storage.lease.
assert_single_writer` first (G2): callers (``core/registry.py``'s
``register()``/``sync()``/``export()``, and from M4 onward ``core/dream.py``'s
checkpoint phase) acquire ``storage.lease.writer_lease()`` once and then
call through this module for every durable mutation. This is spec's
Approach commitment 2 made structural: "writers are a place, not a rule."

Tier A values are always "copied verbatim from the file — the file wins,
always" (spec §2.6 step 3); this module never invents or decays a value,
it only mirrors whatever ``engram/parser.py`` handed it.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from magicite.engram.model import Engram
from magicite.storage.lease import assert_single_writer

#: needs/composes/inhibits frontmatter fields -> their declared edge type
#: (spec §2.6 step 4: "declared composition (needs/composes/inhibits/affinity)").
EDGE_TYPE_FOR_FIELD: dict[str, str] = {
    "needs": "depends_on",
    "composes": "composes",
    "inhibits": "inhibits",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def upsert_engram(conn: sqlite3.Connection, engram: Engram, *, identity_sha256: str) -> str:
    """Upsert the ``engram`` row plus its ``engram_step``/``engram_trigger``/
    ``engram_journal`` children. Returns the engram id."""
    assert_single_writer()

    fm = engram.frontmatter
    now = _now()
    plasticity = fm.plasticity
    trust = fm.trust

    existing = conn.execute("SELECT created_at FROM engram WHERE id = ?", (fm.id,)).fetchone()
    created_at = existing["created_at"] if existing else now

    incoming_s = plasticity.storage_strength if plasticity else 0.0
    # M7 conformance fix: the file's own `peak_storage_strength` (root-level,
    # see engram/model.py) is now real Tier A state -- read it here instead
    # of implicitly re-deriving "peak == current S" (which is what silently
    # reset the high-water mark to whatever S a rebuild happened to find,
    # defeating archive_below_floor's peak-based eligibility gate the
    # moment skill-graph.db was rebuilt). `max(incoming_s, ...)` is a
    # defensive floor only -- a well-formed file's peak is already >= its
    # own S by construction; this just refuses to accept a corrupt/
    # hand-edited file's peak that is somehow lower than its own S.
    incoming_peak = max(incoming_s, fm.peak_storage_strength)
    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, intent_not_when,
          storage_strength, peak_storage_strength, s_decayed_at, exposure_count, success_count,
          failure_count, excitability, last_applied, last_checkpoint,
          embedding_model, embedding_ref, embedding_refreshed_at,
          has_exec_blocks, identity_sha256, content_sha256, body_sha256, file_mtime_ns,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, path=excluded.path, spec_version=excluded.spec_version,
          version=excluded.version, origin=excluded.origin,
          verification_status=excluded.verification_status, status=excluded.status,
          intent_does=excluded.intent_does, intent_use_when=excluded.intent_use_when,
          intent_not_when=excluded.intent_not_when,
          storage_strength=excluded.storage_strength,
          peak_storage_strength=MAX(engram.peak_storage_strength, excluded.peak_storage_strength),
          exposure_count=excluded.exposure_count,
          success_count=excluded.success_count, failure_count=excluded.failure_count,
          excitability=excluded.excitability, last_applied=excluded.last_applied,
          last_checkpoint=excluded.last_checkpoint,
          has_exec_blocks=excluded.has_exec_blocks, identity_sha256=excluded.identity_sha256,
          content_sha256=excluded.content_sha256, body_sha256=excluded.body_sha256,
          file_mtime_ns=excluded.file_mtime_ns, updated_at=excluded.updated_at
        """,
        (
            fm.id,
            fm.name,
            engram.path,
            fm.spec,
            fm.version,
            fm.provenance,
            trust.verification_status if trust else "pending",
            plasticity.status if plasticity else "nascent",
            fm.intent.does,
            fm.intent.use_when,
            fm.intent.not_when,
            incoming_s,
            incoming_peak,  # file wins on ingest, MAX()'d against the DB's own peak above
            now,
            plasticity.exposure_count if plasticity else 0,
            plasticity.outcome.success if plasticity else 0,
            plasticity.outcome.failure if plasticity else 0,
            plasticity.excitability if plasticity else 0.05,
            plasticity.last_applied if plasticity else None,
            plasticity.last_checkpoint if plasticity else None,
            None,
            None,
            None,
            int(bool(engram.body.exec_blocks)),
            identity_sha256,
            engram.content_sha256,
            engram.body_sha256,
            engram.file_mtime_ns,
            created_at,
            now,
        ),
    )

    conn.execute("DELETE FROM engram_step WHERE engram_id = ?", (fm.id,))
    for step in engram.body.procedure:
        conn.execute(
            "INSERT INTO engram_step (engram_id, step_no, text, ok_count, total_count, fault_class) "
            "VALUES (?,?,?,?,?,?)",
            (fm.id, step.step_no, step.text, step.ok_count, step.total_count, step.fault_class),
        )

    conn.execute("DELETE FROM engram_trigger WHERE engram_id = ?", (fm.id,))
    for ordinal, text in enumerate(fm.triggers.positive):
        conn.execute(
            "INSERT INTO engram_trigger (engram_id, polarity, ord, text) VALUES (?,?,?,?)",
            (fm.id, "positive", ordinal, text),
        )
    for ordinal, text in enumerate(fm.triggers.negative):
        conn.execute(
            "INSERT INTO engram_trigger (engram_id, polarity, ord, text) VALUES (?,?,?,?)",
            (fm.id, "negative", ordinal, text),
        )

    upsert_journal(conn, engram)

    return str(fm.id)


def upsert_journal(conn: sqlite3.Connection, engram: Engram) -> None:
    """Mirror ``frontmatter.provenance_journal`` into ``engram_journal``
    (spec §2.6 step 3 -- the DB mirror table M0 left unpopulated)."""
    assert_single_writer()
    fm = engram.frontmatter
    conn.execute("DELETE FROM engram_journal WHERE engram_id = ?", (fm.id,))
    for entry in fm.provenance_journal:
        conn.execute(
            """
            INSERT INTO engram_journal
              (engram_id, version, ts, author, event, note, signal_tier, base_version)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(engram_id, version, ts) DO NOTHING
            """,
            (
                fm.id,
                entry.version,
                entry.timestamp,
                entry.author,
                entry.event,
                entry.note,
                entry.signal_tier,
                entry.base_version,
            ),
        )


def wire_context_affinity(conn: sqlite3.Connection, engram: Engram) -> None:
    assert_single_writer()
    fm = engram.frontmatter
    names = sorted(set(fm.context_affinity) | set(fm.affinity))
    conn.execute("DELETE FROM engram_context WHERE engram_id = ?", (fm.id,))
    for name in names:
        row = conn.execute("SELECT id FROM context_node WHERE name = ?", (name,)).fetchone()
        if row is None:
            context_id = f"ctx_{name}"
            conn.execute(
                "INSERT OR IGNORE INTO context_node (id, name, kind) VALUES (?,?,?)",
                (context_id, name, "project"),
            )
        else:
            context_id = row["id"]
        conn.execute(
            "INSERT OR REPLACE INTO engram_context (engram_id, context_id, weight) VALUES (?,?,1.0)",
            (fm.id, context_id),
        )


def wire_declared_edges(conn: sqlite3.Connection, engram: Engram) -> list[str]:
    """Upsert declared ``needs``/``composes``/``inhibits`` edges (spec §2.6
    steps 4/6). Returns the list of target names that are still dangling."""
    assert_single_writer()
    fm = engram.frontmatter
    dangling: list[str] = []
    now = _now()
    for field_name, edge_type in EDGE_TYPE_FOR_FIELD.items():
        for target_name in getattr(fm, field_name):
            target = conn.execute("SELECT id FROM engram WHERE name = ?", (target_name,)).fetchone()
            dst_id = target["id"] if target else None
            is_dangling = dst_id is None
            if is_dangling:
                dangling.append(target_name)
            conn.execute(
                """
                INSERT INTO edge (
                  src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                  evidence_count, provenance, first_observed, dangling
                ) VALUES (?,?,?,?, 0.0, ?, 0, 'declared', ?, ?)
                ON CONFLICT(src_id, dst_name, type) DO UPDATE SET
                  dst_id=excluded.dst_id, dangling=excluded.dangling
                """,
                (fm.id, target_name, dst_id, edge_type, now, now, int(is_dangling)),
            )
    # Resolve any pre-existing dangling edges that now point at this newly-registered name.
    conn.execute(
        "UPDATE edge SET dst_id = ?, dangling = 0 WHERE dst_name = ? AND dangling = 1",
        (fm.id, fm.name),
    )
    return dangling


def reconcile_file_edges(conn: sqlite3.Connection, engram: Engram) -> int:
    """Replace the source-owned outgoing edge set for one engram.

    The file is authoritative for declared fields and the checkpointed
    ``synapses`` block. Derived ``similar_to`` rows remain DB-owned and are
    rebuilt later in ``sync()``. Returns the number of stale rows removed.
    """
    assert_single_writer()
    fm = engram.frontmatter
    desired = {
        (target_name, edge_type)
        for field_name, edge_type in EDGE_TYPE_FOR_FIELD.items()
        for target_name in getattr(fm, field_name)
    }
    desired.update((synapse.target, synapse.type) for synapse in fm.synapses)

    rows = conn.execute(
        "SELECT dst_name, type FROM edge WHERE src_id = ? AND provenance != 'derived'",
        (fm.id,),
    ).fetchall()
    stale = [row for row in rows if (str(row["dst_name"]), str(row["type"])) not in desired]
    for row in stale:
        conn.execute(
            "DELETE FROM edge WHERE src_id = ? AND dst_name = ? AND type = ?",
            (fm.id, row["dst_name"], row["type"]),
        )
    return len(stale)


def wire_synapse_edges(conn: sqlite3.Connection, engram: Engram) -> list[str]:
    """Upsert edge rows from the file's ``synapses:`` block -- spec §2.6
    step 4's second half: "...and the synapses: block (provenance from the
    file)". This is what makes the file the source of truth (INV-3) for
    *learned* edge weight, not just node state: without it, deleting
    ``skill-graph.db`` and re-running ``sync()`` silently discards every
    Hebbian-learned ``storage_strength``/``evidence_count`` Dream ever
    checkpointed, even though the node-level equivalent (``plasticity:``)
    already survives the identical rebuild via :func:`upsert_engram`.

    Callers (``core/registry.py::_ingest_one``) run this **after**
    :func:`wire_declared_edges` in the same pass. Order matters: a
    ``synapses:`` entry with ``provenance='declared'`` is the file's own
    checkpoint of a *declared* edge's learned strength (spec §4.5's
    ``_build_synapses`` keeps every declared-provenance row unconditionally,
    un-thresholded, precisely so this round-trips) and must overwrite
    :func:`wire_declared_edges`'s ``storage_strength=0.0``/
    ``evidence_count=0`` baseline row for that same ``(src, dst, type)`` --
    not the reverse. A ``learned``/``distilled``-provenance entry with no
    corresponding ``needs``/``composes``/``inhibits`` declaration creates a
    brand-new edge row outright (a pure Hebbian ``co_activation`` edge has
    no declared counterpart at all). ``similar_to``/``derived`` edges never
    appear in ``synapses:`` (spec §4.5 excludes ``provenance='derived'``
    from the checkpoint; they are rebuilt from scratch every ``sync()`` by
    :func:`replace_similar_to_edges` instead) -- nothing here special-cases
    that type, it simply never occurs in practice.

    Returns the list of target names still dangling after this pass (same
    contract as :func:`wire_declared_edges`).
    """
    assert_single_writer()
    fm = engram.frontmatter
    dangling: list[str] = []
    for synapse in fm.synapses:
        target = conn.execute("SELECT id FROM engram WHERE name = ?", (synapse.target,)).fetchone()
        dst_id = target["id"] if target else None
        is_dangling = dst_id is None
        if is_dangling:
            dangling.append(synapse.target)
        s_decayed_at = synapse.last_updated or synapse.first_observed
        conn.execute(
            """
            INSERT INTO edge (
              src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
              evidence_count, provenance, first_observed, last_updated, dangling
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(src_id, dst_name, type) DO UPDATE SET
              dst_id=excluded.dst_id, storage_strength=excluded.storage_strength,
              s_decayed_at=excluded.s_decayed_at, evidence_count=excluded.evidence_count,
              provenance=excluded.provenance, first_observed=excluded.first_observed,
              last_updated=excluded.last_updated, dangling=excluded.dangling
            """,
            (
                fm.id,
                synapse.target,
                dst_id,
                synapse.type,
                synapse.storage_strength,
                s_decayed_at,
                synapse.evidence_count,
                synapse.provenance,
                synapse.first_observed,
                synapse.last_updated,
                int(is_dangling),
            ),
        )
    return dangling


def set_embedding_ref(
    conn: sqlite3.Connection, *, engram_id: str, model_name: str, source_sha256: str
) -> None:
    """Point ``engram.embedding_*`` at the Tier-C vector ``storage.ephemeral.
    upsert_embedding`` just wrote (the pointer is durable metadata even
    though the vector itself is a rebuildable Tier-C cache row)."""
    assert_single_writer()
    conn.execute(
        "UPDATE engram SET embedding_model=?, embedding_ref=?, embedding_refreshed_at=? WHERE id=?",
        (model_name, source_sha256, _now(), engram_id),
    )


def mark_archived(
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    storage_strength: float,
    archive_path: str,
    content_sha256: str | None = None,
    body_sha256: str | None = None,
) -> None:
    """spec §5.1 FSM row "any(≠draft) → archived | S_effective < 0.2 (auto)
    | Dream, archive" -- the one lifecycle transition Dream itself performs
    in M4 (AC-033). Deliberately narrow: this is not the general
    ``set_status()`` the full FSM (M5) will own; it only ever moves a row to
    ``status='archived'`` with the S value the decay floor check already
    computed, from ``core/decay.py::archive_below_floor`` alone.

    ``content_sha256``/``body_sha256`` (M7 conformance fix): optional so a
    caller with nothing new to report keeps working, but ``archive_one``
    always passes both -- the archived file is rewritten (new
    ``plasticity:``/``peak_storage_strength``/``provenance_journal``) as
    part of this same transition, and ``durable_projection()`` includes
    archived rows (it is not filtered by status), so leaving the old
    digests in place here would reproduce the exact rebuild-invariant
    drift the Dream-checkpoint content_sha256 fix closes.
    """
    assert_single_writer()
    if content_sha256 is not None and body_sha256 is not None:
        conn.execute(
            "UPDATE engram SET status = 'archived', storage_strength = ?, path = ?, "
            "content_sha256 = ?, body_sha256 = ?, updated_at = ? WHERE id = ?",
            (storage_strength, archive_path, content_sha256, body_sha256, _now(), engram_id),
        )
    else:
        conn.execute(
            "UPDATE engram SET status = 'archived', storage_strength = ?, path = ?, updated_at = ? "
            "WHERE id = ?",
            (storage_strength, archive_path, _now(), engram_id),
        )


def delete_engram(conn: sqlite3.Connection, engram_id: str) -> None:
    """spec §2.6 step 5: delete durable rows whose file vanished."""
    assert_single_writer()
    conn.execute("DELETE FROM engram WHERE id = ?", (engram_id,))


def recompute_dangling(conn: sqlite3.Connection) -> list[str]:
    """spec §2.6 step 6, run once after all files are ingested and vanished
    rows are removed (step 5): resolve every edge's ``dst_name -> dst_id``
    against the *current* ``engram`` table.

    Per-file resolution (``wire_declared_edges``'s own "resolve pre-existing
    dangling edges pointing at this name" update) only catches edges whose
    target registers *after* it. It does not catch the inverse: a target
    that ``delete_engram`` just removed leaves ``ON DELETE SET NULL``
    behind on ``dst_id`` but does not flip ``dangling`` back to 1. A
    single comprehensive pass over every edge, independent of file
    processing order, is what step 6 actually requires.
    """
    assert_single_writer()
    conn.execute(
        """
        UPDATE edge
        SET dst_id = (SELECT id FROM engram WHERE engram.name = edge.dst_name),
            dangling = CASE
              WHEN (SELECT id FROM engram WHERE engram.name = edge.dst_name) IS NULL THEN 1
              ELSE 0
            END
        """
    )
    rows = conn.execute(
        "SELECT DISTINCT dst_name FROM edge WHERE dangling = 1 ORDER BY dst_name"
    ).fetchall()
    return [str(r["dst_name"]) for r in rows]


def write_schema_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    assert_single_writer()
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def replace_similar_to_edges(
    conn: sqlite3.Connection, neighbors_by_id: dict[str, list[tuple[str, str, float]]]
) -> None:
    """spec §2.6 step 8: rebuild the derived top-``m`` cosine kNN
    ``similar_to`` edges. ``provenance='derived'`` + ``DB-only`` (spec
    §2.2): these never enter a ``.egr.md``'s ``synapses:`` block (§4.5:
    "kNN edges never persist") -- they are recomputed on every ``sync()``
    from whatever embeddings currently exist, same as any other derived
    index.

    ``neighbors_by_id``: ``engram_id -> [(dst_name, dst_id, cosine), ...]``.
    ``storage_strength`` is set to the raw cosine similarity (not 0.0, unlike
    a freshly-declared edge) -- a kNN edge's very existence *is* its
    evidence, unlike a declared ``needs``/``composes``/``inhibits`` edge,
    which encodes intent and must still be *learned* (Dream, M4) before it
    carries weight (spec Approach commitment 2: writers -- and by
    extension, weight -- are earned, not assumed).
    """
    assert_single_writer()
    now = _now()
    conn.execute("DELETE FROM edge WHERE type = 'similar_to' AND provenance = 'derived'")
    for src_id, neighbors in neighbors_by_id.items():
        for dst_name, dst_id, cosine in neighbors:
            conn.execute(
                """
                INSERT INTO edge (
                  src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                  evidence_count, provenance, first_observed, dangling
                ) VALUES (?,?,?, 'similar_to', ?, ?, 1, 'derived', ?, 0)
                ON CONFLICT(src_id, dst_name, type) DO UPDATE SET
                  dst_id=excluded.dst_id, storage_strength=excluded.storage_strength,
                  s_decayed_at=excluded.s_decayed_at
                """,
                (src_id, dst_name, dst_id, round(cosine, 6), now, now),
            )


def set_status(conn: sqlite3.Connection, *, engram_id: str, to_status: str) -> None:
    """spec §5.1: "nothing else assigns [``engram.status``]" -- the
    ``promote()``/``archive()`` lifecycle-transition write path
    (``core/lifecycle.py`` validates the transition *before* ever calling
    this; see that module's docstring for why the guard lives there
    instead of here, mirroring the existing :func:`mark_archived`
    convention). Deliberately guard-free, like every other function in
    this module -- callers decide, this module only writes."""
    assert_single_writer()
    conn.execute(
        "UPDATE engram SET status = ?, updated_at = ? WHERE id = ?", (to_status, _now(), engram_id)
    )


def set_verification_status(conn: sqlite3.Connection, *, engram_id: str, to_status: str) -> None:
    """spec §5.1's ``any -> quarantined`` row (safety direction is free,
    no approval) and the M5 security fix #1's server-assigned trust axis.
    Symmetric to :func:`set_status`, on the orthogonal axis (spec §5.1:
    "status ... orthogonal to verification_status")."""
    assert_single_writer()
    conn.execute(
        "UPDATE engram SET verification_status = ?, updated_at = ? WHERE id = ?",
        (to_status, _now(), engram_id),
    )


def disable_projection_by_path(conn: sqlite3.Connection, path: str) -> int:
    """Fail closed when the authoritative file at ``path`` is invalid."""
    assert_single_writer()
    cursor = conn.execute(
        "UPDATE engram SET verification_status = 'quarantined', updated_at = ? WHERE path = ?",
        (_now(), path),
    )
    return cursor.rowcount


def append_transition_journal(
    conn: sqlite3.Connection,
    *,
    engram_id: str,
    version: int,
    author: str,
    event: str,
    note: str | None = None,
    base_version: int | None = None,
) -> None:
    """spec §5.1: "Every transition appends an engram_journal row[.] ...
    at the next checkpoint, a provenance-journal entry in the file." This
    is the immediate DB-side half; Dream's own checkpoint phase (already
    unconditional for any dirty engram, ``core/dream.py::_phase7_
    checkpoint``) writes the file-side entry the next time it runs -- a
    status change alone is enough to make an engram's rendered candidate
    differ from what is on disk, so no special-casing is needed there."""
    assert_single_writer()
    conn.execute(
        """
        INSERT INTO engram_journal (engram_id, version, ts, author, event, note, signal_tier, base_version)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(engram_id, version, ts) DO NOTHING
        """,
        (engram_id, version, _now(), author, event, note, None, base_version),
    )


def upsert_communities(conn: sqlite3.Connection, assignments: dict[str, int], *, algo: str) -> None:
    """spec §2.6 step 9 / §2.2: ``engram_community`` is "a derived index;
    rebuilt, never checkpointed" -- a full delete + reinsert on every
    ``sync()``, exactly like :func:`replace_similar_to_edges`, and for the
    same reason (it is not Tier A/B durable state, spec §2.2)."""
    assert_single_writer()
    now = _now()
    conn.execute("DELETE FROM engram_community")
    for engram_id, community_id in assignments.items():
        conn.execute(
            "INSERT INTO engram_community (engram_id, community_id, algo, computed_at) VALUES (?,?,?,?)",
            (engram_id, community_id, algo, now),
        )
