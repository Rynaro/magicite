"""Frequent-path detection -> nucleation *proposals* (spec §4.3 Phase 5,
docs/03 §Phase 2 step 5, spec §3.3 tool 13, CR-3).

**CR-3 stands absolutely: the server ships no generative model.** This
module never calls an LLM, never makes an HTTP request, and never writes
a ``.egr.md`` file. It does exactly two mechanical things: (1) mine
captured session evidence for skill combinations that were applied
together, repeatedly, with a consistently positive outcome, and (2)
render a deterministic *scaffold* (a plain-text draft, not a valid
engram) plus a machine-readable trace summary, then hand both to the
docs/06 approval machine as a ``proposed`` ``op='nucleate'`` approval.
The host agent is the one who drafts the real ``.egr.md`` and calls
``register()`` -- ``nucleate()``'s own output says so verbatim (spec
§3.3 tool 13's ``note``).

Quality gating for whatever the host eventually drafts is **not** this
module's job either: the deterministic 12-point structural rubric
(``core/fitness.py::structural_rubric_score``, the >=8/12 bar) runs at
``register()``/lifecycle-transition time, on the file the host actually
writes, exactly like every other engram. This module only decides which
combinations are even worth proposing.

**Evidence source.** ``storage.ephemeral.all_captured_node_tags`` -- every
*captured* (i.e. outcome-verified via ``signal_outcome()``, spec §3.3
tool 6's two-phase commit) node tag, grouped by session. Tier-0
(``eph_event``) is never read here, for the same reason ``core/dream.py``
Phase 2 never reads it for S: uncapped, uncapped-by-``core/signals.py``'s
per-skill-per-session cap, self-reported-nothing evidence has no business
seeding a proposal that -- once a human/agent drafts and registers it --
becomes a routable engram.

**Coverage gap (docs/03 phase 5: "no single engram covering them").** A
combination that an existing engram's declared ``composes``/``depends_on``
edges already cover in full is not a distillation candidate -- it is
already representable, and proposing a duplicate would just add approval
-queue noise.

**Ordering, honestly simplified.** v1 does not attempt cross-session
sequence alignment: ``path_names`` is the alphabetically sorted skill-name
set (deterministic, trivially testable) rather than a reconstructed
"most common order" -- a real ordering hint (each session's own observed
order) is still preserved per-session in ``trace_ir`` for the host to
read.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from magicite.config import Config
from magicite.core import approvals as approvals_mod
from magicite.storage import ephemeral as ephemeral_mod

#: docs/03 phase 5 / spec §3.3 tool 13 default: "Paths traversed >=N times
#: with consistent outcomes" -- NucleateInput.min_support's own default.
DEFAULT_MIN_SUPPORT = 5

#: A session only "exhibits a positive outcome" for a tag if its captured
#: valence clears this bar -- matches the qualitative sense of docs/03's
#: "successful paths (high valence + no failures)" without inventing a
#: second, undocumented threshold; theta_salience is already the
#: config-level bar for "this outcome mattered enough to act on" (spec
#: §3.3 tool 6).
_PLAN_EDGE_TYPES: tuple[str, ...] = ("composes", "depends_on")


@dataclass(frozen=True)
class SessionPath:
    session_id: str
    ordered_names: list[str]
    valences: dict[str, float]


@dataclass(frozen=True)
class PathCandidate:
    path_names: list[str]
    support: int
    mean_valence: float
    contributing_sessions: list[str]
    trace_ir: dict[str, Any]
    draft_skeleton: str


def _session_paths(
    conn: sqlite3.Connection, *, theta_salience: float, session_ids: list[str] | None
) -> list[SessionPath]:
    """Group captured node tags by session; a session "exhibits" a skill
    if it has at least one captured tag whose valence clears
    ``theta_salience`` (the config-level "this outcome mattered" bar,
    spec §3.3 tool 6) -- a session with any non-positive-enough or mixed
    evidence for a skill does not nominate that skill into its path.

    Reads through ``storage.ephemeral.all_captured_node_tags`` rather
    than querying ``eph_tag`` directly (that module's own discipline:
    "there is exactly one place that knows the ``eph_*`` schema shapes")
    -- only the ``engram_id -> name`` resolution below touches ``engram``
    directly, a plain read this module (not in AC-024's hot-path-only
    list) is free to do.
    """
    tag_rows = ephemeral_mod.all_captured_node_tags(conn)
    names_by_id = {
        str(r["id"]): str(r["name"]) for r in conn.execute("SELECT id, name FROM engram").fetchall()
    }

    by_session: dict[str, list[sqlite3.Row]] = {}
    for row in tag_rows:
        sid = str(row["session_id"])
        if session_ids is not None and sid not in session_ids:
            continue
        by_session.setdefault(sid, []).append(row)

    paths: list[SessionPath] = []
    for sid, tags in by_session.items():
        ordered: list[str] = []
        valences: dict[str, list[float]] = {}
        for row in tags:
            name = names_by_id.get(str(row["engram_id"]))
            if name is None:
                continue  # engram no longer exists (deleted/rebuilt registry) -- ignore stale evidence
            valence = float(row["capture_valence"] or 0.0)
            valences.setdefault(name, []).append(valence)
            if name not in ordered:
                ordered.append(name)
        # Only names whose *mean* captured valence for this session clears
        # theta_salience count as "successfully applied" for path purposes.
        qualifying = [n for n in ordered if (sum(valences[n]) / len(valences[n])) > theta_salience]
        if len(qualifying) < 2:
            continue
        mean_by_name = {n: sum(valences[n]) / len(valences[n]) for n in qualifying}
        paths.append(SessionPath(session_id=sid, ordered_names=qualifying, valences=mean_by_name))
    return paths


def _already_covered(conn: sqlite3.Connection, path_names: frozenset[str]) -> bool:
    """docs/03 phase 5: "no single engram covering them" -- true if some
    existing engram (possibly itself one of the path's own members, e.g.
    a "hub" skill that already ``needs``/``composes`` the rest of the
    path -- an engram never declares an edge to itself) already declares
    composition edges spanning every *other* member of the candidate set."""
    placeholders = ",".join("?" for _ in _PLAN_EDGE_TYPES)
    rows = conn.execute(
        f"""
        SELECT e.name AS src_name, edge.dst_name AS dst_name
        FROM edge JOIN engram e ON e.id = edge.src_id
        WHERE edge.type IN ({placeholders})
        """,
        _PLAN_EDGE_TYPES,
    ).fetchall()
    by_src: dict[str, set[str]] = {}
    for r in rows:
        by_src.setdefault(str(r["src_name"]), set()).add(str(r["dst_name"]))
    for src_name, targets in by_src.items():
        required = path_names - {src_name}
        if required and required <= targets:
            return True
    return False


def _draft_skeleton(path_names: list[str], *, support: int, mean_valence: float) -> str:
    """CR-3: a mechanical scaffold, never generated prose -- every field
    below is either a literal placeholder the host agent must fill in, or
    the observed skill names themselves. This is not a valid ``.egr.md``
    (deliberately -- see module docstring) and ``nucleate()`` never writes
    it to disk; it is returned for the host agent to draft from."""
    compose_list = ", ".join(path_names)
    steps = "\n".join(f"{i + 1}. Apply `{name}` (see {name}.egr.md)." for i, name in enumerate(path_names))
    return (
        "# DRAFT nucleation skeleton -- NOT a valid .egr.md.\n"
        "# CR-3: the server never generates prose; every <host: ...> field\n"
        "# below is a placeholder for the host agent to fill in before\n"
        "# calling register(). Derived mechanically from "
        f"{support} session(s), mean captured valence {mean_valence:.2f}.\n"
        "---\n"
        "spec: engram/0.2\n"
        "name: <host: choose-a-kebab-case-name>\n"
        "intent:\n"
        "  does: <host: describe what this composite skill accomplishes>\n"
        "  use_when: <host: describe when to use it>\n"
        "  not_when: <host: describe when NOT to use it>\n"
        "triggers:\n"
        "  positive: [<host: fill in>]\n"
        "  negative: [<host: fill in>]\n"
        f"composes: [{compose_list}]\n"
        "---\n"
        "## Procedure\n"
        f"{steps}\n\n"
        "## Pitfalls\n"
        "- <host: fill in from the observed sessions>\n\n"
        "## Examples\n"
        "+ \"<host: a positive example query>\"\n"
        "- \"<host: a negative example query>\"\n"
    )


def mine_frequent_paths(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    session_ids: list[str] | None = None,
) -> list[PathCandidate]:
    """docs/03 phase 5: "Paths traversed >=N times with consistent
    outcomes and no single engram covering them." Pure read -- no
    approval is created here (see :func:`run_distillation`)."""
    sessions = _session_paths(conn, theta_salience=cfg.theta_salience, session_ids=session_ids)

    groups: dict[frozenset[str], list[SessionPath]] = {}
    for sp in sessions:
        key = frozenset(sp.ordered_names)
        groups.setdefault(key, []).append(sp)

    candidates: list[PathCandidate] = []
    for key, members in sorted(groups.items(), key=lambda kv: sorted(kv[0])):
        support = len(members)
        if support < min_support:
            continue
        if _already_covered(conn, key):
            continue
        path_names = sorted(key)
        all_valences = [sp.valences[n] for sp in members for n in sp.ordered_names]
        mean_valence = sum(all_valences) / len(all_valences) if all_valences else 0.0
        per_skill: dict[str, Any] = {
            name: {
                "occurrences": sum(1 for sp in members if name in sp.valences),
                "mean_valence": round(
                    sum(sp.valences[name] for sp in members if name in sp.valences)
                    / max(1, sum(1 for sp in members if name in sp.valences)),
                    4,
                ),
            }
            for name in path_names
        }
        trace_ir = {
            "sessions": [sp.session_id for sp in members],
            "path_names": path_names,
            "support": support,
            "mean_valence": round(mean_valence, 4),
            "per_skill": per_skill,
            "observed_orders": [sp.ordered_names for sp in members],
        }
        candidates.append(
            PathCandidate(
                path_names=path_names,
                support=support,
                mean_valence=round(mean_valence, 4),
                contributing_sessions=sorted(sp.session_id for sp in members),
                trace_ir=trace_ir,
                draft_skeleton=_draft_skeleton(path_names, support=support, mean_valence=mean_valence),
            )
        )
    return candidates


@dataclass
class DistillOutcome:
    candidates: list[PathCandidate] = field(default_factory=list)
    approval_ids: list[str] = field(default_factory=list)
    note: str = ""


def run_distillation(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    session_ids: list[str] | None = None,
    proposed_by: str,
) -> DistillOutcome:
    """Shared by both callers named in spec §3.3 tool 13 / §4.3 Phase 5:
    the manual ``nucleate()`` tool (``mcp/bind_dream.py``,
    ``proposed_by='nucleate-tool'``) and Dream's own automatic Phase 5
    (``core/dream.py``, ``proposed_by='dream-worker'``) -- one mining +
    proposing implementation, two triggers, exactly like ``core/dream.py::
    enqueue()`` is shared by ``consolidate()`` and ``session_end()``.

    Creates one ``proposed`` ``op='nucleate'`` approval per candidate
    (docs/06 approval machine, spec §5.2) -- never writes an engram, never
    changes ``status``/``verification_status`` anywhere (CR-3: proposal
    only)."""
    candidates = mine_frequent_paths(cfg, conn, min_support=min_support, session_ids=session_ids)
    approval_ids: list[str] = []
    for cand in candidates:
        target_name = "nucleated:" + "+".join(cand.path_names)
        record = approvals_mod.propose(
            conn,
            cfg,
            op="nucleate",
            target_name=target_name,
            payload={
                "path_names": cand.path_names,
                "support": cand.support,
                "mean_valence": cand.mean_valence,
                "trace_ir": cand.trace_ir,
                "draft_skeleton": cand.draft_skeleton,
            },
            proposed_by=proposed_by,
        )
        approval_ids.append(record.id)

    note = (
        f"{len(candidates)} nucleation candidate(s) proposed (min_support={min_support}); "
        "draft the .egr.md yourself and call register() -- the server never generates prose (CR-3)."
        if candidates
        else f"no path cleared min_support={min_support} with an uncovered skill combination."
    )
    return DistillOutcome(candidates=candidates, approval_ids=approval_ids, note=note)
