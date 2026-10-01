"""AC-S07-04: reranker timeout / missing model → fallback or operational error."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from magicite.core import router as router_mod
from magicite.engram import ids as ids_mod
from magicite.storage import ephemeral as ephemeral_mod


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_engram(cfg, conn, engram_id: str, name: str) -> str:
    body = f"## Procedure\n{name}\n"
    raw = f"""---
spec: engram/0.2
name: {name}
id: {engram_id}
version: 1
provenance: authored
intent:
  does: "does {name}"
  use_when: "use when {name}"
  not_when: "never"
triggers:
  positive: ["{name}"]
  negative: []
context_affinity: []
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
inhibits: []
provenance_journal: []
trust:
  origin: authored
  verification_status: verified
---
{body}"""
    # Engram ids must be egr_[0-9a-f]{8}; tests use that shape.
    rel = f".magicite/engrams/{name}.egr.md"
    full = cfg.project_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    data = raw.encode("utf-8")
    full.write_bytes(data)
    digest = ids_mod.content_sha256(data)
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
            rel,
            "engram/0.2",
            1,
            "authored",
            "verified",
            "nascent",
            "does",
            "use_when",
            now,
            digest,
            digest,
            digest,
            now,
            now,
        ),
    )
    from tests.support.custody_adapter import review_inserted_source

    return review_inserted_source(cfg, conn, path=full, source=data)


def _embed_and_store(conn, embedder, engram_id: str, text: str, digest: str) -> None:
    vec = embedder.embed(text)
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=engram_id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=vec,
        source_sha256=digest,
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
    router_mod._SUBJECT_CACHE.clear()
    query = "timeout fallback query"
    digest = _insert_engram(cfg, db_conn, "egr_aa01cc01", "keep-skill")
    _embed_and_store(db_conn, embedder, "egr_aa01cc01", query, digest)

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
    assert outcome.candidates[0].id == "egr_aa01cc01"
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
