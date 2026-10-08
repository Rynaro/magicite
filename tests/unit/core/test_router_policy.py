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


BASELINES = ("experimental/sparse-v1", "experimental/trigger-v1", "experimental/hybrid-rrf-v1")


def _baseline_registry(cfg, conn, embedder):
    """Live reviewed skills and durable published generation, no model acquisition."""
    from magicite.core import index_generation as generation
    from magicite.core import trust
    from magicite.engram import parser
    from magicite.storage.lease import writer_lease

    ids = ("egr_aa01cc11", "egr_aa01cc12", "egr_aa01cc13")
    names = ("orchid", "orchid-orchid", "vector")
    for nid, name in zip(ids, names, strict=True):
        digest = _insert_engram(cfg, conn, nid, name)
        _embed_and_store(conn, embedder, nid, "orchid" if name == "vector" else "different", digest)
    catalog = generation.IndexCatalog(conn)
    fp = generation.IndexFingerprint(
        provider="hashing",
        dimension=embedder.dim,
        model_artifact_digest=generation.model_artifact_digest(
            model_name=embedder.model_name, dim=embedder.dim
        ),
    )
    with writer_lease("baseline-test"):
        gid = catalog.begin(snapshot_id="baseline-snapshot", fingerprint=fp)
        projections = []
        for row in conn.execute("SELECT * FROM engram"):
            full = cfg.project_root / row["path"]
            raw = full.read_text()
            artifact, _ = parser.parse_artifact(
                raw,
                relpath=row["path"],
                admit=False,
                registry_root=cfg.registry_dir,
                require_asset_files=False,
            )
            _, body = parser.split_frontmatter(raw)
            proj = generation.project_artifact(artifact, raw_body_text=body)
            vector = conn.execute(
                "SELECT vec FROM eph_embedding WHERE engram_id=? AND model=?",
                (row["id"], embedder.model_name),
            ).fetchone()["vec"]
            import numpy as np

            catalog.add_entry(
                gid,
                proj,
                dense_vec=np.frombuffer(vector, dtype=np.float32),
                asset_digest=trust.resource_digest_for_artifact(cfg, artifact),
            )
            projections.append(proj)
        catalog.complete(gid, projections, expected_fingerprint=fp)
        catalog.publish(gid)
    return ids, gid


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_declared_dispatch_and_raw_scores(cfg, db_conn, embedder, monkeypatch, policy):
    from magicite.core import candidates as source

    ids, gid = _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy

    def forbidden(*args, **kwargs):
        pytest.fail("baseline dispatched adaptive blend")

    monkeypatch.setattr(router_mod, "_route_adaptive_blend_v1", forbidden)
    outcome = router_mod.route(cfg, db_conn, embedder, query="orchid", k=3)
    d = outcome.decision
    assert d is not None and d.status == "selected", d
    assert d.policy_id == policy and d.policy_family == "experimental"
    assert d.index_generation_id == gid and d.snapshot_id == "baseline-snapshot"
    assert d.confidence.value is None
    expected_sources = (
        {"sparse"} if "sparse" in policy else {"trigger"} if "trigger" in policy else {"dense", "sparse"}
    )
    for candidate in d.candidates:
        comps = d.score_components[candidate.id]
        assert {key[:-6] for key in comps if key.endswith("_score")} <= expected_sources
        if len(expected_sources) == 1:
            assert candidate.score == comps[f"{next(iter(expected_sources))}_score"]
        else:
            assert candidate.score == pytest.approx(
                sum(1 / (60 + comps[f"{name}_rank"]) for name in expected_sources if f"{name}_rank" in comps)
            )
    # Lexical and trigger policy cannot silently use the dense winner.
    if len(expected_sources) == 1:
        assert d.selected_ids[0] != ids[2]
    assert source.DEFAULT_RRF_K == 60


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_absent_generation_errors_even_empty(cfg, db_conn, embedder, policy):
    cfg.routing_policy = policy
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert out.decision.status == "error"
    assert out.decision.operational_error == "index_generation_absent"


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_live_drift_after_warm_denied_before_sources(cfg, db_conn, embedder, monkeypatch, policy):
    from magicite.core import candidates as source

    ids, _ = _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    assert router_mod.route(cfg, db_conn, embedder, query="orchid").decision.status == "selected"
    path = (
        cfg.project_root / db_conn.execute("SELECT path FROM engram WHERE id=?", (ids[0],)).fetchone()["path"]
    )
    path.write_bytes(path.read_bytes() + b"\nDrift\n")
    actual_generate = source.generate

    def checked(*args, **kwargs):
        assert ids[0] not in kwargs["eligible_ids"]
        return actual_generate(*args, **kwargs)

    monkeypatch.setattr(source, "generate", checked)
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert ids[0] not in [candidate.id for candidate in out.candidates]
    assert any(item.engram_id == ids[0] for item in out.decision.exclusions)


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_stale_projection_is_operational_error(cfg, db_conn, embedder, policy):
    ids, gid = _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    db_conn.execute(
        "UPDATE index_entry SET projection_sha256=? WHERE generation_id=? AND engram_id=?",
        ("0" * 64, gid, ids[0]),
    )
    db_conn.commit()
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert out.decision.status == "error"
    assert out.decision.operational_error == "index_generation_stale"


