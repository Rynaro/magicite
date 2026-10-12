"""R3 witnesses for AC-TH-01 (splice, root/policy projections) and AC-TH-03 (policy binding)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import trust, trust_journal, writer_guard
from magicite.core.trust import TrustDecision, default_policy
from magicite.core.trust_custodian import HEAD_FIELDS, CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal, _Replay
from magicite.storage import db

SPLICE_MESSAGE = "local trust history does not match custody"


class Adapter:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def call(self, operation, **arguments):
        return getattr(self.store, operation)(self.registry, **arguments)


def make_journal(root: Path, registry: str):
    store = CustodianStore.create(root / f"custody-{registry}")
    store.enroll(registry, default_policy().to_dict(), actor="operator", reviewed=True)
    ledger = TrustJournal(root / f"journal-{registry}", registry, Adapter(store, registry))
    ledger.initialize_reviewed_genesis()
    return ledger, store


def commit(ledger, store, identity, action, subject="subject"):
    policy = default_policy()
    value = TrustDecision(
        decision_id=identity,
        engram_id=subject,
        content_digest="a" * 64,
        decision=action,
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp="2026-01-01T00:00:00Z",
    ).to_dict()
    fence = store.register_fence(
        ledger.registry_id,
        predecessor=store.read_current(ledger.registry_id),
        attempt_id=identity,
        holder="lease",
        local_token=1,
    )
    ledger.append(
        record_id=identity, kind="trust_decision", payload=value, fence=fence, assert_owned=lambda: None
    )


@pytest.fixture
def two_registries(tmp_path):
    local, local_store = make_journal(tmp_path, "registry-one")
    foreign, foreign_store = make_journal(tmp_path, "registry-two")
    for ledger, store in ((local, local_store), (foreign, foreign_store)):
        commit(ledger, store, f"admit-{ledger.registry_id}", "admit")
        commit(ledger, store, f"revoke-{ledger.registry_id}", "revoke")
    yield local, foreign
    local_store.close()
    foreign_store.close()


def rows_of(ledger):
    return ledger.journal_path.read_bytes().splitlines()


def write_rows(ledger, rows):
    ledger.journal_path.write_bytes(b"\n".join(rows) + b"\n")


def splice(ledger, foreign, mutation):
    rows, other = rows_of(ledger), rows_of(foreign)
    assert len(rows) == 3 and len(other) == 3
    if mutation == "foreign_insert":
        rows.insert(2, other[1])
    elif mutation == "foreign_replace_same_count":
        rows[1] = other[1]
    elif mutation == "foreign_head_row_replace":
        rows[2] = other[2]
    elif mutation == "reorder_non_adjacent":
        rows[0], rows[2] = rows[2], rows[0]
    elif mutation == "reorder_adjacent_tail":
        rows[1], rows[2] = rows[2], rows[1]
    elif mutation == "move_row_from_other_position":
        rows[2] = rows[1]  # same count; a valid row relocated to a different position
    elif mutation == "move_foreign_row_to_other_position":
        rows[1], rows[2] = other[2], other[1]
    else:  # pragma: no cover
        raise AssertionError(mutation)
    write_rows(ledger, rows)


SPLICES = [
    "foreign_insert",
    "foreign_replace_same_count",
    "foreign_head_row_replace",
    "reorder_non_adjacent",
    "reorder_adjacent_tail",
    "move_row_from_other_position",
    "move_foreign_row_to_other_position",
]


def test_splice_positive_control_unmutated_journal_reads(two_registries):
    """AC-TH-01 VERIFY 'decision ... mutation matrix' positive control: the unspliced journal reads."""
    ledger, _ = two_registries
    assert ledger.snapshot().latest_by_engram["subject"]["decision"] == "revoke"
    assert len(rows_of(ledger)) == 3


@pytest.mark.parametrize("mutation", SPLICES)
def test_spliced_journal_closes_cold(two_registries, mutation):
    """AC-TH-01 WHEN 'spliced' (VERIFY: decision mutation matrix): cold read closes with custody mismatch."""
    ledger, foreign = two_registries
    trust_journal._VERIFIED_SNAPSHOTS.clear()
    splice(ledger, foreign, mutation)
    with pytest.raises(CustodianError, match=SPLICE_MESSAGE):
        ledger.snapshot()


@pytest.mark.parametrize("mutation", SPLICES)
def test_spliced_journal_closes_with_warm_verified_cache(two_registries, mutation):
    """AC-TH-01 WHEN 'spliced' with a snapshot cached before the splice: the verified cache cannot hide it."""
    ledger, foreign = two_registries
    first = ledger.snapshot()
    assert ledger.snapshot() is first  # cache is warm (positive control for reuse)
    key = (str(ledger.directory.absolute()), "registry-one", None)
    assert key in trust_journal._VERIFIED_SNAPSHOTS
    splice(ledger, foreign, mutation)
    with pytest.raises(CustodianError, match=SPLICE_MESSAGE):
        ledger.snapshot()
    assert key not in trust_journal._VERIFIED_SNAPSHOTS


def test_same_size_splice_with_restored_mtime_closes_warm(two_registries):
    """AC-TH-01 WHEN 'spliced': a same-size in-place splice with restored mtime cannot ride the warm cache."""
    ledger, _ = two_registries
    ledger.snapshot()
    rows = rows_of(ledger)
    before = ledger.journal_path.stat()
    rows[1], rows[2] = rows[2], rows[1]
    spliced = b"\n".join(rows) + b"\n"
    assert len(spliced) == before.st_size
    ledger.journal_path.write_bytes(spliced)
    os.utime(ledger.journal_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(CustodianError, match=SPLICE_MESSAGE):
        ledger.snapshot()


# --- AC-TH-03 policy binding -------------------------------------------------


def forged_policy_digest(store):
    head = store.read_current("registry-one")
    assert head["policy_digest"] != "f" * 64
    return "f" * 64


def test_policy_binding_positive_control_and_direct_mismatch(two_registries):
    """AC-TH-03 WHEN 'policy binding' mismatch: replay refuses a head committing a different policy."""
    ledger, _ = two_registries
    snapshot = ledger.snapshot()
    replay = _Replay.start("registry-one", snapshot.records[0]).extended(list(snapshot.records[1:]))
    assert replay.snapshot(snapshot.head).policy == snapshot.policy  # positive control
    assert "policy_digest" not in HEAD_FIELDS  # so the page/head match cannot be what rejects it
    forged = {**snapshot.head, "policy_digest": "f" * 64}
    with pytest.raises(CustodianError, match="snapshot policy commitment mismatch"):
        replay.snapshot(forged)


@pytest.mark.parametrize("warm", [False, True])
def test_custody_head_with_wrong_policy_digest_closes_reads(tmp_path, warm):
    """AC-TH-03 WHEN 'policy binding' mismatch: fresh custody head with a different policy_digest closes."""
    ledger, store = make_journal(tmp_path, "registry-one")
    try:
        commit(ledger, store, "admit", "admit")
        trust_journal._VERIFIED_SNAPSHOTS.clear()
        if warm:
            assert ledger.snapshot().latest_by_engram["subject"]["decision"] == "admit"
        forged = forged_policy_digest(store)
        genuine = ledger.client

        class ForgedHead:
            def call(self, operation, **arguments):
                result = genuine.call(operation, **arguments)
                if operation == "read_current":
                    result = {**result, "policy_digest": forged}
                return result

        ledger.client = ForgedHead()
        # Local head file copies the forged commitment, so only policy binding can reject it.
        head = json.loads(ledger.head_path.read_bytes())
        head["policy_digest"] = forged
        ledger.head_path.write_bytes(json.dumps(head).encode())
        with pytest.raises(CustodianError, match="snapshot policy commitment mismatch"):
            ledger.snapshot()
        # Control: the genuine head still reads once local head bytes are restored.
        ledger.client = genuine
        head["policy_digest"] = default_policy().digest()
        ledger.head_path.write_bytes(json.dumps(head).encode())
        assert ledger.snapshot().latest_by_engram["subject"]["decision"] == "admit"
    finally:
        store.close()


# --- AC-TH-01 root / policy projection mutation ------------------------------


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_dirs()
    connection = db.connect(cfg.db_path)
    store = CustodianStore.create(tmp_path / "independent-test-store")
    store.enroll("r", default_policy().to_dict(), actor="operator", reviewed=True)
    client = Adapter(store, "r")
    monkeypatch.setattr(writer_guard, "resolve_custody", lambda cfg: ("r", client))
    TrustJournal(cfg.data_dir / "trust" / "authority", "r", client).initialize_reviewed_genesis()
    yield cfg, connection
    connection.close()
    store.close()


PROJECTIONS = ["delete_both", "edit_policy_unrevoke", "rollback_both_to_prerevocation", "garbage_both"]


@pytest.mark.parametrize("mutation", PROJECTIONS)
def test_root_policy_projection_mutation_cannot_restore_admission(enrolled, mutation):
    """AC-TH-01 WHEN 'policy/root projections ... deleted, edited, ... rolled back'
    (VERIFY: policy/root mutation matrix; root-revocation rollback; missing-policy fallback):
    route and body still deny and authenticated policy wins."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from magicite.core import registry, router
    from magicite.core.bundles import public_key_fingerprint
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.mcp import bind_retrieval
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput

    cfg, connection = enrolled
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    fingerprint = public_key_fingerprint(public)
    pinned = trust.pin_trust_root(cfg, public_key_bytes=public)
    prerevocation = pinned.to_dict()
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/toy-registry/engrams"
    for source in fixtures.glob("*.egr.md"):
        shutil.copy(source, cfg.registry_dir / source.name)
    embedder = get_embedder(dim=256)
    registry.register(cfg, connection, embedder, path=".magicite/engrams")
    for row in connection.execute("SELECT id,content_sha256 FROM engram").fetchall():
        registry.review_approve(
            cfg, connection, engram_id=row["id"], expected_digest=row["content_sha256"], actor="operator"
        )
    name = "proton-ge-proton-downgrade"
    row = connection.execute("SELECT content_sha256 FROM engram WHERE name=?", (name,)).fetchone()
    ctx = ToolContext(cfg=cfg, conn=connection, embedder=embedder)

    def params():
        return LoadSkillBodyInput(
            name=name,
            level="L2",
            expected_content_digest=row["content_sha256"],
            expected_policy_digest=bind_retrieval._active_policy_digest(cfg),
        )

    assert bind_retrieval.load_skill_body(ctx, params()).status == "ok"  # positive control
    revoked = trust.revoke_trust_root(cfg, fingerprint=fingerprint)
    assert revoked.roots[0].revoked
    assert bind_retrieval.load_skill_body(ctx, params()).status != "ok"

    policy_file, roots_file = trust.trust_policy_path(cfg), trust.trust_roots_path(cfg)
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    unrevoked_roots = [{**item, "revoked": False} for item in prerevocation["roots"]]
    assert unrevoked_roots and not unrevoked_roots[0]["revoked"]
    if mutation == "delete_both":
        for path in (policy_file, roots_file):
            path.unlink(missing_ok=True)
    elif mutation == "edit_policy_unrevoke":
        edited = revoked.to_dict()
        edited["roots"] = unrevoked_roots
        policy_file.write_text(json.dumps(edited))
        roots_file.write_text(json.dumps(unrevoked_roots))
    elif mutation == "rollback_both_to_prerevocation":
        policy_file.write_text(json.dumps(prerevocation))
        roots_file.write_text(json.dumps(prerevocation["roots"]))
    else:
        policy_file.write_text("{not json")
        roots_file.write_bytes(b"\x00\xff")

    assert trust.load_policy(cfg) == revoked
    assert trust.load_policy(cfg).roots[0].revoked
    assert bind_retrieval.load_skill_body(ctx, params()).status != "ok"
    body = bind_retrieval.load_skill_body(ctx, params())
    assert not body.procedure
    routed = router.route(cfg, connection, embedder, query="rollback proton for a steam game", k=5)
    assert not routed.candidates
    assert not trust.admission_still_valid(
        cfg, engram_id=connection.execute("SELECT id FROM engram WHERE name=?", (name,)).fetchone()["id"],
        content_digest=row["content_sha256"],
    )
