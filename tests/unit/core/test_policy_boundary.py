"""AC-S00-* policy boundary: stable dense-v1 vs explicit experimental blend."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from magicite.core import dream as dream_mod
from magicite.core import router as router_mod
from magicite.core import routing_policy as policy_mod
from magicite.core import signals as signals_mod
from magicite.storage import ephemeral as ephemeral_mod
from tests.conftest import TOY_ENGRAM_NAMES
from tests.support.custody_adapter import review_toy_sources


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_engram(cfg, conn, engram_id: str, name: str, *, excitability: float = 0.05) -> str:
    from magicite.engram import ids as ids_mod

    now = _now()
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
  excitability: {excitability}
  status: nascent
needs: []
inhibits: []
provenance_journal: []
trust:
  origin: authored
  verification_status: verified
---
{body}"""
    rel = f".magicite/engrams/{name}.egr.md"
    full = cfg.project_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    data = raw.encode("utf-8")
    full.write_bytes(data)
    digest = ids_mod.content_sha256(data)
    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, storage_strength, s_decayed_at, excitability,
          identity_sha256, content_sha256, body_sha256, file_mtime_ns, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?, 0.0, ?, ?, ?,?,?, 0, ?, ?)
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
            excitability,
            digest,
            digest,
            digest,
            now,
            now,
        ),
    )
    from tests.support.custody_adapter import review_inserted_source

    return review_inserted_source(cfg, conn, path=full, source=data)


