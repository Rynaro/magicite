"""AC-023's proving unit (``test_inhibition_lowers_score``) plus targeted
coverage for the other M2 route() steps that are not separately gated by
a frozen AC id (hub penalty, context conditioning) -- mirrors
test_composition.py's direct-SQL synthetic-fixture style so each behavior
is isolated from register()/embed() plumbing."""

from __future__ import annotations

from datetime import UTC, datetime

from magicite.core import router as router_mod
from magicite.storage import ephemeral as ephemeral_mod


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _insert_engram(conn, engram_id: str, name: str, *, context_names: list[str] | None = None) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO engram (
          id, name, path, spec_version, version, origin, verification_status, status,
          intent_does, intent_use_when, storage_strength, s_decayed_at, excitability,
          identity_sha256, content_sha256, body_sha256, file_mtime_ns, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?, ?,?, 0.0, ?, 0.05, ?,?,?, 0, ?, ?)
        """,
        (engram_id, name, f"{name}.egr.md", "engram/0.2", 1, "authored", "verified", "nascent",
         "does", "use_when", now, engram_id, engram_id, engram_id, now, now),
    )
    for ctx_name in context_names or []:
        ctx_id = f"ctx_{ctx_name}"
        conn.execute(
            "INSERT OR IGNORE INTO context_node (id, name, kind) VALUES (?,?, 'project')",
            (ctx_id, ctx_name),
        )
        conn.execute(
            "INSERT INTO engram_context (engram_id, context_id, weight) VALUES (?,?,1.0)",
            (engram_id, ctx_id),
        )


def _insert_edge(
    conn, src_id: str, dst_name: str, dst_id: str | None, edge_type: str, strength: float
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO edge (src_id, dst_name, dst_id, type, storage_strength, s_decayed_at,
                          evidence_count, provenance, first_observed, dangling)
        VALUES (?,?,?,?,?,?, 3, 'declared', ?, ?)
        """,
        (src_id, dst_name, dst_id, edge_type, strength, now, now, 0 if dst_id else 1),
    )


def _embed_and_store(conn, embedder, engram_id: str, text: str) -> None:
    vec = embedder.embed(text)
    ephemeral_mod.upsert_embedding(
        conn, engram_id=engram_id, model_name=embedder.model_name, dim=embedder.dim, vec=vec,
        source_sha256=engram_id,
    )


def test_inhibition_lowers_score(cfg, db_conn, embedder) -> None:
    """AC-023: GIVEN an engram whose inhibits edge targets a competitor
    engram WHEN both are activated by a query THEN the inhibited engram's
    score SHALL be strictly lower than without the inhibition edge."""
    _insert_engram(db_conn, "egr_inhibitor", "inhibitor")
    _insert_engram(db_conn, "egr_target", "target")
    _embed_and_store(db_conn, embedder, "egr_inhibitor", "shared query text")
    _embed_and_store(db_conn, embedder, "egr_target", "shared query text")

    baseline = router_mod.route(cfg, db_conn, embedder, query="shared query text", k=5)
    baseline_score = next(c.score for c in baseline.candidates if c.name == "target")
    inhibitor_activation_present = any(c.name == "inhibitor" for c in baseline.candidates)
    assert inhibitor_activation_present

    _insert_edge(db_conn, "egr_inhibitor", "target", "egr_target", "inhibits", 0.8)

    inhibited = router_mod.route(cfg, db_conn, embedder, query="shared query text", k=5)
    inhibited_score = next(c.score for c in inhibited.candidates if c.name == "target")

    assert inhibited_score < baseline_score