@pytest.mark.parametrize("policy", (BASELINES[0], BASELINES[2]))
def test_baseline_missing_sparse_never_falls_back(cfg, db_conn, embedder, monkeypatch, policy):
    from magicite.core import candidates as source

    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    original = source.RetrievalIndex.from_catalog

    def absent(*args, **kwargs):
        index = original(*args, **kwargs)
        index.sparse_conn = None
        return index

    monkeypatch.setattr(source.RetrievalIndex, "from_catalog", absent)
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert out.decision.status == "error"
    assert out.decision.operational_error == "sparse_capability_unavailable"
    assert not out.candidates


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_reranker_fallback_names_actual_mechanism(cfg, db_conn, embedder, policy):
    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    cfg.reranker_provider = "missing-provider"
    cfg.reranker_required = True
    cfg.reranker_fallback = "dense-v1"
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert out.decision.status == "selected"
    assert out.decision.fallback_identity == policy
    assert out.decision.selection_mechanism == policy


@pytest.mark.parametrize("fault", ("model", "assets", "snapshot", "dimension", "batch"))
def test_baseline_generation_binding_faults(cfg, db_conn, embedder, monkeypatch, fault):
    from dataclasses import replace

    from magicite.core import candidates as source

    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = BASELINES[2]
    original = source.RetrievalIndex.from_catalog

    def corrupt(*args, **kwargs):
        index = original(*args, **kwargs)
        if fault == "model":
            index.fingerprint = replace(index.fingerprint, model_artifact_digest="0" * 64)
        elif fault == "assets":
            first = next(iter(index.entries))
            index.entries[first] = replace(index.entries[first], asset_digest="0" * 64)
        elif fault == "snapshot":
            index.snapshot_id = "wrong"
        elif fault == "dimension":
            index.fingerprint = replace(index.fingerprint, dimension=embedder.dim + 1)
        return index

    monkeypatch.setattr(source.RetrievalIndex, "from_catalog", corrupt)
    if fault == "batch":
        generate = source.generate
        monkeypatch.setattr(
            source, "generate", lambda *a, **kw: replace(generate(*a, **kw), generation_id="wrong")
        )
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert out.decision.status == "error" and not out.candidates
    assert out.decision.operational_error == "index_generation_stale"


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_empty_match_and_threshold_confidence(cfg, db_conn, embedder, policy):
    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    cfg.abstention_score_threshold = 1000.0
    out = router_mod.route(cfg, db_conn, embedder, query="unmatched-zymology")
    assert out.decision.status == "abstained"
    assert out.decision.confidence.value is None
    assert cfg.abstention_score_threshold == 1000.0


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_revoked_top_refills_live_slate(cfg, db_conn, embedder, monkeypatch, policy):
    from magicite.core import candidates as source
    from magicite.core import registry

    ids, _ = _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    warm = router_mod.route(cfg, db_conn, embedder, query="orchid", k=1)
    top = warm.candidates[0].id
    registry.review_revoke(
        cfg,
        db_conn,
        engram_id=top,
        expected_digest=db_conn.execute("SELECT content_sha256 FROM engram WHERE id=?", (top,)).fetchone()[0],
        actor="test-operator",
    )
    generate = source.generate

    def checked(*a, **kw):
        assert top not in kw["eligible_ids"]
        return generate(*a, **kw)

    monkeypatch.setattr(source, "generate", checked)
    out = router_mod.route(cfg, db_conn, embedder, query="orchid", k=1)
    assert out.decision.status == "selected"
    assert out.candidates[0].id in ids and out.candidates[0].id != top


