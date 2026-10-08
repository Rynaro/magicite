"""AC-S07-04: reranker timeout / missing model → fallback or operational error."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

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


@pytest.mark.parametrize(
    "payload",
    [b"[]", b'"policy_activate"', b'{"op": ', b'{"op": "\xff\xfe"}'],
    ids=["list", "scalar", "truncated", "invalid-utf8"],
)
def test_malformed_mirror_without_store_is_policy_store_corrupt(cfg, db_conn, embedder, payload) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    (cfg.approvals_dir / "m.json").write_bytes(payload)
    out = router_mod.route(cfg, db_conn, embedder, query="anything", k=3)
    assert out.decision is not None
    assert out.decision.status == "error"
    assert out.decision.operational_error == "policy_store_corrupt"
    assert "config_fresh_install" not in out.decision.reason_codes


def test_wellformed_policy_mirror_without_store_is_policy_store_missing(cfg, db_conn, embedder) -> None:
    cfg.approvals_dir.mkdir(parents=True, exist_ok=True)
    (cfg.approvals_dir / "m.json").write_text('{"op": "policy_activate"}', encoding="utf-8")
    out = router_mod.route(cfg, db_conn, embedder, query="anything", k=3)
    assert out.decision is not None
    assert out.decision.operational_error == "policy_store_missing"


def test_no_mirrors_without_store_is_fresh_install(cfg, db_conn, embedder) -> None:
    out = router_mod.route(cfg, db_conn, embedder, query="anything", k=3)
    assert out.decision is not None
    assert out.decision.operational_error is None


@pytest.fixture
def empty_subject_cache():
    router_mod._SUBJECT_CACHE.clear()
    yield
    router_mod._SUBJECT_CACHE.clear()


def _metadata_entry(index):
    from magicite.core.eligibility import EligibilitySubject

    return router_mod._SubjectCacheEntry(
        subject=EligibilitySubject(id=f"egr_{index:08x}"),
        db_digest="a" * 64,
        asset_idents=(),
        resource_digest="b" * 64,
    )


def test_subject_cache_reuses_working_set_beyond_old_limit(empty_subject_cache):
    # A complete sequential sweep just above the former bound must survive a
    # second sweep. This measures retention behavior rather than just a number.
    for index in range(4097):
        router_mod._cache_put(("root", "registry", index), _metadata_entry(index))
    assert all(router_mod._cache_get(("root", "registry", index)) is not None for index in range(4097))


def test_subject_cache_stays_hard_bounded_and_evicts_lru(empty_subject_cache):
    limit = router_mod._SUBJECT_CACHE_MAX
    assert limit <= 16384
    for index in range(limit):
        router_mod._cache_put(("root", "registry", index), _metadata_entry(index))
    first = router_mod._cache_get(("root", "registry", 0))
    router_mod._cache_put(("root", "registry", limit), _metadata_entry(limit))
    assert len(router_mod._SUBJECT_CACHE) == limit
    assert router_mod._cache_get(("root", "registry", 1)) is None
    assert router_mod._cache_get(("root", "registry", 0)) is first


@pytest.mark.parametrize("mutation", ["artifact", "db_digest"])
def test_warm_subject_cache_rechecks_content_identity(cfg, db_conn, mutation, empty_subject_cache):
    import os

    _insert_engram(cfg, db_conn, "egr_cac40001", "warm-identity")
    row = db_conn.execute("SELECT * FROM engram WHERE id='egr_cac40001'").fetchone()
    cached = router_mod._build_subject_entry(cfg, row)
    assert router_mod._build_subject_entry(cfg, row) is cached
    if mutation == "artifact":
        path = cfg.project_root / row["path"]
        before = path.stat()
        raw = path.read_bytes()
        changed = raw.replace(b"warm-identity", b"warn-identity")
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_mtime_ns == before.st_mtime_ns
    else:
        row = dict(row)
        row["content_sha256"] = "f" * 64  # A changed authoritative DB projection cannot hit.
    with pytest.raises(router_mod._SubjectProjectionDenied):
        router_mod._build_subject_entry(cfg, row)


def test_warm_subject_cache_isolates_registry_identity(cfg, db_conn, empty_subject_cache):
    from magicite.core.trust_custodian import CustodianError

    _insert_engram(cfg, db_conn, "egr_cac40002", "registry-identity")
    row = db_conn.execute("SELECT * FROM engram WHERE id='egr_cac40002'").fetchone()
    cached = router_mod._build_subject_entry(cfg, row)
    assert router_mod._build_subject_entry(cfg, row) is cached
    with pytest.raises(CustodianError, match="enrollment marker mismatch"):
        router_mod._build_subject_entry(cfg, row, scope=(cfg.registry_dir.resolve(), "other-registry"))


@pytest.mark.parametrize("change", ["revoke", "policy"])
def test_warm_subject_cache_does_not_cache_trust_decisions(
    cfg, db_conn, embedder, change, empty_subject_cache
):
    from magicite.core import registry, trust

    identity = "egr_cac40003"
    digest = _insert_engram(cfg, db_conn, identity, "warm-trust")
    _embed_and_store(db_conn, embedder, identity, "warm trust query", digest)
    for _ in range(2):
        assert identity in [
            c.id for c in router_mod.route(cfg, db_conn, embedder, query="warm trust query").candidates
        ]
    if change == "revoke":
        registry.review_revoke(
            cfg, db_conn, engram_id=identity, expected_digest=digest, actor="test-operator"
        )
    else:
        trust.save_policy(cfg, trust.TrustPolicy(policy_id="restricted-cache-test", revision=2, roots=()))
    assert identity not in [
        c.id for c in router_mod.route(cfg, db_conn, embedder, query="warm trust query").candidates
    ]


@pytest.mark.parametrize("change", ["missing", "symlink", "context"])
def test_warm_subject_cache_rechecks_asset_and_context(cfg, db_conn, embedder, change, empty_subject_cache):
    from magicite.core import registry
    from magicite.core.context import RouteContext
    from tests.unit.core.test_router_core import _asset_bound_engram

    path, asset = _asset_bound_engram(cfg, b"asset-v1")
    path.write_text(path.read_text().replace("routing:\n", "compatibility:\n  os: [linux]\nrouting:\n"))
    outcome = registry.register(cfg, db_conn, embedder, path=str(path))
    assert outcome.ingested == 1
    identity = outcome.registered[0].id
    digest = db_conn.execute("SELECT content_sha256 FROM engram WHERE id=?", (identity,)).fetchone()[0]
    registry.review_approve(cfg, db_conn, engram_id=identity, expected_digest=digest, actor="test-operator")
    query = "asset bound imported skill assets"
    for _ in range(2):
        assert identity in [
            c.id
            for c in router_mod.route(
                cfg, db_conn, embedder, query=query, route_context=RouteContext(platform="linux")
            ).candidates
        ]
    if change == "missing":
        asset.unlink()
    elif change == "symlink":
        external = cfg.project_root / "external-asset.txt"
        external.write_bytes(asset.read_bytes())
        asset.unlink()
        asset.symlink_to(external)
    context = RouteContext(platform="macos" if change == "context" else "linux")
    assert identity not in [
        c.id for c in router_mod.route(cfg, db_conn, embedder, query=query, route_context=context).candidates
    ]


def test_subject_cache_isolates_roots_with_same_inode_and_registry(
    cfg, db_conn, tmp_path, empty_subject_cache
):
    import os

    from magicite.config import Config

    _insert_engram(cfg, db_conn, "egr_cac40004", "root-isolation")
    row = db_conn.execute("SELECT * FROM engram WHERE id='egr_cac40004'").fetchone()
    scope = router_mod._subject_scope(cfg)
    other = Config.load(tmp_path / "other-root", env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    target = other.project_root / row["path"]
    target.parent.mkdir(parents=True)
    original = cfg.project_root / row["path"]
    os.link(original, target)
    assert router_mod._file_identity(original) == router_mod._file_identity(target)
    first = router_mod._build_subject_entry(cfg, row, scope=scope)
    # Same valid marker/ID/digest/inode; only the resolved root differs. This
    # tests metadata scope isolation, not admission of the second registry.
    second_scope = (other.registry_dir.resolve(), scope[1])
    second = router_mod._build_subject_entry(other, row, scope=second_scope)
    assert second is not first
    assert router_mod._build_subject_entry(cfg, row, scope=scope) is first
    assert router_mod._build_subject_entry(other, row, scope=second_scope) is second
