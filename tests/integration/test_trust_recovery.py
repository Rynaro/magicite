"""AC-S04-04 — concurrent review idempotency and trust ledger rebuild."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db as db_mod

pytestmark = pytest.mark.acceptance


def _subject(cfg: Config) -> tuple[str, str]:
    incoming = cfg.project_root / "incoming"
    incoming.mkdir(exist_ok=True)
    path = incoming / "replay.egr.md"
    path.write_text(
        "---\n"
        "spec: engram/0.2\n"
        "name: replay-subject\n"
        "id: egr_a0a0a0a0\n"
        "version: 1\n"
        "provenance: imported\n"
        "intent:\n"
        "  does: Survive concurrent review and DB rebuild\n"
        "  use_when: testing trust ledger durability\n"
        "  not_when: losing admissions on rebuild\n"
        "triggers:\n"
        "  positive: [trust recovery]\n"
        "  negative: [silent drop]\n"
        "---\n"
        "## Procedure\n"
        "1. Approve once under an event id.\n"
        "## Pitfalls\n"
        "- Duplicate admits for one event\n"
        "## Examples\n"
        "+ single applied decision\n"
        "- duplicated ledger rows\n",
        encoding="utf-8",
    )
    embedder = get_embedder(dim=256)
    conn = db_mod.connect(cfg.db_path)
    try:
        outcome = registry_mod.register(cfg, conn, embedder, path="incoming", fmt="egr")
        assert outcome.ingested == 1
        entry = outcome.registered[0]
        digest = conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)).fetchone()[
            "content_sha256"
        ]
        return entry.id, digest
    finally:
        conn.close()


def test_review_replay_rebuild(project_root: Path, custody_for, monkeypatch) -> None:
    """AC-S04-04: concurrent approve with one event_id → one admit; rebuild restores it."""
    cfg = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    from tests.support.custody_adapter import threaded_calls

    threaded_calls(custody_for(cfg), monkeypatch)
    cfg.ensure_dirs()
    engram_id, digest = _subject(cfg)

    def _approve_once() -> str:
        local = Config.load(project_root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        conn = db_mod.connect(local.db_path)
        try:
            decision = registry_mod.review_approve(
                local,
                conn,
                engram_id=engram_id,
                expected_digest=digest,
                actor="reviewer",
                event_id="evt-replay-1",
            )
            return decision.decision_id
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: _approve_once(), range(8)))

    assert len(set(ids)) == 1

    admits = [
        d for d in trust_mod.list_decisions(cfg) if d.decision == "admit" and d.event_id == "evt-replay-1"
    ]
    assert len(admits) == 1

    # Delete the rebuildable DB and sync — trust projection must reload from authenticated journal.
    db_path = cfg.db_path
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix) if suffix else db_path
        p.unlink(missing_ok=True)

    embedder = get_embedder(dim=256)
    conn = db_mod.connect(cfg.db_path)
    try:
        registry_mod.sync(cfg, conn, embedder)
        rows = conn.execute(
            "SELECT decision_id, decision, event_id FROM trust_decision WHERE event_id = ?",
            ("evt-replay-1",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["decision"] == "admit"
        assert trust_mod.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
    finally:
        conn.close()
