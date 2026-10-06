"""AC-TH-07 call-site completeness: every trust read uses one validated snapshot.

Frozen VERIFY clause witnessed here: "all trust call sites" (AC-TH-07), under
THEN "every path SHALL use one validated snapshot whose latest-by-engram
journal sequence and authenticated current policy are bound to the same
verified head, without a subsequent mutable-policy reread".

The enumeration is AST-based over ``src/magicite`` and must match the explicit
allowlist below exactly (site *and* per-function call count), so a new caller
of an authority read API, or a second read added to an existing caller, fails
until it is reviewed and justified here.
"""

from __future__ import annotations

import ast
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import magicite
from magicite.core import registry, trust
from magicite.errors import InvalidInputError
from magicite.storage import lease as lease_mod

SRC_ROOT = Path(magicite.__file__).resolve().parent

# Authority read APIs (functions defined in core/trust.py and core/writer_guard.py
# that resolve a TrustJournal or an authenticated TrustSnapshot).
TRACKED_FUNCTIONS = frozenset(
    {
        "authenticated_snapshot",
        "load_policy",
        "list_decisions",
        "get_decision",
        "latest_decision_for",
        "admission_still_valid",
        "project_trust_view",
        "reload_from_mirror",
        "journal_for",
        "bound_journal",
    }
)
# TrustJournal primitives; only matched as attribute calls (``x.snapshot()``).
TRACKED_METHODS = frozenset({"snapshot", "_remote"})
# Same-named functions on a non-authority ledger (approvals JSON mirror).
NON_AUTHORITY_RECEIVERS = frozenset({"approvals_mod"})
# The journal implementation itself defines the primitives.
EXCLUDED_FILES = frozenset({"core/trust_journal.py"})

LEASE = (
    "lease-serialized: runs under the held registry writer lease, and every journal append"
    " requires that lease (writer_guard.bound_journal), so all reads observe one head"
)