def _insert_edge(
    conn, src_id: str, dst_name: str, dst_id: str | None, edge_type: str, strength: float
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                          evidence_count, provenance, first_observed, dangling)
        VALUES (?,?,?,?,?,?, 3, 'learned', ?, ?)
        """,
        (src_id, dst_name, dst_id, edge_type, strength, now, now, 0 if dst_id else 1),
    )


def _embed_and_store(conn, embedder, engram_id: str, text: str) -> None:
    row = conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)).fetchone()
    digest = row["content_sha256"] if row is not None else engram_id
    vec = embedder.embed(text)
    ephemeral_mod.upsert_embedding(
        conn,
        engram_id=engram_id,
        model_name=embedder.model_name,
        dim=embedder.dim,
        vec=vec,
        source_sha256=digest,
    )


def _semantic_ids(outcome: router_mod.RouteOutcome) -> list[str]:
    return [c.id for c in outcome.candidates]


def test_stable_ignores_adaptation(cfg, db_conn, embedder) -> None:
    """AC-S00-01: usage / strength / community variance must not change stable results."""
    assert cfg.routing_policy == policy_mod.POLICY_DENSE_V1
    query = "shared query text"
    _insert_engram(cfg, db_conn, "egr_aa02ee0a", "a", excitability=0.01)
    _insert_engram(cfg, db_conn, "egr_aa02ee0b", "b", excitability=0.01)
    _embed_and_store(db_conn, embedder, "egr_aa02ee0a", query)
    _embed_and_store(db_conn, embedder, "egr_aa02ee0b", query)

    baseline = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert baseline.policy_id == policy_mod.POLICY_DENSE_V1
    assert baseline.policy_family == "stable"
    baseline_ids = _semantic_ids(baseline)
    baseline_scores = [c.score for c in baseline.candidates]

    # Vary usage / retrieval / node+edge strength / community assignment.
    db_conn.execute(
        "UPDATE engram SET exposure_count = 999, storage_strength = 0.95 WHERE id = 'egr_aa02ee0a'"
    )
    db_conn.execute("UPDATE engram SET excitability = 0.99 WHERE id = 'egr_aa02ee0b'")
    db_conn.execute(
        "INSERT INTO eph_retrieval (engram_id, r, r_decayed_at) VALUES ('egr_aa02ee0a', 1.0, ?)",
        (_now(),),
    )
    _insert_edge(db_conn, "egr_aa02ee0a", "b", "egr_aa02ee0b", "co_activation", 0.99)
    now = _now()
    db_conn.execute(
        "INSERT INTO engram_community (engram_id, community_id, algo, computed_at) VALUES "
        "(?, 1, 'test', ?), (?, 2, 'test', ?)",
        ("egr_aa02ee0a", now, "egr_aa02ee0b", now),
    )

    adapted_state = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert _semantic_ids(adapted_state) == baseline_ids
    assert [c.score for c in adapted_state.candidates] == baseline_scores
    assert adapted_state.policy_digest == baseline.policy_digest
    assert adapted_state.composition_plan == baseline.composition_plan
    assert adapted_state.plan_confidence == baseline.plan_confidence


def test_dream_cannot_change_stable_policy(cfg, db_conn, embedder) -> None:
    """AC-S00-02: Dream checkpoints historical strength without moving stable digest."""
    from magicite.core import registry as registry_mod

    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_toy_sources(cfg, db_conn, names=TOY_ENGRAM_NAMES)
    digest_before = policy_mod.active_stable_policy_digest(cfg)

    # Capture a real signal so Phase 2 has something to potentiate when possible.
    outcome = router_mod.route(cfg, db_conn, embedder, query="steam wont open", k=3)
    assert outcome.candidates
    winner = outcome.candidates[0].id
    signals_mod.signal_use(cfg, db_conn, skill_ids=[winner], session_id=outcome.session_id)
    signals_mod.signal_outcome(
        cfg,
        db_conn,
        valence=1.0,
        skill_ids=[winner],
        session_id=outcome.session_id,
    )

    result = dream_mod.run(cfg, db_conn, trigger="manual")
    digest_after = policy_mod.active_stable_policy_digest(cfg)

    assert digest_before == digest_after
    assert result.stable_policy_digest == digest_before
    assert result.stats["stable_policy"]["unchanged"] is True

    # Stable route semantic result remains identical after Dream strength writes.
    before_ids = _semantic_ids(outcome)
    after = router_mod.route(
        cfg, db_conn, embedder, query="steam wont open", k=3, session_id=outcome.session_id
    )
    assert _semantic_ids(after) == before_ids
    assert after.policy_digest == digest_before


def test_experimental_is_explicit(cfg, db_conn, embedder) -> None:
    """AC-S00-03: experimental selection is visible on the decision."""
    query = "shared query text"
    _insert_engram(cfg, db_conn, "egr_aa02ee0a", "a")
    _insert_engram(cfg, db_conn, "egr_aa02ee0b", "b")
    _embed_and_store(db_conn, embedder, "egr_aa02ee0a", query)
    _embed_and_store(db_conn, embedder, "egr_aa02ee0b", query)

    stable = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert stable.policy_family == "stable"
    assert stable.policy_id == policy_mod.POLICY_DENSE_V1

    cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    experimental = router_mod.route(cfg, db_conn, embedder, query=query, k=5)
    assert experimental.policy_family == "experimental"
    assert experimental.policy_id == policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    assert experimental.policy_digest != stable.policy_digest
    assert experimental.candidates[0].diagnostics.get("policy_experimental") == 1.0


def test_no_raw_query_logging(cfg, db_conn, embedder) -> None:
    """AC-S00-04: persistent route events omit the raw query string."""
    import hashlib

    from magicite.core import fingerprint_key as fingerprint_key_mod

    secret = "SENTINEL_SECRET_TOKEN_S00_DO_NOT_PERSIST"
    _insert_engram(cfg, db_conn, "egr_aa02ee0a", "a")
    _embed_and_store(db_conn, embedder, "egr_aa02ee0a", "benign text")

    router_mod.route(cfg, db_conn, embedder, query=secret, k=3)

    key = fingerprint_key_mod.load_or_create_fingerprint_key(cfg)
    expected_fp = fingerprint_key_mod.query_fingerprint(secret, key=key)
    unsalted = hashlib.sha256(secret.encode("utf-8")).hexdigest()

    rows = db_conn.execute(
        "SELECT payload_json FROM eph_event WHERE tool = 'route' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    assert rows
    for row in rows:
        payload = json.loads(row["payload_json"])
        blob = json.dumps(payload)
        assert secret not in blob
        assert "query" not in payload
        assert "query_sha256" not in payload
        assert unsalted not in blob
        assert payload.get("query_fingerprint") == expected_fp
        assert payload.get("fingerprint_scheme") == fingerprint_key_mod.FINGERPRINT_SCHEME
        assert payload.get("policy_id") == policy_mod.POLICY_DENSE_V1
