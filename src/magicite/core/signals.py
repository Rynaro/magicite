"""Tag set/capture, co-activation, per-session caps, server-side tier
assignment (spec §3.3 tools 5-6, §6.2 Tier gate, docs/05 D3).

**AC-024 / hot path (spec §6.2 G1):** this module MUST NOT import
``magicite.storage.durable`` or ``magicite.engram.writer`` --
``signal_use``/``signal_outcome`` write only ``eph_*`` tables (Tier C), via
``storage.ephemeral``, on the authorizer-restricted connection
(``storage/authorizer.py::ephemeral_connection``). ``tests/unit/
test_p0_enforcement.py`` asserts this statically.

**AC-015 / R2 (the second of this milestone's two P0 security risks):**
:func:`assign_tier` is the *only* place a signal's provenance tier is
decided, and it never trusts the caller. Tier 2 requires
``adapter_token == cfg.hook_token`` -- a secret held only in host adapter
config (``docs/adapters/claude-code.md``), never echoed back to a client and
never derivable from anything a client sends. A caller claiming
``adapter_token="hook_verified"`` (or any other guess) with no matching
server-side token configured gets Tier 1, full stop.

**R1 (signal poisoning defense, bounded, not eliminated):** the per-skill-
per-session cap (``cfg.per_skill_session_cap``, default 3) bounds how many
times one session can tag one skill; Tier-1's weight cap (``core/plasticity.
py::TIER_WEIGHT[1] == 0.6``) bounds how much a self-reported signal is worth
once Dream (M4) actually applies it. Neither claims to eliminate poisoning
by a sufficiently patient adversarial session -- only to bound its cost per
session, exactly as docs/05 describes.
"""

from __future__ import annotations

import hmac
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from magicite.config import Config
from magicite.core import session as session_mod
from magicite.engram.model import ROUTABLE_STATUSES
from magicite.errors import InvalidInputError
from magicite.storage import ephemeral as ephemeral_mod


def assign_tier(cfg: Config, adapter_token: str | None) -> int:
    """AC-015: server-side tier assignment. Tier 2 requires the caller's
    ``adapter_token`` to match ``cfg.hook_token`` (``MAGICITE_HOOK_TOKEN``,
    host adapter config only) via a constant-time comparison; every other
    input -- ``None``, the empty string, a guessed token, or the literal
    string naming the tier a caller wants -- yields Tier 1. If the server
    has no ``hook_token`` configured at all, Tier 2 is categorically
    unreachable, independent of what any caller sends."""
    if cfg.hook_token and adapter_token and hmac.compare_digest(adapter_token, cfg.hook_token):
        return 2
    return 1


def _resolve_skill(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name, status, verification_status FROM engram WHERE id = ? OR name = ?",
        (identifier, identifier),
    ).fetchone()


def _is_routable(row: sqlite3.Row) -> bool:
    return row["status"] in ROUTABLE_STATUSES and row["verification_status"] == "verified"


def _resolve_routable_ids(conn: sqlite3.Connection, identifiers: list[str]) -> list[str]:
    """spec §3.3 tool 5 step 1: "resolve + routable check; unknown id =>
    invalid_input (never silently ignored)". A resolved-but-not-routable
    engram (draft/archived/quarantined) is treated the same as unknown --
    tagging a skill that is not currently eligible for routing would let a
    caller manufacture co-activation evidence for content the system has
    otherwise decided not to surface, which is exactly the kind of signal
    the Tier ladder exists to keep honest."""
    resolved: list[str] = []
    for ident in identifiers:
        row = _resolve_skill(conn, ident)
        if row is None or not _is_routable(row):
            raise InvalidInputError(
                f"{ident!r} does not resolve to a routable engram",
                hint="signal_use only accepts ids/names of engrams currently eligible for routing",
            )
        resolved.append(str(row["id"]))
    return resolved


@dataclass
class SignalUseOutcome:
    tagged: list[str]
    co_activation_candidates: list[str]
    expires_at: str
    signal_tier: int
    capped: list[str] = field(default_factory=list)
    note: str = ""


