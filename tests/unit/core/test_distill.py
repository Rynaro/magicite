"""``core/distill.py``: frequent-path mining -> nucleation *proposals*
(spec §4.3 Phase 5, §3.3 tool 13, CR-3). Never writes an engram."""

from __future__ import annotations

from magicite.core import distill as distill_mod
from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod

PROTON = "proton-ge-proton-downgrade"
STEAM_PREFIX = "steam-prefix-access"
NVIDIA = "nvidia-prime-render-offload"
LUTRIS = "lutris-wine-prefix-setup"


def _positive_sessions(cfg, db_conn, names: list[str], count: int, *, valence: float = 0.9) -> None:
    for i in range(count):
        sid = f"s-{'-'.join(names)}-{i}"
        signals_mod.signal_use(cfg, db_conn, skill_ids=names, session_id=sid)
        signals_mod.signal_outcome(
            cfg, db_conn, valence=valence, salience=0.9, skill_ids=names, session_id=sid
        )


def test_mine_frequent_paths_below_support_is_empty(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [NVIDIA, LUTRIS], 4)
    candidates = distill_mod.mine_frequent_paths(cfg, db_conn, min_support=5)
    assert candidates == []


def test_mine_frequent_paths_finds_an_uncovered_combination(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [NVIDIA, LUTRIS], 5)
    candidates = distill_mod.mine_frequent_paths(cfg, db_conn, min_support=5)
    assert len(candidates) == 1
    assert candidates[0].path_names == sorted([NVIDIA, LUTRIS])
    assert candidates[0].support == 5


def test_mine_frequent_paths_excludes_a_combination_already_covered(cfg, db_conn, embedder) -> None:
    """docs/03 phase 5: "no single engram covering them" -- PROTON already
    declares `needs: [steam-prefix-access]` (a depends_on edge), so this
    combination is already representable and must not be proposed."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [PROTON, STEAM_PREFIX], 5)
    candidates = distill_mod.mine_frequent_paths(cfg, db_conn, min_support=5)
    assert candidates == []


def test_mine_frequent_paths_ignores_low_valence_sessions(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [NVIDIA, LUTRIS], 5, valence=0.1)  # below theta_salience
    candidates = distill_mod.mine_frequent_paths(cfg, db_conn, min_support=5)
    assert candidates == []


def test_mine_frequent_paths_ignores_single_skill_sessions(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    for i in range(5):
        sid = f"solo-{i}"
        signals_mod.signal_use(cfg, db_conn, skill_ids=[NVIDIA], session_id=sid)
        signals_mod.signal_outcome(
            cfg, db_conn, valence=0.9, salience=0.9, skill_ids=[NVIDIA], session_id=sid
        )
    candidates = distill_mod.mine_frequent_paths(cfg, db_conn, min_support=5)
    assert candidates == []


def test_run_distillation_creates_a_proposal_and_no_engram(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [NVIDIA, LUTRIS], 5)
    before = db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]

    outcome = distill_mod.run_distillation(cfg, db_conn, min_support=5, proposed_by="test-worker")

    assert len(outcome.approval_ids) == 1
    assert len(outcome.candidates) == 1
    row = db_conn.execute(
        "SELECT state, op, proposed_by FROM approval WHERE id = ?", (outcome.approval_ids[0],)
    ).fetchone()
    assert row["state"] == "proposed"
    assert row["op"] == "nucleate"
    assert row["proposed_by"] == "test-worker"
    after = db_conn.execute("SELECT COUNT(*) AS n FROM engram").fetchone()["n"]
    assert after == before


def test_run_distillation_mirrors_the_approval_to_disk(cfg, db_conn, embedder) -> None:
    """spec §5.2: every approval row is mirrored to
    .spectra/approvals/<id>.json -- same discipline sharpen/promote/
    archive already follow."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [NVIDIA, LUTRIS], 5)

    outcome = distill_mod.run_distillation(cfg, db_conn, min_support=5, proposed_by="test-worker")

    mirror_path = cfg.approvals_dir / f"{outcome.approval_ids[0]}.json"
    assert mirror_path.is_file()


def test_draft_skeleton_is_a_scaffold_not_a_valid_engram(cfg, db_conn, embedder) -> None:
    """CR-3: the skeleton is a mechanical, clearly-labelled draft the host
    agent must fill in -- never itself a complete, registrable engram."""
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    _positive_sessions(cfg, db_conn, [NVIDIA, LUTRIS], 5)
    candidates = distill_mod.mine_frequent_paths(cfg, db_conn, min_support=5)
    skeleton = candidates[0].draft_skeleton
    assert "<host:" in skeleton
    assert "DRAFT" in skeleton
