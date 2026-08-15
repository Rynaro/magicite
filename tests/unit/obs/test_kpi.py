"""``obs/kpi.py``: standing KPI computation (docs/07), read-only."""

from __future__ import annotations

from magicite.core import registry as registry_mod
from magicite.obs import kpi as kpi_mod

PROTON = "proton-ge-proton-downgrade"


def test_cold_start_signal_below_reference_size() -> None:
    signal = kpi_mod.cold_start_signal(7)
    assert signal["below_reference_size"] is True
    assert signal["cold_start_reference_size"] == kpi_mod.COLD_START_REFERENCE_SIZE
    assert "unevidenced" in signal["note"] or "no claim is made" in signal["note"]


def test_cold_start_signal_at_or_above_reference_size_does_not_assert_a_verdict() -> None:
    """VIVI, M7 conformance fix: crossing the ~50-skill heuristic must
    never be reported as a resolved break-even -- docs/01's Falsification
    Record measured a 70-skill (above-reference) registry and found dense
    embedding retrieval still beat the full pipeline (p=0.00053)."""
    signal = kpi_mod.cold_start_signal(200)
    assert signal["below_reference_size"] is False
    assert "not evidence" in signal["note"] or "no routing-quality verdict is inferred" in signal["note"]


def test_fitness_distribution_buckets_by_storage_strength(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    db_conn.execute("UPDATE engram SET storage_strength = 0.9 WHERE id = ?", (proton_id,))

    dist = kpi_mod.fitness_distribution(db_conn)

    assert dist["total"] == 7
    assert dist["histogram"]["promoted"]["count"] == 1
    assert dist["histogram"]["nascent_or_failed"]["count"] == 6


def test_top_n_traffic_share_empty_registry_is_zero(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    share = kpi_mod.top_n_traffic_share(db_conn, n=5)
    assert share["share"] == 0.0
    assert share["exceeds_target"] is False


def test_top_n_traffic_share_flags_concentration(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    proton_id = db_conn.execute("SELECT id FROM engram WHERE name = ?", (PROTON,)).fetchone()["id"]
    db_conn.execute(
        "INSERT INTO eph_bookkeeping (engram_id, exposure_delta, route_returns) VALUES (?, 0, 100)",
        (proton_id,),
    )
    share = kpi_mod.top_n_traffic_share(db_conn, n=1)
    assert share["share"] == 1.0
    assert share["exceeds_target"] is True


def test_compute_standing_kpis_is_read_only(cfg, db_conn, embedder) -> None:
    registry_mod.register(cfg, db_conn, embedder, path=".spectra/engrams")
    before = {dict(r)["id"]: dict(r) for r in db_conn.execute("SELECT * FROM engram").fetchall()}

    result = kpi_mod.compute_standing_kpis(cfg, db_conn)

    after = {dict(r)["id"]: dict(r) for r in db_conn.execute("SELECT * FROM engram").fetchall()}
    assert before == after
    assert result["registry_size"] == 7
    assert result["silent_engrams"]["count"] == 7
    assert result["silent_engrams"]["pct"] == 1.0
    assert result["silent_engrams"]["systematically_poor"] is True
