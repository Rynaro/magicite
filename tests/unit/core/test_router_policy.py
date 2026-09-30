"""AC-S07-04: reranker timeout / missing model → fallback or operational error."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from magicite.core import router as router_mod
from magicite.storage import ephemeral as ephemeral_mod


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_engram(conn, engram_id: str, name: str) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, storage_strength, s_decayed_at, excitability,
          identity_sha256, content_sha256, body_sha256, file_mtime_ns, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?, 0.0, ?, 0.05, ?,?,?, 0, ?, ?)
        """,
        (
            engram_id,
            name,
            f"{name}.egr.md",
            "engram/0.2",
            1,
            "authored",
            "verified",
            "nascent",
            "does",
            "use_when",
            now,
            engram_id,
            engram_id,
            engram_id,
            now,
            now,
        ),
    )


def _embed_and_store(conn, embedder, engram_id: str, text: str) -> None:
    vec = embedder.embed(text)
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=engram_id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=vec,
        source_sha256=engram_id,
    )


class _TimeoutReranker:
    model_id = "timeout-reranker"
    model_digest = "a" * 64

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        *,
        token_budget: int,
        timeout_s: float,
    ) -> Sequence[Any]:
        del query, candidates, token_budget
        import time

        time.sleep(timeout_s + 1.0)
        return []


def test_fallback_identity(cfg, db_conn, embedder, monkeypatch) -> None:
    """GIVEN a reranker timeout or missing required model
    WHEN routing runs
    THEN the result SHALL identify configured fallback or explicit operational error.
    """
    query = "timeout fallback query"
    _insert_engram(db_conn, "egr_keep", "keep-skill")
    _embed_and_store(db_conn, embedder, "egr_keep", query)

    # --- missing required model with configured fallback ---
    cfg.reranker_provider = "missing-model-xyz"
    cfg.reranker_required = True
    cfg.reranker_fallback = "dense-v1"
    cfg.reranker_timeout_s = 0.05

    outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=3)
    assert outcome.decision is not None
    assert outcome.decision.fallback_identity == "dense-v1"
    assert outcome.decision.operational_error is None
    assert outcome.decision.status == "selected"
    assert outcome.candidates
    assert outcome.candidates[0].id == "egr_keep"
    assert outcome.decision.selection_mechanism == "dense-v1"

    # --- missing required model without fallback → operational error ---
    cfg.reranker_fallback = ""
    err_outcome = router_mod.route(cfg, db_conn, embedder, query=query, k=3)
    assert err_outcome.decision is not None
    assert err_outcome.decision.status == "error"
    assert err_outcome.decision.operational_error == "reranker_model_missing"
    assert err_outcome.decision.fallback_identity is None
    assert err_outcome.candidates == []
    # Selection-quality abstention codes must not disguise the ops failure.
    assert "below_score_threshold" not in err_outcome.decision.reason_codes

    # --- timeout with fallback ---
    cfg.reranker_provider = "timeout"
    cfg.reranker_fallback = "dense-v1"
    cfg.reranker_required = True

    def _fake_get_reranker(provider: str = "noop"):
        if provider == "timeout":
            return _TimeoutReranker()
        raise ValueError(provider)

    monkeypatch.setattr("magicite.embeddings.reranker.get_reranker", _fake_get_reranker)
    timed = router_mod.route(cfg, db_conn, embedder, query=query, k=3)
    assert timed.decision is not None
    assert timed.decision.fallback_identity == "dense-v1"
    assert timed.decision.status == "selected"
    assert timed.candidates
