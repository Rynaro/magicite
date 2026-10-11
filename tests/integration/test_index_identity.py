"""Identity-only reads preserve metadata and routing admission boundaries."""

from dataclasses import asdict
from unittest import mock

import numpy as np
import pytest
from tests.conftest import TOY_ENGRAM_NAMES
from tests.integration.test_index_generation import _fp, _sample_projection

from magicite.core import index_generation as idx
from magicite.core import registry, router
from magicite.storage import db, lease


def _build(conn, tmp_path, *, count=1, publish=True):
    catalog = idx.IndexCatalog(conn)
    projection = _sample_projection()
    with lease.CrossProcessLease(lock_path=tmp_path / "writer.lock", conn=conn).acquire():
        with lease.writer_lease("identity-fixture"):
            gid = catalog.begin(snapshot_id="identity-snapshot", fingerprint=_fp())
            for n in range(count):
                from dataclasses import replace

                catalog.add_entry(
                    gid,
                    replace(projection, engram_id=f"egr_{n:08x}"),
                    dense_vec=np.ones(64, dtype=np.float32),
                )
            catalog.complete(gid)
            if publish:
                catalog.publish(gid)
    return catalog, gid


def test_identity_matches_full_pin_without_entry_query(tmp_path):
    conn = db.connect(tmp_path / "index.db")
    catalog, gid = _build(conn, tmp_path)
    full = catalog.pin()
    statements = []
    conn.set_trace_callback(statements.append)
    with mock.patch.object(catalog, "_load_entries", side_effect=AssertionError("entry load")):
        identity = catalog.pin_identity()
    assert asdict(identity) == {k: v for k, v in asdict(full).items() if k != "entries"}
    assert not any("index_entry" in sql.lower() for sql in statements)
    assert len(full.entries) == 1
    conn.close()


@pytest.mark.parametrize(
    "case", ["absent", "unknown", "building", "failed", "complete", "bad_json", "missing_field", "empty"]
)
def test_metadata_errors_match_full_pin(tmp_path, case):
    conn = db.connect(tmp_path / "index.db")
    catalog, gid = _build(conn, tmp_path, count=0 if case == "empty" else 1, publish=case != "complete")
    argument = gid
    if case == "unknown":
        argument = "unknown"
    elif case == "absent":
        conn.execute("DELETE FROM index_active_pointer")
        argument = None
    elif case in ("building", "failed"):
        conn.execute("UPDATE index_generation SET state=? WHERE generation_id=?", (case, gid))
    elif case in ("bad_json", "missing_field"):
        conn.execute(
            "UPDATE index_generation SET fingerprint_json=? WHERE generation_id=?",
            ("{" if case == "bad_json" else "{}", gid),
        )
    conn.commit()

    def observed(method):
        try:
            value = method(argument)
            return ("value", value.generation_id, value.snapshot_id, value.fingerprint, value.status)
        except Exception as exc:
            return ("error", type(exc), str(exc))

    assert observed(catalog.pin) == observed(catalog.pin_identity)
    if case == "complete":
        assert catalog.pin_identity(gid).status == "complete"
        with pytest.raises(idx.IncompleteGenerationError):
            catalog.pin_identity()
    conn.close()


def test_identity_reloads_live_publish_and_rollback(tmp_path):
    conn = db.connect(tmp_path / "index.db")
    catalog, first = _build(conn, tmp_path)
    assert catalog.pin_identity().generation_id == first
    _, second = _build(conn, tmp_path)
    assert catalog.pin_identity().generation_id == second
    with lease.CrossProcessLease(lock_path=tmp_path / "writer.lock", conn=conn).acquire():
        with lease.writer_lease("rollback"):
            catalog.rollback()
    assert catalog.pin_identity().generation_id == first
    conn.close()


@pytest.mark.parametrize("column,value", [("chunks_json", "{"), ("symbols_json", "{"), ("dense_vec", b"x")])
def test_unused_payload_does_not_block_identity_but_full_pin_rejects(tmp_path, column, value):
    conn = db.connect(tmp_path / "index.db")
    catalog, gid = _build(conn, tmp_path)
    conn.execute(f"UPDATE index_entry SET {column}=? WHERE generation_id=?", (value, gid))
    conn.commit()
    assert catalog.pin_identity(gid).generation_id == gid
    with pytest.raises(ValueError):
        catalog.pin(gid)
    # Retrieval consumers still request the full entry set, independently of identity.
    from magicite.core.candidates import RetrievalIndex

    with pytest.raises(ValueError):
        RetrievalIndex.from_catalog(catalog, gid)
    conn.close()


def _signature(outcome):
    result = asdict(outcome)

    # Fresh request/session event identifiers and elapsed time are not route semantics.
    def normalize(value):
        if isinstance(value, dict):
            return {
                k: normalize(v)
                for k, v in value.items()
                if k not in {"decision_id", "timing_ms", "elapsed_ms"}
            }
        if isinstance(value, (tuple, list)):
            return [normalize(v) for v in value]
        return value

    return normalize(result)


@pytest.mark.parametrize("corrupt", [None, "chunks_json", "symbols_json", "dense_vec"])
def test_actual_dense_route_and_live_refusal(
    cfg, db_conn, embedder, review_fixture_artifacts, tmp_path, corrupt
):
    registry.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    catalog, gid = _build(db_conn, tmp_path)
    baseline_reader = idx.IndexCatalog.pin
    candidate_reader = idx.IndexCatalog.pin_identity

    def run(reader):
        with mock.patch.object(
            idx.IndexCatalog, "pin_identity", lambda self, generation_id=None: reader(self, generation_id)
        ):
            return router.route(
                cfg, db_conn, embedder, query="steam wont open", k=3, session_id="identity-comparison"
            )

    # Full pin delegates metadata to candidate_reader: avoid recursive baseline patch.
    def baseline(self, generation_id=None):
        with mock.patch.object(idx.IndexCatalog, "pin_identity", candidate_reader):
            return baseline_reader(self, generation_id)

    before = run(baseline)
    assert _signature(before) == _signature(run(candidate_reader))
    assert before.candidates
    if corrupt:
        value = b"x" if corrupt == "dense_vec" else "{"
        db_conn.execute(f"UPDATE index_entry SET {corrupt}=? WHERE generation_id=?", (value, gid))
        db_conn.commit()
        with pytest.raises(ValueError):
            run(baseline)
        # Explicit changed contract: unused payload corruption no longer fails dense routing.
        assert _signature(before) == _signature(run(candidate_reader))
    # A live source change still invokes original freshness/admission refusal.
    candidate = before.candidates[0]
    row = db_conn.execute("SELECT path FROM engram WHERE id=?", (candidate.id,)).fetchone()
    path = cfg.project_root / row["path"]
    path.write_text(path.read_text() + "\nUnreviewed source change.\n")
    refused = run(candidate_reader)
    assert candidate.id not in [item.id for item in refused.candidates]
    if not corrupt:
        assert _signature(refused) == _signature(run(baseline))


def test_consumed_embedding_corruption_still_fails(
    cfg, db_conn, embedder, review_fixture_artifacts, tmp_path
):
    registry.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(*TOY_ENGRAM_NAMES)
    _build(db_conn, tmp_path)
    before = router.route(cfg, db_conn, embedder, query="steam wont open", k=3)
    assert before.candidates
    db_conn.execute(
        "UPDATE eph_embedding SET vec=? WHERE engram_id=? AND model=?",
        (b"x", before.candidates[0].id, embedder.model_name),
    )
    db_conn.commit()
    with pytest.raises(ValueError):
        router.route(cfg, db_conn, embedder, query="steam wont open", k=3)