@pytest.mark.parametrize("policy", BASELINES)
def test_baseline_rejects_authenticated_dense_calibration(cfg, db_conn, embedder, policy):
    from magicite.core import calibration as cal
    from magicite.core import routing_policy as policies

    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    config_digest = policies.compute_config_digest(cfg)
    artifact = cal.fit_abstention(
        [
            cal.CalibrationExample(
                query_fingerprint="a" * 64, top_score=0.9, margin=0.5, label_relevant=True
            ),
            cal.CalibrationExample(
                query_fingerprint="b" * 64, top_score=0.0, margin=0.0, label_relevant=False
            ),
        ],
        cfg=cfg,
        policy_id="dense-v1",
        policy_digest=policies.compute_policy_digest("dense-v1", cfg),
        config_digest=config_digest,
        rejection_queries=(),
    )
    cal.save_calibration(cfg, artifact)
    assert cal.load_calibration(cfg, expected_config_digest=config_digest) is not None
    out = router_mod.route(cfg, db_conn, embedder, query="orchid")
    assert out.decision.status == "selected"
    assert out.decision.confidence.value is None
    assert out.decision.calibration_digest is None


@pytest.mark.parametrize("policy", BASELINES)
@pytest.mark.parametrize("change", ("missing", "symlink", "context"))
def test_baseline_warm_assets_context_filtered_before_sources(
    cfg, db_conn, embedder, monkeypatch, policy, change
):
    from magicite.core import candidates as source
    from magicite.core import registry
    from magicite.core.context import RouteContext
    from tests.unit.core.test_router_core import _asset_bound_engram

    path, asset = _asset_bound_engram(cfg, b"asset-v1")
    path.write_text(path.read_text().replace("routing:\n", "compatibility:\n  os: [linux]\nrouting:\n"))
    registered = registry.register(cfg, db_conn, embedder, path=str(path))
    identity = registered.registered[0].id
    digest = db_conn.execute("SELECT content_sha256 FROM engram WHERE id=?", (identity,)).fetchone()[0]
    registry.review_approve(cfg, db_conn, engram_id=identity, expected_digest=digest, actor="test-operator")
    _baseline_registry(cfg, db_conn, embedder)
    cfg.routing_policy = policy
    warm = router_mod.route(
        cfg, db_conn, embedder, query="asset bound", k=5, route_context=RouteContext(platform="linux")
    )
    assert identity in [c.id for c in warm.candidates]
    if change == "missing":
        asset.unlink()
    elif change == "symlink":
        outside = cfg.project_root / "outside-asset"
        outside.write_bytes(asset.read_bytes())
        asset.unlink()
        asset.symlink_to(outside)
    actual = source.generate

    def checked(*a, **kw):
        assert identity not in kw["eligible_ids"]
        return actual(*a, **kw)

    monkeypatch.setattr(source, "generate", checked)
    out = router_mod.route(
        cfg,
        db_conn,
        embedder,
        query="asset bound",
        k=5,
        route_context=RouteContext(platform="macos" if change == "context" else "linux"),
    )
    assert identity not in [c.id for c in out.candidates]
    assert any(item.engram_id == identity for item in out.decision.exclusions)