# (file, enclosing function, callee) -> (count, one-line justification)
ALLOWLIST: dict[tuple[str, str, str], tuple[int, str]] = {
    # --- primitive definitions / thin wrappers (exactly one snapshot each) ---
    ("core/trust.py", "authenticated_snapshot", "journal_for"): (
        1,
        "the single validated-snapshot primitive",
    ),
    ("core/trust.py", "authenticated_snapshot", "snapshot"): (1, "the single validated-snapshot primitive"),
    ("core/trust.py", "load_policy", "authenticated_snapshot"): (1, "one snapshot; policy only"),
    ("core/trust.py", "list_decisions", "authenticated_snapshot"): (1, "one snapshot; decisions only"),
    ("core/trust.py", "get_decision", "list_decisions"): (1, "delegates to one list_decisions snapshot"),
    ("core/trust.py", "latest_decision_for", "authenticated_snapshot"): (
        1,
        "one snapshot; latest_by_engram only",
    ),
    ("core/trust.py", "reload_from_mirror", "list_decisions"): (1, "one snapshot projected into SQLite"),
    ("core/trust.py", "admission_still_valid", "authenticated_snapshot"): (
        1,
        "uses threaded _snapshot when given, else exactly one",
    ),
    ("core/trust.py", "project_trust_view", "authenticated_snapshot"): (
        1,
        "one snapshot for decision, channel and admission",
    ),
    ("core/trust.py", "project_trust_view", "admission_still_valid"): (
        1,
        "threads the same _snapshot (no second read)",
    ),
    # --- trust writers ---
    ("core/trust.py", "_trust_write_leases", "bound_journal"): (
        1,
        "fence/ownership check only; reads no history",
    ),
    ("core/trust.py", "save_policy", "bound_journal"): (
        1,
        "policy CAS read and append on one bound journal under the lease",
    ),
    ("core/trust.py", "save_policy", "snapshot"): (1, "the CAS base snapshot under the lease"),
    ("core/trust.py", "pin_trust_root", "load_policy"): (1, LEASE + "; save_policy re-checks revision CAS"),
    ("core/trust.py", "revoke_trust_root", "load_policy"): (
        1,
        LEASE + "; save_policy re-checks revision CAS",
    ),
    ("core/trust.py", "persist_decision", "bound_journal"): (1, "append-only; reads no policy or decisions"),
    ("core/trust.py", "record_pending_intake", "authenticated_snapshot"): (
        1,
        "policy read under the same lease persist_decision appends under (F3 fix)",
    ),
    ("core/trust.py", "approve", "list_decisions"): (1, LEASE + "; event_id idempotency pre-check"),
    ("core/trust.py", "approve", "authenticated_snapshot"): (
        1,
        "one snapshot binds prior decision, policy and validity check",
    ),
    ("core/trust.py", "approve", "admission_still_valid"): (
        1,
        "deliberate post-write revalidation of the committed head",
    ),
    ("core/trust.py", "reject", "authenticated_snapshot"): (
        1,
        "one snapshot binds prior decision and policy under the lease",
    ),
    ("core/trust.py", "revoke", "authenticated_snapshot"): (
        1,
        "one snapshot binds prior decision and policy under the lease",
    ),
    # --- registry: route-independent domain paths ---
    ("core/registry.py", "_ingest_one", "admission_still_valid"): (
        2,
        LEASE + " (register/sync/import_bundle callers)",
    ),
    ("core/registry.py", "import_bundle", "load_policy"): (
        2,
        "pre-lease read only selects verify roots; the in-lease reread is the binding fail-closed recheck",
    ),
    ("core/registry.py", "import_bundle", "bound_journal"): (1, LEASE + "; registry id for marking"),
    ("core/registry.py", "review_approve", "list_decisions"): (1, LEASE + "; event_id idempotency pre-check"),
    ("core/registry.py", "review_approve", "admission_still_valid"): (
        1,
        "deliberate post-write revalidation under the lease",
    ),
    ("core/registry.py", "sync", "reload_from_mirror"): (1, LEASE),
    ("core/registry.py", "trust_list", "list_decisions"): (1, "one snapshot (read-only listing)"),
    ("core/registry.py", "trust_view_for", "project_trust_view"): (
        1,
        "sole read; channel resolved inside the same snapshot (F3 fix)",
    ),
    # --- route / body disclosure ---
    ("core/router.py", "_trust_decisions_by_engram", "authenticated_snapshot"): (
        1,
        "one snapshot per route binds decisions+policy, threaded to every subject",
    ),
    ("mcp/bind_retrieval.py", "load_skill_body", "authenticated_snapshot"): (
        2,
        "one disclosure snapshot; second read compares heads only (mandated freshness revalidation)",
    ),
    # --- rebuild / restore / backup ---
    ("core/backup.py", "_backup_lease", "bound_journal"): (1, "fence/ownership check only; reads no history"),
    ("core/backup.py", "_domain_sequences", "authenticated_snapshot"): (
        1,
        "head sequence only; callers hold the backup lease",
    ),
    ("core/backup.py", "_collect_live_known_sets", "list_decisions"): (
        1,
        "revoke id set; caller create_snapshot holds the backup lease",
    ),
    ("core/backup.py", "create_snapshot", "authenticated_snapshot"): (1, LEASE + " (_backup_lease)"),
    ("core/backup.py", "build_recovery_overlay", "list_decisions"): (
        1,
        LEASE + " (only product caller restore_snapshot via _preserve_live_overlay)",
    ),
    ("core/backup.py", "build_recovery_overlay", "load_policy"): (
        1,
        LEASE + " (only product caller restore_snapshot via _preserve_live_overlay)",
    ),
    ("core/backup.py", "_apply_overlay", "authenticated_snapshot"): (
        1,
        "one snapshot validates overlay revokes against authenticated history",
    ),
    ("core/backup.py", "_rebuild_projections", "reload_from_mirror"): (
        1,
        "one snapshot projected into SQLite",
    ),
    ("core/backup.py", "_verify_restore_custody", "bound_journal"): (1, LEASE),
    ("core/backup.py", "_verify_restore_custody", "_remote"): (
        1,
        "one authenticated remote head/history read",
    ),
    ("core/backup.py", "restore_snapshot", "bound_journal"): (1, LEASE + "; reconcile under fence"),
    # --- adaptive writers (dream/decay) ---
    ("core/decay.py", "archive_below_floor", "admission_still_valid"): (
        1,
        "one snapshot per subject decision; runs under Dream's writer lease",
    ),
    ("core/decay.py", "archive_one", "bound_journal"): (1, "fence/ownership check only; reads no history"),
    ("core/dream.py", "_build_checkpoint_candidate", "admission_still_valid"): (
        1,
        "one snapshot per subject decision; runs under Dream's writer lease",
    ),
    # Read-only diagnostic: fresh protected head and verified local history;
    # no writer lease, fence registration or reconciliation is requested.
    ("obs/doctor.py", "custody_check", "snapshot"): (
        1,
        "standalone zero-write diagnosis; one authenticated local journal snapshot",
    ),
    # --- other writers (fence only) ---
    ("core/evidence.py", "_evidence_write_guard", "bound_journal"): (
        1,
        "fence/ownership check only; reads no history",
    ),
    ("core/policy_store.py", "_policy_write_leases", "bound_journal"): (
        1,
        "fence/ownership check only; reads no history",
    ),
    ("core/trust_artifacts.py", "publish_new_artifact", "bound_journal"): (1, LEASE),
    ("core/trust_artifacts.py", "publish_new_artifact", "snapshot"): (1, "one snapshot of the bound journal"),
    ("core/trust_artifacts.py", "bind_prepared_transform", "bound_journal"): (
        1,
        "append-only lineage; reads no policy or decisions",
    ),
    ("core/trust_artifacts.py", "publish_authored_edit", "bound_journal"): (
        1,
        "append-only lineage; reads no policy or decisions",
    ),
    ("core/trust_artifacts.py", "require_bound_artifact", "journal_for"): (
        1,
        "one snapshot for lineage check",
    ),
    ("core/trust_artifacts.py", "require_bound_artifact", "snapshot"): (1, "one snapshot for lineage check"),
    # --- policy/root, legacy import, rotation, migration, custody admin ---
    ("core/trust_legacy.py", "backup_reviewed", "_remote"): (
        1,
        "one read; checks head_sequence==1 and the immutable genesis policy record",
    ),
    ("core/trust_legacy.py", "apply_reviewed", "_remote"): (
        1,
        "one read; checks only the immutable genesis policy record (history[0]);"
        " appends are fenced under the lease",
    ),
    ("core/trust_legacy.py", "apply_reviewed", "bound_journal"): (1, LEASE),
    ("core/trust_legacy.py", "apply_reviewed", "snapshot"): (
        1,
        "final head report after commit under the lease",
    ),
    ("core/trust_rotation_client.py", "rotate_registry", "_remote"): (
        1,
        "one maintenance-history read under the rotation lease",
    ),
    ("core/migration.py", "_reviewed_history", "_remote"): (
        1,
        "one read of reconciliation records (no policy/decision use)",
    ),
    ("core/custody_admin.py", "initialize_journal", "bound_journal"): (1, LEASE),
    ("core/custody_admin.py", "reconcile", "bound_journal"): (1, LEASE),
}


