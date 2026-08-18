"""Frozen AC-041: real process death converges to uninterrupted state."""

from __future__ import annotations

import multiprocessing
import os
import shutil
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import dream as dream_mod
from magicite.core import registry as registry_mod
from magicite.core import signals as signals_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db as db_mod
from magicite.storage import queries as queries_mod

PROTON = "proton-ge-proton-downgrade"
FIXED_RUN_TIME = "2026-08-18T12:00:00+00:00"


def _die_during_dream(project_root: str, selector: str) -> None:
    cfg = Config.load(Path(project_root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    conn = db_mod.connect(cfg.db_path)

    def stop(label: str) -> None:
        if label == selector or (selector == "checkpoint:file" and label.startswith(selector)):
            os._exit(92)

    dream_mod.run(cfg, conn, trigger="crash-test", fault_hook=stop)


def _prepare_identical_roots(cfg: Config, baseline_root: Path) -> tuple[str, Config]:
    conn = db_mod.connect(cfg.db_path)
    try:
        assert registry_mod.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams").ingested == 7
        signals_mod.signal_use(cfg, conn, skill_ids=[PROTON], session_id="crash-recovery")
        signals_mod.signal_outcome(
            cfg,
            conn,
            valence=0.9,
            salience=0.9,
            skill_ids=[PROTON],
            session_id="crash-recovery",
        )
        queued = dream_mod.enqueue(conn, cfg, trigger="crash-test")
        assert queued.consolidation_id is not None
        conn.execute(
            "UPDATE consolidation_run SET started_at = ? WHERE id = ?",
            (FIXED_RUN_TIME, queued.consolidation_id),
        )
    finally:
        conn.close()

    shutil.copytree(cfg.project_root, baseline_root)
    baseline_cfg = Config.load(baseline_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    return queued.consolidation_id, baseline_cfg


def _engram_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted((root / ".magicite" / "engrams").glob("*.egr.md"))
    }


@pytest.mark.parametrize(
    "selector",
    ["phase:potentiate:after-write", "checkpoint:file"],
)
def test_process_death_recovers_to_uninterrupted_state(cfg, tmp_path: Path, selector: str) -> None:
    suffix = f"{selector.split(':')[0]}-{selector.count(':')}"
    baseline_root = tmp_path.parent / f"{tmp_path.name}-baseline-{suffix}"
    run_id, baseline_cfg = _prepare_identical_roots(cfg, baseline_root)

    baseline_conn = db_mod.connect(baseline_cfg.db_path)
    try:
        baseline_result = dream_mod.run(baseline_cfg, baseline_conn, trigger="crash-test")
        baseline_projection = queries_mod.durable_projection(baseline_conn)
        baseline_files = _engram_bytes(baseline_root)
    finally:
        baseline_conn.close()
    assert baseline_result.run_id == run_id

    process = multiprocessing.get_context("spawn").Process(
        target=_die_during_dream,
        args=(str(cfg.project_root), selector),
    )
    process.start()
    process.join(timeout=30)
    assert process.exitcode == 92

    recovered_conn = db_mod.connect(cfg.db_path)
    try:
        interrupted = recovered_conn.execute(
            "SELECT state, phase FROM consolidation_run WHERE id = ?", (run_id,)
        ).fetchone()
        assert interrupted["state"] == "running"
        # Recovery is intentionally fail-closed until the dead holder's
        # lease TTL elapses.  Advance only that external clock condition;
        # the consolidation row and its phase state remain untouched.
        recovered_conn.execute(
            "UPDATE writer_lease SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = 1"
        )
        recovered_result = dream_mod.run(cfg, recovered_conn, trigger="crash-test")
        assert recovered_result.run_id == run_id
        assert queries_mod.durable_projection(recovered_conn) == baseline_projection
        assert _engram_bytes(cfg.project_root) == baseline_files
        assert dream_mod.run(cfg, recovered_conn, trigger="steady-state").modified_engrams == []
    finally:
        recovered_conn.close()
