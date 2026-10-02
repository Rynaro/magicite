from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.core import router as router_mod
from magicite.core import routing_policy as policy_mod
from tests.conftest import TOY_ENGRAM_NAMES


def test_route_mints_session_id_when_omitted(cfg, db_conn, embedder, review_fixture_artifacts) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(cfg, db_conn, embedder, query="steam wont open", k=3)
    assert outcome.session_id
    row = db_conn.execute(
        "SELECT session_id FROM eph_session WHERE session_id = ?", (outcome.session_id,)
    ).fetchone()
    assert row is not None


def test_route_reuses_explicit_session_id(cfg, db_conn, embedder, review_fixture_artifacts) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(cfg, db_conn, embedder, query="steam wont open", k=3, session_id="my-session")
    assert outcome.session_id == "my-session"


def test_route_on_empty_registry_returns_no_candidates(
    cfg, db_conn, embedder, review_fixture_artifacts
) -> None:
    outcome = router_mod.route(cfg, db_conn, embedder, query="anything at all", k=5)
    assert outcome.candidates == []
    assert outcome.composition_plan == []
    assert outcome.registry_size == 0
    assert outcome.policy_id == policy_mod.POLICY_DENSE_V1


def test_route_respects_k(cfg, db_conn, embedder, review_fixture_artifacts) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(cfg, db_conn, embedder, query="proton", k=2)
    assert len(outcome.candidates) == 2


def test_route_context_strings_are_echoed_as_unresolved(
    cfg, db_conn, embedder, review_fixture_artifacts
) -> None:
    # Context unresolved echo is part of the experimental adaptive path;
    # stable dense-v1 does not apply soft context conditioning (S06 owns eligibility).
    cfg.routing_policy = policy_mod.POLICY_EXPERIMENTAL_ADAPTIVE_BLEND_V1
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    outcome = router_mod.route(
        cfg,
        db_conn,
        embedder,
        query="steam wont open",
        context={"project_tag": "steam-gaming", "recent_failures": ["OOMKilled"]},
        k=3,
    )
    assert "steam-gaming" in outcome.unresolved_context
    assert "OOMKilled" in outcome.unresolved_context


def _asset_bound_engram(cfg, asset_bytes: bytes):
    from magicite.engram.digests import asset_bytes_digest, routing_body_digest

    asset_rel = "assets/tool.txt"
    asset_path = cfg.registry_dir / asset_rel
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(asset_bytes)
    body = (
        "## Procedure\n"
        "1. Use the bundled asset.\n"
        "## Pitfalls\n"
        "- Ignoring asset digest binding\n"
        "## Examples\n"
        "+ stable assets\n"
        "- mutated assets\n"
    )
    egr = cfg.registry_dir / "asset-bound.egr.md"
    egr.write_text(
        "---\n"
        "spec: engram/1.0\n"
        "name: asset-bound\n"
        "id: egr_a55e7001\n"
        "version: 1\n"
        "intent:\n"
        "  does: Bind resource digests into local admission\n"
        "  use_when: assets accompany an imported skill\n"
        "  not_when: assets can mutate after approval\n"
        "routing:\n"
        "  positive: [asset bound]\n"
        "  negative: [mutable asset]\n"
        f'  body_digest: "{routing_body_digest(body)}"\n'
        "origin:\n"
        "  channel: imported\n"
        "  verification_status: pending\n"
        "assets:\n"
        f"  {asset_rel}:\n"
        f'    sha256: "{asset_bytes_digest(asset_bytes)}"\n'
        f"    size: {len(asset_bytes)}\n"
        "    media_type: text/plain\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )
    return egr, asset_path


def test_route_reverifies_asset_after_same_size_mutation_with_mtime_restored(
    cfg, db_conn, embedder
) -> None:
    import os

    egr, asset_path = _asset_bound_engram(cfg, b"asset-v1")
    outcome = registry_mod.register(cfg, db_conn, embedder, path=str(egr))
    assert outcome.ingested == 1
    engram_id = outcome.registered[0].id
    digest = db_conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)).fetchone()[
        "content_sha256"
    ]
    registry_mod.review_approve(
        cfg, db_conn, engram_id=engram_id, expected_digest=digest, actor="reviewer", event_id="evt-r3-asset"
    )

    query = "asset bound imported skill assets"
    # Positive control: the approved, untouched artifact routes (and primes the subject cache).
    first = router_mod.route(cfg, db_conn, embedder, query=query, k=3)
    assert engram_id in [c.id for c in first.candidates]
    again = router_mod.route(cfg, db_conn, embedder, query=query, k=3)
    assert engram_id in [c.id for c in again.candidates]

    st = asset_path.stat()
    asset_path.write_bytes(b"asset-v2")  # same size, different bytes
    os.utime(asset_path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert asset_path.stat().st_size == st.st_size
    assert asset_path.stat().st_mtime_ns == st.st_mtime_ns

    after = router_mod.route(cfg, db_conn, embedder, query=query, k=3)
    assert engram_id not in [c.id for c in after.candidates]
