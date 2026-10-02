"""Regression (r3-d2): ``similar_to`` kNN edges must not depend on the
ingestion / row order of ``eph_embedding`` (rebuild invariant)."""

from __future__ import annotations

import sqlite3
import struct

import numpy as np

from magicite.core import registry as registry_mod

MODEL = "m"


def _vectors() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(7)
    vecs: dict[str, np.ndarray] = {}
    for i in range(40):
        v = rng.standard_normal(64).astype(np.float32)
        vecs[f"egr_{i:08x}"] = v / np.linalg.norm(v)
    # exact duplicates and a near-identical vector: tie-break cases
    vecs["egr_d0000001"] = vecs["egr_00000003"].copy()
    vecs["egr_d0000002"] = vecs["egr_00000003"].copy()
    near = vecs["egr_00000003"].copy()
    near[0] = np.nextafter(near[0], np.float32(2.0))
    vecs["egr_d0000003"] = near
    return vecs


def _run(order: list[str], vecs: dict[str, np.ndarray], monkeypatch) -> dict:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE engram (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE eph_embedding (engram_id TEXT, model TEXT, vec BLOB)")
    for eid in order:
        conn.execute("INSERT INTO engram VALUES (?,?)", (eid, "n-" + eid))
        conn.execute("INSERT INTO eph_embedding VALUES (?,?,?)", (eid, MODEL, vecs[eid].tobytes()))
    captured: dict = {}
    monkeypatch.setattr(
        registry_mod.durable_mod, "replace_similar_to_edges", lambda _c, nb: captured.update(nb)
    )
    registry_mod._compute_similar_to_edges(conn, MODEL, top_m=5)
    conn.close()
    return {k: captured[k] for k in sorted(captured)}


def _bits(result: dict) -> dict:
    return {
        k: [(n, d, struct.pack("<d", w)) for n, d, w in v] for k, v in result.items()
    }


def test_similar_to_edges_are_ingestion_order_independent(monkeypatch) -> None:
    vecs = _vectors()
    ids = sorted(vecs)
    base = _bits(_run(ids, vecs, monkeypatch))
    rng = np.random.default_rng(1)
    orders = [ids[::-1]] + [list(rng.permutation(ids)) for _ in range(8)]
    for order in orders:
        assert _bits(_run(order, vecs, monkeypatch)) == base


def test_similar_to_ties_break_by_engram_id(monkeypatch) -> None:
    vecs = _vectors()
    result = _run(list(vecs)[::-1], vecs, monkeypatch)
    picked = [d for _n, d, _w in result["egr_00000003"]]
    # the two exact duplicates tie on weight; the lower id must come first
    assert picked.index("egr_d0000001") < picked.index("egr_d0000002")