def _enumerate_source(rel: str, source: str) -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*stack, child.name])
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name: str | None = None
                if isinstance(func, ast.Name) and func.id in TRACKED_FUNCTIONS:
                    name = func.id
                elif isinstance(func, ast.Attribute) and (
                    func.attr in TRACKED_FUNCTIONS or func.attr in TRACKED_METHODS
                ):
                    receiver = func.value
                    if not (isinstance(receiver, ast.Name) and receiver.id in NON_AUTHORITY_RECEIVERS):
                        name = func.attr
                if name is not None:
                    found[(rel, ".".join(stack) or "<module>", name)] += 1
            visit(child, stack)

    visit(ast.parse(source), [])
    return found


def _enumerate_src() -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in EXCLUDED_FILES:
            continue
        found.update(_enumerate_source(rel, path.read_text(encoding="utf-8")))
    return found


def test_all_trust_call_sites_are_enumerated_and_justified():
    """AC-TH-07 VERIFY "all trust call sites": every authority read in src/magicite
    is on the reviewed single-snapshot allowlist, with the exact per-function count."""
    found = _enumerate_src()
    expected = {key: count for key, (count, _) in ALLOWLIST.items()}
    unlisted = {key: n for key, n in found.items() if key not in expected}
    stale = sorted(key for key in expected if key not in found)
    miscounted = {
        key: (found[key], expected[key]) for key in expected if key in found and found[key] != expected[key]
    }
    assert not unlisted, f"unreviewed trust authority call sites: {unlisted}"
    assert not stale, f"allowlist entries without a call site (remove them): {stale}"
    assert not miscounted, f"(found, allowed) read counts differ: {miscounted}"
    for key, (_, why) in ALLOWLIST.items():
        assert why.strip() and "\n" not in why, key