def signal_use(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    skill_ids: list[str],
    session_id: str | None = None,
    adapter_token: str | None = None,
) -> SignalUseOutcome:
    """spec §3.3 tool 5. Sets Tier-C tags (phase 1 of the docs/03 two-phase
    commit), nudges R, and generates co-activation candidate edges -- never
    S (Principle 1: application, not listing, and even then only via R)."""
    tier = assign_tier(cfg, adapter_token)
    sid = session_mod.resolve_id(cfg, conn, session_id)

    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(hours=cfg.session_ttl_hours)).isoformat()

    resolved_ids = _resolve_routable_ids(conn, skill_ids)

    tagged: list[str] = []
    capped: list[str] = []
    cap = cfg.per_skill_session_cap
    for engram_id in resolved_ids:
        if ephemeral_mod.count_node_tags(conn, session_id=sid, engram_id=engram_id) >= cap:
            capped.append(engram_id)
            continue
        ephemeral_mod.insert_node_tag(
            conn,
            session_id=sid,
            engram_id=engram_id,
            signal_tier=tier,
            set_at=now,
            expires_at=expires_at,
        )
        ephemeral_mod.bump_retrieval(conn, engram_id, eta_r=cfg.eta_r, refractory_s=cfg.eta_r_refractory_s)
        tagged.append(engram_id)

    # spec §3.3 tool 5 step 5: "every pair of skills with live tags in this
    # session" -- but only pairs where at least one side was newly tagged
    # *this call* are (re-)witnessed here; a pair entirely among skills that
    # were already live before this call was already witnessed on the call
    # that made them co-live, and re-touching it now would inflate its
    # evidence_count for a co-occurrence this call did not actually observe.
    live_ids = ephemeral_mod.live_tagged_engram_ids(conn, session_id=sid, now=now)
    newly_tagged = set(tagged)
    touched_pairs: set[tuple[str, str]] = set()
    for x in live_ids:
        for y in live_ids:
            if x >= y:
                continue
            if x in newly_tagged or y in newly_tagged:
                touched_pairs.add((x, y))

    for a, b in touched_pairs:
        for src, dst in ((a, b), (b, a)):
            ephemeral_mod.upsert_candidate_edge(conn, src_id=src, dst_id=dst, edge_type="co_activation")
            ephemeral_mod.insert_edge_tag(
                conn,
                session_id=sid,
                edge_src=src,
                edge_dst=dst,
                edge_type="co_activation",
                signal_tier=tier,
                set_at=now,
                expires_at=expires_at,
            )

    touched_ids = {nid for pair in touched_pairs for nid in pair}
    co_activation_candidates = sorted(touched_ids - newly_tagged)

    ephemeral_mod.append_event(
        conn,
        session_id=sid,
        tool="signal_use",
        signal_tier=tier,
        engram_id=tagged[0] if tagged else None,
        payload={"skill_ids": skill_ids, "tagged": tagged, "capped": capped},
    )

    if capped:
        note = (
            f"{len(capped)} skill(s) already hit the per-session cap "
            f"({cap} signal_use calls per skill per session); no new tags were set for them"
        )
    else:
        note = (
            f"tag set; expires at {expires_at}. signal_outcome() will capture "
            "these into durable weight changes."
        )

    return SignalUseOutcome(
        tagged=tagged,
        co_activation_candidates=co_activation_candidates,
        expires_at=expires_at,
        signal_tier=tier,
        capped=capped,
        note=note,
    )


@dataclass
class SignalOutcomeOutcome:
    captured: int
    skills_credited: list[str]
    signal_tier: int
    consolidation_scheduled: bool
    note: str


