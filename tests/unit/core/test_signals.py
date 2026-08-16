"""``core/signals.py``: server-side tier assignment (AC-015), the
per-skill-per-session cap (R1), co-activation candidate generation, and the
signal_outcome credit-set rule (spec §3.3 tools 5-6)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
from magicite.errors import InvalidInputError

PROTON = "proton-ge-proton-downgrade"
STEAM_PREFIX = "steam-prefix-access"


@pytest.fixture
def registered(cfg, db_conn, embedder) -> None:
    outcome = registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    assert outcome.ingested == 7


def _candidate_evidence(db_conn, src_id: str, dst_id: str) -> int | None:
    row = db_conn.execute(
        "SELECT evidence_count FROM eph_candidate_edge "
        "WHERE src_id = ? AND dst_id = ? AND type = 'co_activation'",
        (src_id, dst_id),
    ).fetchone()
    return None if row is None else int(row["evidence_count"])


# ── AC-015: tier is assigned server-side, never client-claimed ─────────────


def test_tier_assigned_server_side(cfg, db_conn, registered) -> None:
    """GIVEN a caller that supplies no adapter_token
    WHEN it calls signal_outcome(valence=1.0)
    THEN the recorded signal tier SHALL be 1, regardless of any tier the
    caller claims."""
    assert cfg.hook_token is None  # no MAGICITE_HOOK_TOKEN configured (v1 default)
    outcome = signals_mod.signal_outcome(cfg, db_conn, valence=1.0, session_id="s1")
    assert outcome.signal_tier == 1


@pytest.mark.parametrize(
    "spoofed_token",
    ["hook_verified", "tier2", "2", "", "Bearer sometoken", "s3cr3t-host-token-guess"],
)
def test_adapter_token_spoof_attempt_never_earns_tier2_with_no_server_token(
    cfg, db_conn, registered, spoofed_token: str
) -> None:
    """The spoof attempt: a caller sends a plausible-looking adapter_token
    (including literally naming the tier it wants) while the server has no
    MAGICITE_HOOK_TOKEN configured at all. It genuinely fails to spoof --
    every single one of these still resolves to Tier 1."""
    assert cfg.hook_token is None
    outcome = signals_mod.signal_outcome(
        cfg, db_conn, valence=1.0, session_id="s1", adapter_token=spoofed_token
    )
    assert outcome.signal_tier == 1


@pytest.mark.parametrize(
    "spoofed_token",
    ["hook_verified", "s3cr3t-host-token ", "S3CR3T-HOST-TOKEN", "s3cr3t-host-toke", ""],
)
def test_adapter_token_spoof_attempt_never_earns_tier2_against_a_real_token(
    cfg, db_conn, registered, spoofed_token: str
) -> None:
    """Same spoof attempt, but now a real hook token *is* configured on the
    server (a live Claude Code adapter deployment) and the caller guesses
    near-misses (wrong case, trailing whitespace, or the literal string
    'hook_verified'). Every near-miss still resolves to Tier 1 -- only the
    exact secret earns Tier 2."""
    cfg.hook_token = "s3cr3t-host-token"
    outcome = signals_mod.signal_outcome(
        cfg, db_conn, valence=1.0, session_id="s1", adapter_token=spoofed_token
    )
    assert outcome.signal_tier == 1


def test_correct_adapter_token_earns_tier2(cfg, db_conn, registered) -> None:
    cfg.hook_token = "s3cr3t-host-token"
    outcome = signals_mod.signal_outcome(
        cfg, db_conn, valence=1.0, session_id="s1", adapter_token="s3cr3t-host-token"
    )
    assert outcome.signal_tier == 2


def test_signal_use_tier_assignment_matches_signal_outcome(cfg, db_conn, registered) -> None:
    cfg.hook_token = "s3cr3t-host-token"
    tier1 = signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    assert tier1.signal_tier == 1
    tier2 = signals_mod.signal_use(
        cfg, db_conn, skill_ids=[STEAM_PREFIX], session_id="s1", adapter_token="s3cr3t-host-token"
    )
    assert tier2.signal_tier == 2


# ── signal_use: resolution, caps, R nudge, co-activation ───────────────────


def test_signal_use_rejects_unknown_skill_id(cfg, db_conn, registered) -> None:
    with pytest.raises(InvalidInputError):
        signals_mod.signal_use(cfg, db_conn, skill_ids=["not-a-real-skill"], session_id="s1")


def test_signal_use_rejects_non_routable_skill(cfg, db_conn, embedder, toy_registry_dir) -> None:
    """A SKILL.md import lands status='draft' (CR-4), which is not routable
    -- signal_use must refuse to tag it, the same as an unknown id."""
    import shutil

    skills_dir = cfg.project_root / "skills"
    shutil.copytree(toy_registry_dir / "skills", skills_dir)
    outcome = registry_mod.register(cfg, db_conn, embedder, path="skills", fmt="skill")
    assert outcome.ingested == 3
    draft_name = outcome.registered[0].name

    with pytest.raises(InvalidInputError):
        signals_mod.signal_use(cfg, db_conn, skill_ids=[draft_name], session_id="s1")


def test_signal_use_nudges_retrieval_strength(cfg, db_conn, registered) -> None:
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    before = db_conn.execute("SELECT r FROM eph_retrieval WHERE engram_id = ?", (engram_id,)).fetchone()
    assert before is None

    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")

    after = db_conn.execute("SELECT r FROM eph_retrieval WHERE engram_id = ?", (engram_id,)).fetchone()
    assert after["r"] == pytest.approx(cfg.eta_r)


def test_signal_use_caps_at_per_session_limit(cfg, db_conn, registered) -> None:
    """R1: per-skill-per-session cap = 3; extra calls return capped=true and
    set no new tags (anti-poisoning bound -- bounded, not eliminated: a
    fresh session_id resets the cap, which is the documented, honest limit
    of a *session-scoped* defense)."""
    assert cfg.per_skill_session_cap == 3
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]

    for _ in range(3):
        outcome = signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
        assert outcome.capped == []

    capped_outcome = signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    assert capped_outcome.capped == [engram_id]
    assert capped_outcome.tagged == []

    tag_count = db_conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag "
        "WHERE session_id = 's1' AND subject_kind = 'node' AND engram_id = ?",
        (engram_id,),
    ).fetchone()["n"]
    assert tag_count == 3  # the 4th (capped) call set no new tag


def test_signal_use_cap_is_per_session_not_global(cfg, db_conn, registered) -> None:
    for _ in range(3):
        signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    # a different session_id starts a fresh cap
    outcome = signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s2")
    assert outcome.capped == []
    assert outcome.tagged


def test_signal_use_generates_bidirectional_co_activation_candidates(cfg, db_conn, registered) -> None:
    a_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    b_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (STEAM_PREFIX,)).fetchone()["id"]

    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    outcome = signals_mod.signal_use(cfg, db_conn, skill_ids=[STEAM_PREFIX], session_id="s1")

    assert outcome.co_activation_candidates == [a_id]

    assert _candidate_evidence(db_conn, a_id, b_id) == 1
    assert _candidate_evidence(db_conn, b_id, a_id) == 1

    edge_tags = db_conn.execute(
        "SELECT edge_src, edge_dst FROM eph_tag WHERE session_id = 's1' AND subject_kind = 'edge'"
    ).fetchall()
    assert {(row["edge_src"], row["edge_dst"]) for row in edge_tags} == {(a_id, b_id), (b_id, a_id)}


def test_signal_use_does_not_reinflate_pairs_untouched_by_the_current_call(cfg, db_conn, registered) -> None:
    """A third, unrelated skill tagged later must not re-witness the
    (A, B) pair that was already live before it arrived."""
    third = "proton-verify-installation"
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    signals_mod.signal_use(cfg, db_conn, skill_ids=[STEAM_PREFIX], session_id="s1")
    signals_mod.signal_use(cfg, db_conn, skill_ids=[third], session_id="s1")

    a_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    b_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (STEAM_PREFIX,)).fetchone()["id"]
    assert _candidate_evidence(db_conn, a_id, b_id) == 1


# ── signal_outcome: the credit-set rule + capture_weight recency ───────────


def test_signal_outcome_explicit_skill_ids_take_priority(cfg, db_conn, registered) -> None:
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON, STEAM_PREFIX], session_id="s1")
    outcome = signals_mod.signal_outcome(
        cfg, db_conn, valence=0.1, skill_ids=[PROTON], session_id="s1"  # low |valence|, explicit skill_ids
    )
    assert outcome.captured == 1
    assert outcome.skills_credited == [
        db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    ]


def test_signal_outcome_retroactive_credit_on_high_salience(cfg, db_conn, registered) -> None:
    assert cfg.theta_salience == 0.7
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON, STEAM_PREFIX], session_id="s1")
    outcome = signals_mod.signal_outcome(cfg, db_conn, valence=0.95, session_id="s1")
    assert outcome.captured == 2
    assert set(outcome.skills_credited) == {
        db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"],
        db_conn.execute("SELECT id FROM engram WHERE name = ?", (STEAM_PREFIX,)).fetchone()["id"],
    }


def test_signal_outcome_captures_nothing_below_salience_threshold(cfg, db_conn, registered) -> None:
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    outcome = signals_mod.signal_outcome(cfg, db_conn, valence=0.2, session_id="s1")
    assert outcome.captured == 0
    assert outcome.skills_credited == []
    assert "did not clear theta_salience" in outcome.note


def test_signal_outcome_capture_weight_decays_with_recency(cfg, db_conn, registered) -> None:
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]

    stale_set_at = (datetime.now(UTC) - timedelta(seconds=cfg.tau_credit_seconds)).isoformat()
    db_conn.execute(
        "UPDATE eph_tag SET set_at = ? WHERE session_id = 's1' AND engram_id = ?",
        (stale_set_at, engram_id),
    )

    signals_mod.signal_outcome(cfg, db_conn, valence=1.0, skill_ids=[PROTON], session_id="s1")
    row = db_conn.execute(
        "SELECT capture_weight FROM eph_tag WHERE session_id = 's1' AND engram_id = ?", (engram_id,)
    ).fetchone()
    # capture_weight = salience * exp(-dt/tau_credit); dt ~= tau_credit here -> exp(-1)
    assert row["capture_weight"] == pytest.approx(0.5 * math.exp(-1), rel=1e-2)


def test_signal_outcome_unknown_explicit_skill_id_is_rejected(cfg, db_conn, registered) -> None:
    with pytest.raises(InvalidInputError):
        signals_mod.signal_outcome(cfg, db_conn, valence=1.0, skill_ids=["nope"], session_id="s1")


def test_signal_outcome_retroactive_credit_is_bounded(cfg, db_conn, registered) -> None:
    """M4 hardening: spec §3.3 tool 6's "capture(all_tags_alive_in_window)"
    is bounded to cfg.retroactive_credit_max most-recently-tagged skills,
    not literally every live tag -- an unbounded fan-out from one call."""
    cfg.retroactive_credit_max = 2
    all_names = [PROTON, STEAM_PREFIX, "proton-clean-install", "proton-verify-installation"]
    signals_mod.signal_use(cfg, db_conn, skill_ids=all_names, session_id="s1")
    outcome = signals_mod.signal_outcome(cfg, db_conn, valence=0.95, session_id="s1")
    assert outcome.captured == 2
    assert "2 most recently" in outcome.note


def test_signal_outcome_captures_edge_tags_for_credited_pairs(cfg, db_conn, registered) -> None:
    """M4 carry-forward: signal_outcome() now closes the CAPTURE side of
    the edge two-phase commit M3 left built only for nodes -- a
    co_activation edge tag whose endpoint is in the credit set gets
    capture_valence/weight recorded too, so Dream (Phase 2) has real
    outcome-linked evidence to potentiate S_edge from."""
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON, STEAM_PREFIX], session_id="s1")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    steam_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (STEAM_PREFIX,)).fetchone()["id"]

    edge_tags_before = db_conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag WHERE subject_kind = 'edge' AND captured_at IS NOT NULL"
    ).fetchone()["n"]
    assert edge_tags_before == 0

    signals_mod.signal_outcome(cfg, db_conn, valence=0.95, skill_ids=[proton_id], session_id="s1")

    captured_edge_tags = db_conn.execute(
        "SELECT edge_src, edge_dst, edge_type, capture_valence, capture_weight FROM eph_tag "
        "WHERE subject_kind = 'edge' AND captured_at IS NOT NULL"
    ).fetchall()
    assert len(captured_edge_tags) >= 1
    row = captured_edge_tags[0]
    assert {row["edge_src"], row["edge_dst"]} == {proton_id, steam_id}
    assert row["edge_type"] == "co_activation"
    assert row["capture_valence"] == 0.95
    assert row["capture_weight"] is not None


def test_signal_outcome_does_not_capture_edges_unrelated_to_credit_set(cfg, db_conn, registered) -> None:
    """An edge whose neither endpoint is in the credit set is left
    uncaptured -- credit is scoped, not blanket."""
    third = "proton-clean-install"
    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON, STEAM_PREFIX, third], session_id="s1")
    third_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (third,)).fetchone()["id"]

    # Credit ONLY `third` explicitly -- edges between PROTON/STEAM_PREFIX
    # (neither of which is `third`) must not be captured.
    signals_mod.signal_outcome(cfg, db_conn, valence=0.95, skill_ids=[third_id], session_id="s1")

    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    steam_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (STEAM_PREFIX,)).fetchone()["id"]
    uncredited_edge_captured = db_conn.execute(
        "SELECT COUNT(*) AS n FROM eph_tag WHERE subject_kind = 'edge' AND captured_at IS NOT NULL "
        "AND ((edge_src = ? AND edge_dst = ?) OR (edge_src = ? AND edge_dst = ?))",
        (proton_id, steam_id, steam_id, proton_id),
    ).fetchone()["n"]
    assert uncredited_edge_captured == 0


def test_signal_outcome_never_writes_a_learned_S_value(cfg, db_conn, registered) -> None:
    """Belt-and-suspenders on AC-014 at the signals layer: signal_outcome()
    only ever touches eph_tag capture columns, never engram.storage_strength."""
    engram_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    before = db_conn.execute(
        "SELECT storage_strength FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["storage_strength"]

    signals_mod.signal_use(cfg, db_conn, skill_ids=[PROTON], session_id="s1")
    signals_mod.signal_outcome(cfg, db_conn, valence=1.0, skill_ids=[PROTON], session_id="s1")

    after = db_conn.execute(
        "SELECT storage_strength FROM engram WHERE id = ?", (engram_id,)
    ).fetchone()["storage_strength"]
    assert after == before