def test_hub_penalty_dampens_a_structural_hub(cfg, db_conn, embedder) -> None:
    """Not gated by a numbered AC (AC-023's frozen text is inhibition-only)
    but named explicitly in the M2 story action plan ("hub penalty
    applies"): a node with many incoming composes edges should score
    lower with the penalty active than with it switched off, all else
    identical."""
    _insert_engram(db_conn, "egr_hub", "hub")
    _embed_and_store(db_conn, embedder, "egr_hub", "shared text")
    n_spokes = 40
    for i in range(n_spokes):
        spoke_id = f"egr_spoke{i}"
        _insert_engram(db_conn, spoke_id, f"spoke{i}")
        _embed_and_store(db_conn, embedder, spoke_id, "shared text")
        _insert_edge(db_conn, spoke_id, "hub", "egr_hub", "composes", 1.0)

    cfg.hub_penalty = 0.0
    unpenalized = router_mod.route(cfg, db_conn, embedder, query="shared text", k=1)
    hub_score_unpenalized = next(c.score for c in unpenalized.candidates if c.name == "hub")

    cfg.hub_penalty = 0.15
    penalized = router_mod.route(cfg, db_conn, embedder, query="shared text", k=1)
    hub_score_penalized = next(c.score for c in penalized.candidates if c.name == "hub")

    assert hub_score_penalized < hub_score_unpenalized


def test_context_conditioning_project_tag_boosts_linked_engram(cfg, db_conn, embedder) -> None:
    _insert_engram(db_conn, "egr_a", "a", context_names=["steam-gaming"])
    _insert_engram(db_conn, "egr_b", "b")
    _embed_and_store(db_conn, embedder, "egr_a", "shared text")
    _embed_and_store(db_conn, embedder, "egr_b", "shared text")

    without_context = router_mod.route(cfg, db_conn, embedder, query="shared text", k=5)
    a_without = next(c.score for c in without_context.candidates if c.name == "a")

    with_context = router_mod.route(
        cfg, db_conn, embedder, query="shared text", k=5, context={"project_tag": "steam-gaming"}
    )
    a_with = next(c.score for c in with_context.candidates if c.name == "a")
    b_with = next(c.score for c in with_context.candidates if c.name == "b")

    assert a_with > a_without
    assert a_with > b_with
    assert with_context.unresolved_context == []


def test_context_conditioning_unresolvable_strings_are_echoed(cfg, db_conn, embedder) -> None:
    _insert_engram(db_conn, "egr_a", "a")
    _embed_and_store(db_conn, embedder, "egr_a", "shared text")

    context = {
        "project_tag": "no-such-tag",
        "recent_failures": ["NO_SUCH_FAULT"],
        "user_prefs": ["no-such-name"],
    }
    result = router_mod.route(cfg, db_conn, embedder, query="shared text", k=5, context=context)
    assert set(result.unresolved_context) == {"no-such-tag", "NO_SUCH_FAULT", "no-such-name"}


def test_context_conditioning_user_pref_hard_excludes(cfg, db_conn, embedder) -> None:
    _insert_engram(db_conn, "egr_a", "a")
    _insert_engram(db_conn, "egr_b", "b")
    _embed_and_store(db_conn, embedder, "egr_a", "shared text")
    _embed_and_store(db_conn, embedder, "egr_b", "shared text")

    result = router_mod.route(
        cfg, db_conn, embedder, query="shared text", k=5, context={"user_prefs": ["-a"]}
    )
    names = {c.name for c in result.candidates}
    assert "a" not in names
    assert "b" in names


def test_recent_failures_boosts_matching_fault_class(cfg, db_conn, embedder) -> None:
    _insert_engram(db_conn, "egr_a", "a")
    _insert_engram(db_conn, "egr_b", "b")
    _embed_and_store(db_conn, embedder, "egr_a", "shared text")
    _embed_and_store(db_conn, embedder, "egr_b", "shared text")
    db_conn.execute(
        "INSERT INTO engram_step (engram_id, step_no, text, fault_class) VALUES (?,?,?,?)",
        ("egr_a", 1, "some step", "OOMKilled"),
    )

    result = router_mod.route(
        cfg, db_conn, embedder, query="shared text", k=5, context={"recent_failures": ["OOMKilled"]}
    )
    a_score = next(c.score for c in result.candidates if c.name == "a")
    b_score = next(c.score for c in result.candidates if c.name == "b")
    assert a_score > b_score
    assert result.unresolved_context == []