def test_enumerator_detects_new_and_repeated_sites():
    """AC-TH-07 VERIFY "all trust call sites" positive control: the enumerator is
    not vacuous; it flags a new caller, a direct journal snapshot, and a repeated
    read such as the pre-fix trust_view_for (latest_decision_for + project_trust_view)."""
    source = (
        "def new_route(cfg):\n"
        "    trust_mod.load_policy(cfg)\n"
        "    writer_guard.journal_for(cfg).snapshot()\n"
        "    approvals_mod.reload_from_mirror(cfg, conn)\n"
        "def trust_view_for(cfg):\n"
        "    trust_mod.latest_decision_for(cfg, 'x')\n"
        "    return trust_mod.project_trust_view(cfg)\n"
    )
    found = _enumerate_source("core/registry.py", source)
    assert found[("core/registry.py", "new_route", "load_policy")] == 1
    assert found[("core/registry.py", "new_route", "journal_for")] == 1
    assert found[("core/registry.py", "new_route", "snapshot")] == 1
    assert ("core/registry.py", "new_route", "reload_from_mirror") not in found
    old_view = ("core/registry.py", "trust_view_for", "latest_decision_for")
    assert found[old_view] == 1 and old_view not in ALLOWLIST


# --------------------------------------------------------------------------
# F3 behavioral witnesses
# --------------------------------------------------------------------------


def _decision(
    engram_id: str, kind: str, channel: str, policy: trust.TrustPolicy, *, sig: bool | None
) -> dict:
    return trust.TrustDecision(
        decision_id=f"{kind}-{engram_id}",
        engram_id=engram_id,
        content_digest="a" * 64,
        decision=kind,  # type: ignore[arg-type]
        source_channel=channel,  # type: ignore[arg-type]
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="t",
        timestamp="2026-01-01T00:00:00Z",
        signature_valid=sig,
    ).to_dict()


def _view_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE engram (id TEXT, content_sha256 TEXT, status TEXT,"
        " verification_status TEXT, origin TEXT, path TEXT)"
    )
    conn.execute(
        "INSERT INTO engram VALUES ('subject', ?, 'active', 'unverified', 'imported', 'x.egr.md')",
        ("a" * 64,),
    )
    return conn