def signal_outcome(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    valence: float,
    salience: float | None = 0.5,
    skill_ids: list[str] | None = None,
    session_id: str | None = None,
    adapter_token: str | None = None,
) -> SignalOutcomeOutcome:
    """spec §3.3 tool 6. Phase 2 of the docs/03 two-phase commit: captures
    live tags for Dream consolidation. Never moves S itself -- that is
    Dream's phase 2 (M4), gated through ``core/plasticity.py::apply()``
    (AC-014)."""
    tier = assign_tier(cfg, adapter_token)
    sid = session_mod.resolve_id(cfg, conn, session_id)

    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    salience_value = 0.5 if salience is None else salience

    # spec §3.3 tool 6's credit-set rule (the construction spec's own
    # resolution of docs/03 rule 2's ordering, spec wins on conflict):
    #   skill_ids given            -> exactly those (recency-weighted)
    #   |valence| > theta_salience -> every live tag in the session window
    #   otherwise                  -> nothing captured
    if skill_ids:
        credit_ids: list[str] = []
        for ident in skill_ids:
            row = _resolve_skill(conn, ident)
            if row is None:
                raise InvalidInputError(f"{ident!r} does not resolve to a known engram")
            credit_ids.append(str(row["id"]))
        note = "captured explicitly listed skill_ids"
    elif abs(valence) > cfg.theta_salience:
        # M4 hardening: bounded to the N most-recently-tagged skills (see
        # storage.ephemeral.live_tagged_engram_ids_recent's docstring) --
        # spec §3.3's "every live tag in the session window" is otherwise
        # an unbounded fan-out from a single call.
        credit_ids = ephemeral_mod.live_tagged_engram_ids_recent(
            conn, session_id=sid, now=now, limit=cfg.retroactive_credit_max
        )
        note = (
            "high-salience outcome: retroactive credit to the "
            f"{cfg.retroactive_credit_max} most recently live-tagged skill(s) in the session window"
        )
    else:
        credit_ids = []
        note = (
            f"|valence|={abs(valence):.2f} did not clear theta_salience={cfg.theta_salience} "
            "and no skill_ids were given; nothing captured"
        )

    skills_credited: list[str] = []
    for engram_id in credit_ids:
        live_tags = ephemeral_mod.live_uncaptured_node_tags(
            conn, session_id=sid, engram_id=engram_id, now=now
        )
        if not live_tags:
            continue
        for tag_row in live_tags:
            dt_seconds = (now_dt - datetime.fromisoformat(str(tag_row["set_at"]))).total_seconds()
            # spec §3.3 tool 6: capture_weight = salience * exp(-dt / tau_credit) -- recency weighting.
            capture_weight = salience_value * math.exp(-max(dt_seconds, 0.0) / cfg.tau_credit_seconds)
            ephemeral_mod.capture_node_tag(
                conn,
                int(tag_row["id"]),
                captured_at=now,
                valence=valence,
                salience=salience_value,
                capture_weight=capture_weight,
            )
        skills_credited.append(engram_id)

    # M4 carry-forward ("fold captured eph_tag rows into real edge ...
    # updates"): a co_activation edge whose *either* endpoint is in this
    # call's credit set gets the same outcome-linked, capped, two-phase
    # capture the node side has always had -- M3 built insert_edge_tag()
    # (the SET side) but never wired the CAPTURE side, so edges accumulated
    # evidence_count via eph_candidate_edge but never an outcome-weighted
    # Δw input. Still gated by the same credit-set rule (explicit skill_ids
    # or high-salience retroactive window) and the same per-tag cap
    # core/signals.py::signal_use() already enforces at SET time.
    if credit_ids:
        credit_set = set(credit_ids)
        live_edge_keys = ephemeral_mod.live_tagged_edge_keys(conn, session_id=sid, now=now)
        for edge_src, edge_dst, edge_type in live_edge_keys:
            if edge_src not in credit_set and edge_dst not in credit_set:
                continue
            edge_tags = ephemeral_mod.live_uncaptured_edge_tags(
                conn, session_id=sid, edge_src=edge_src, edge_dst=edge_dst, edge_type=edge_type, now=now
            )
            for tag_row in edge_tags:
                dt_seconds = (now_dt - datetime.fromisoformat(str(tag_row["set_at"]))).total_seconds()
                capture_weight = salience_value * math.exp(-max(dt_seconds, 0.0) / cfg.tau_credit_seconds)
                ephemeral_mod.capture_edge_tag(
                    conn,
                    int(tag_row["id"]),
                    captured_at=now,
                    valence=valence,
                    salience=salience_value,
                    capture_weight=capture_weight,
                )

    ephemeral_mod.append_event(
        conn,
        session_id=sid,
        tool="signal_outcome",
        signal_tier=tier,
        engram_id=None,
        valence=valence,
        salience=salience_value,
        payload={"skill_ids": skill_ids, "skills_credited": skills_credited},
    )

    return SignalOutcomeOutcome(
        captured=len(skills_credited),
        skills_credited=skills_credited,
        signal_tier=tier,
        # Dream's enqueue/run machinery is core/dream.py, M4 (spec Stories);
        # honestly false rather than a stub success (INV-4's tool-level
        # discipline applied at the core layer too).
        consolidation_scheduled=False,
        note=note,
    )