def _script_snapshots(monkeypatch, script: list) -> list[int]:
    calls: list[int] = []

    def fake(cfg):
        calls.append(1)
        item = script[min(len(calls), len(script)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(trust, "authenticated_snapshot", fake)
    return calls


def test_trust_view_for_projects_from_one_snapshot_when_history_moves(monkeypatch, tmp_path):
    """AC-TH-07 VERIFY "all trust call sites" + "post-route policy/root/history drift":
    trust_view_for (MCP trust view) takes exactly one authenticated snapshot, so a
    head change between reads cannot mix channel from one head with admission from another."""
    monkeypatch.setattr(
        trust, "live_resource_digest", lambda *a, **k: (_ for _ in ()).throw(InvalidInputError("x"))
    )
    policy = trust.default_policy()
    head_a = SimpleNamespace(
        latest_by_engram={"subject": _decision("subject", "pending", "local_register", policy, sig=True)},
        policy=policy.to_dict(),
        source_signers={},
    )
    cfg = SimpleNamespace(project_root=tmp_path)
    conn = _view_conn()

    # Reference: the view derived from head A alone.
    _script_snapshots(monkeypatch, [head_a])
    reference = registry.trust_view_for(cfg, conn, engram_id="subject")
    assert reference.signature_valid is True and reference.admitted is False

    # Second read would see an unavailable head; old code mixed A's channel with it.
    calls = _script_snapshots(monkeypatch, [head_a, trust.TrustLedgerCorruptError("moved")])
    view = registry.trust_view_for(cfg, conn, engram_id="subject")
    assert len(calls) == 1
    assert view == reference


def test_trust_view_for_channel_semantics_preserved_on_real_custody(cfg, db_conn, monkeypatch):
    """AC-TH-07 VERIFY "all trust call sites" positive control: with real enrolled
    custody, an imported subject's recorded source channel still drives origin trust,
    an undecided imported subject falls back to external_file (untrusted), and an
    unavailable ledger stays fail-closed."""
    monkeypatch.setattr(
        trust, "live_resource_digest", lambda *a, **k: (_ for _ in ()).throw(InvalidInputError("x"))
    )
    policy = trust.load_policy(cfg)
    trust.persist_decision(
        cfg,
        db_conn,
        trust.TrustDecision.from_dict(_decision("subject", "admit", "bundle_import", policy, sig=True)),
    )
    conn = _view_conn()
    conn.execute(
        "INSERT INTO engram VALUES ('other', ?, 'active', 'unverified', 'imported', 'y.egr.md')", ("a" * 64,)
    )

    admitted = registry.trust_view_for(cfg, conn, engram_id="subject")
    assert admitted.admitted and admitted.origin_trusted and admitted.signature_valid is True
    undecided = registry.trust_view_for(cfg, conn, engram_id="other")
    assert not undecided.admitted and not undecided.origin_trusted

    _script_snapshots(monkeypatch, [trust.TrustLedgerCorruptError("down")])
    closed = registry.trust_view_for(cfg, conn, engram_id="subject")
    assert not closed.admitted and not closed.origin_trusted and closed.signature_valid is None


def test_record_pending_intake_binds_policy_of_the_head_it_appends_to(cfg, db_conn, monkeypatch):
    """AC-TH-07 VERIFY "all trust call sites" + "post-route policy/root/history drift":
    record_pending_intake reads policy under the same writer lease as its append, so a
    policy commit that wins the lease first is the policy the pending record binds."""
    rev2 = trust.TrustPolicy(policy_id="reviewed", revision=2, roots=())
    real = trust._trust_write_leases
    state = {"fired": False}

    @contextmanager
    def racing_leases(c, conn, *, holder):
        if not state["fired"]:
            state["fired"] = True  # a concurrent writer commits policy first
            trust.save_policy(cfg, rev2)
        with real(c, conn, holder=holder):
            yield

    monkeypatch.setattr(trust, "_trust_write_leases", racing_leases)
    decision = trust.record_pending_intake(
        cfg,
        db_conn,
        engram_id="subject",
        content_digest="a" * 64,
        source_channel="bundle_import",
        actor="t",
    )
    head = trust.authenticated_snapshot(cfg)
    assert state["fired"]
    assert decision.policy_revision == 2 and decision.policy_digest == rev2.digest()
    assert head.policy == rev2.to_dict()
    assert head.latest_by_engram["subject"]["decision_id"] == decision.decision_id


def test_record_pending_intake_reads_policy_under_lease_positive_control(cfg, db_conn, monkeypatch):
    """AC-TH-07 VERIFY "all trust call sites" positive control: without drift the
    pending record binds the current authenticated policy, and the policy read
    happens while the cross-process writer lease is held."""
    real = trust.authenticated_snapshot
    held: list[bool] = []

    def spy(c):
        held.append(lease_mod.cross_process_lease_held())
        return real(c)

    monkeypatch.setattr(trust, "authenticated_snapshot", spy)
    decision = trust.record_pending_intake(
        cfg,
        db_conn,
        engram_id="subject",
        content_digest="a" * 64,
        source_channel="external_file",
        actor="t",
    )
    assert held and all(held)
    current = trust.TrustPolicy.from_dict(real(cfg).policy)
    assert decision.policy_digest == current.digest() and decision.policy_revision == current.revision


def test_record_pending_intake_fails_closed_without_snapshot(cfg, db_conn, monkeypatch):
    """AC-TH-07 VERIFY "all trust call sites": an unavailable authenticated snapshot
    aborts pending staging (no default-policy fallback) and appends nothing."""
    real = trust.authenticated_snapshot
    before = real(cfg).head["head_sequence"]
    _script_snapshots(monkeypatch, [trust.TrustLedgerCorruptError("down")])
    with pytest.raises(trust.TrustLedgerCorruptError):
        trust.record_pending_intake(
            cfg,
            db_conn,
            engram_id="subject",
            content_digest="a" * 64,
            source_channel="external_file",
            actor="t",
        )
    assert real(cfg).head["head_sequence"] == before
