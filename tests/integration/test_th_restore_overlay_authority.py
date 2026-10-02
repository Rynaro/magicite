"""AC-TH-09 closure: RecoveryOverlay / fingerprint-key MAC cannot substitute for custody
authority; the witnessed restore/import paths write no ``trust/decisions/`` mirror authority."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.config import Config
from magicite.core import backup as backup_mod
from magicite.core import bundles as bundles_mod
from magicite.core import fingerprint_key as fk
from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod
from magicite.errors import InvalidInputError

pytestmark = pytest.mark.acceptance

SRC = Path(backup_mod.__file__).resolve().parents[1]
REFUSAL = "recovery overlay trust record lacks authenticated custody history"


def _revoked_backup(cfg: Config, conn, tmp_path: Path):
    """Admit then revoke one engram, snapshot, return identifiers and the fingerprint key."""
    outcome = registry_mod.register(cfg, conn, _embedder(), path=".magicite/engrams")
    engram_id = outcome.registered[0].id
    digest = conn.execute("SELECT content_sha256 FROM engram WHERE id = ?", (engram_id,)).fetchone()[0]
    admit = registry_mod.review_approve(
        cfg, conn, engram_id=engram_id, expected_digest=digest, actor="reviewer", event_id="evt-th09-a"
    )
    revoke = registry_mod.review_revoke(
        cfg,
        conn,
        engram_id=engram_id,
        actor="reviewer",
        expected_digest=digest,
        reason="th09",
        event_id="evt-th09-r",
    )
    key = fk.load_or_create_fingerprint_key(cfg)
    backup = tmp_path / "backup"
    backup_mod.create_snapshot(cfg, conn, backup)
    return engram_id, admit, revoke, key, backup


def _embedder():
    from magicite.embeddings.hashing_provider import get_embedder

    return get_embedder(dim=256)


def _resign(overlay: backup_mod.RecoveryOverlay, records: list[dict[str, Any]], key: bytes):
    """Build an overlay whose MAC and content hashes are genuinely valid for ``records``."""
    body = overlay.body_for_mac()
    body["revocation_records"] = records
    body["content_hashes"] = sorted(
        hashlib.sha256(backup_mod._canonical_json(r).encode()).hexdigest()  # noqa: SLF001
        for r in (*overlay.deletion_records, *records)
    )
    body["mac"] = backup_mod.sign_overlay(body, key=key)
    forged = backup_mod.RecoveryOverlay.from_dict(body)
    backup_mod.verify_overlay(forged, key=key)  # the MAC really is valid: only authority is missing
    return forged


def _legit_overlay(cfg: Config, key: bytes) -> backup_mod.RecoveryOverlay:
    return backup_mod.build_recovery_overlay(cfg, control_sequence=10**6, operator_provenance="th09", key=key)


def test_legit_overlay_in_custody_history_is_accepted(cfg, db_conn, tmp_path) -> None:
    """AC-TH-09 VERIFY 'pre-revoke backup/current-suffix restoration' positive control for the
    RecoveryOverlay substitution cases: a production-built overlay whose revocation record is
    in authenticated custody history restores and the revoke stays effective."""
    engram_id, _admit, revoke, key, backup = _revoked_backup(cfg, db_conn, tmp_path)
    overlay = _legit_overlay(cfg, key)
    assert [r["decision_id"] for r in overlay.revocation_records] == [revoke.decision_id]
    result = backup_mod.restore_snapshot(cfg, db_conn, backup, overlay=overlay, custody_key=key)
    assert result["status"] == "ok", result
    assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"


VARIANTS = ["absent_from_custody", "resequenced_later_timestamp", "wrong_id_content"]
# Observed default-path (live overlay preserved + merged) outcome per variant. Anti-shrink fires
# first when the forged id is not in the live overlay; the resequenced record shares the live
# engram_id, wins the merge on timestamp and reaches the custody check.
_PRESERVE_OUTCOME = {
    "absent_from_custody": ("staged", "live_overlay_shrunk"),
    "resequenced_later_timestamp": ("raised", REFUSAL),
    "wrong_id_content": ("staged", "live_overlay_shrunk"),
}


def _forge(cfg, key, admit, variant):
    legit = _legit_overlay(cfg, key)
    real = dict(legit.revocation_records[0])
    if variant == "absent_from_custody":
        extra = {**real, "decision_id": "td_forged000001", "engram_id": "ghost"}
        records = [*legit.revocation_records, extra]
    elif variant == "resequenced_later_timestamp":
        records = [{**real, "timestamp": "2999-01-01T00:00:00+00:00"}]
    else:  # a custody-present decision id carrying different (revoke) content
        records = [*legit.revocation_records, {**real, "decision_id": admit.decision_id}]
    return _resign(legit, records, key)


def _assert_closed_and_not_authority(cfg, db_conn, engram_id, before):
    """Gate closed (markers stamped, no activation seal); nothing routable, readable or admitted."""
    from magicite.core import recovery_gate as gate
    from magicite.core import router as router_mod
    from magicite.mcp import bind_retrieval as br
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput

    status = gate.reconciliation_status(cfg)
    assert status["reconciliation_required"] is True, status
    assert "without activation seal" in status["reason"], status
    assert not backup_mod.recovery_activation_path(cfg).exists()
    with pytest.raises(InvalidInputError, match="routing disabled: reconciliation_required"):
        router_mod.route(cfg, db_conn, _embedder(), query="proton", k=10)
    row = db_conn.execute("SELECT name, content_sha256 FROM engram WHERE id=?", (engram_id,)).fetchone()
    body = br.load_skill_body(
        ToolContext(cfg=cfg, conn=db_conn, embedder=_embedder()),
        LoadSkillBodyInput(
            name=row["name"],
            level="L2",
            expected_content_digest=row["content_sha256"],
            expected_policy_digest=br._active_policy_digest(cfg) or "none",  # noqa: SLF001
        ),
    )
    assert not (body.procedure or "").strip(), body.status
    assert not trust_mod.admission_still_valid(cfg, engram_id=engram_id, content_digest=row["content_sha256"])
    assert [d.decision_id for d in trust_mod.list_decisions(cfg)] == before
    assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"


@pytest.mark.parametrize("variant", VARIANTS)
def test_valid_mac_overlay_record_not_in_custody_history_is_refused(cfg, db_conn, tmp_path, variant) -> None:
    """AC-TH-09 VERIFY 'RecoveryOverlay mirrors or local fingerprint-key MACs SHALL NOT be
    resequenced or substituted for custodian authority' (ledger gap: valid RecoveryOverlay
    revocation records / fingerprint-key MACs). The overlay MAC is valid under the fingerprint
    key, yet the record is absent from, or differs from, custody history: with the caller overlay
    alone restore raises the custody refusal; files were already replaced, but the generation gate
    stays closed (no seal) and routing, body and admission stay closed."""
    engram_id, admit, _revoke, key, backup = _revoked_backup(cfg, db_conn, tmp_path)
    forged = _forge(cfg, key, admit, variant)
    before = [d.decision_id for d in trust_mod.list_decisions(cfg)]
    with pytest.raises(InvalidInputError, match=REFUSAL):
        backup_mod.restore_snapshot(
            cfg, db_conn, backup, overlay=forged, custody_key=key, preserve_live_overlay=False
        )
    _assert_closed_and_not_authority(cfg, db_conn, engram_id, before)


@pytest.mark.parametrize("variant", VARIANTS)
def test_valid_mac_overlay_record_default_preserve_path_outcome(cfg, db_conn, tmp_path, variant) -> None:
    """AC-TH-09 same sub-clause, default path (live overlay preserved + merged): exact
    per-variant outcome (_PRESERVE_OUTCOME) and a closed gate with no authority afterwards."""
    engram_id, admit, _revoke, key, backup = _revoked_backup(cfg, db_conn, tmp_path)
    forged = _forge(cfg, key, admit, variant)
    before = [d.decision_id for d in trust_mod.list_decisions(cfg)]
    kind, expected = _PRESERVE_OUTCOME[variant]
    if kind == "raised":
        with pytest.raises(InvalidInputError, match=expected):
            backup_mod.restore_snapshot(cfg, db_conn, backup, overlay=forged, custody_key=key)
    else:
        result = backup_mod.restore_snapshot(cfg, db_conn, backup, overlay=forged, custody_key=key)
        assert result["status"] == "reconciliation_required" and result["activated"] is False, result
        assert expected in result["reason"], result
    _assert_closed_and_not_authority(cfg, db_conn, engram_id, before)


# --- AC-TH-09 'all direct mirror-writing restore paths' --------------------------------------

_MIRROR_NAMES = {"trust_decisions_dir", "_decision_mirror_path", "trust_dir"}
# Reference sites of the trust directory / decisions location. "defn/read" sites must not call a
# write primitive; "generic" sites are archive-driven copies (no per-decision path) and are
# covered by the dynamic planted-mirror test below.
_ALLOWED_MIRROR_REFERENCES = {
    ("__main__.py", "trust_list_cmd"): ("defn/read", "JSON key 'decisions' of a list response"),
    ("core/backup.py", "<module>"): ("defn/read", "module docstring text"),
    ("core/backup.py", "_stamp_restore_generation"): ("generic", "marker file under trust/"),
    ("core/backup.py", "_iter_domain_files"): ("generic", "archive source enumeration (read)"),
    ("core/backup.py", "_preserve_live_overlay"): ("generic", "existence check only"),
    ("core/backup.py", "restore_snapshot"): ("generic", "clears/restores trust/ from the archive"),
    ("core/trust.py", "trust_decisions_dir"): ("defn/read", "path definition"),
    ("core/trust.py", "trust_policy_path"): ("defn/read", "path definition"),
    ("core/trust.py", "trust_roots_path"): ("defn/read", "path definition"),
    ("core/trust.py", "ensure_trust_dirs"): ("defn/read", "mkdir of the empty directory only"),
    ("core/trust.py", "_decision_mirror_path"): ("defn/read", "dead helper (no callers; test below)"),
    ("core/trust_legacy.py", "_preview"): ("defn/read", "READ-only enumeration of legacy bytes"),
    ("core/trust_legacy.py", "_migration_records"): ("defn/read", "plan dict key 'decisions'"),
}
_WRITE_CALLS = {
    "write_text", "write_bytes", "open", "replace", "rename", "copyfile", "copy", "copy2",
    "copytree", "move", "touch", "write", "dump", "mkdir_write", "_write_json_durable",
    "_copy_file_durable", "_write_bytes_durable",
}  # fmt: skip


def _mirror_references() -> dict[tuple[str, str], list[ast.AST]]:
    """Every Name/Attribute/str-constant reference (function AND module level) to the trust dir
    or decisions location, keyed by (file, enclosing function or '<module>')."""
    found: dict[tuple[str, str], list[ast.AST]] = {}

    def visit(node: ast.AST, rel: str, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else fn
            hit = (
                (isinstance(child, ast.Name) and child.id in _MIRROR_NAMES)
                or (isinstance(child, ast.Attribute) and child.attr in _MIRROR_NAMES)
                or (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and (child.value == "decisions" or "trust/decisions" in child.value)
                )
            )
            if hit:
                found.setdefault((rel, scope), []).append(child)
            visit(child, rel, scope)

    for path in sorted(SRC.rglob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(SRC).as_posix(), "<module>")
    return found


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            f = c.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def test_mirror_location_references_are_allowlisted_and_not_writes() -> None:
    """AC-TH-09 VERIFY 'all direct mirror-writing restore paths' (static part). Claims only: no
    src code references the trust dir / decisions location (by helper name, 'decisions' constant
    or 'trust/decisions' literal, at function or module level) except the allowlisted sites, and
    the defn/read sites call no write primitive. NOT claimed: paths assembled dynamically from
    other constants; archive-driven generic copies are covered by the dynamic planted-mirror test."""
    found = _mirror_references()
    unexpected = set(found) - set(_ALLOWED_MIRROR_REFERENCES)
    assert not unexpected, f"unreviewed reference to the trust/decisions location: {sorted(unexpected)}"
    # Module-level references may only be string constants (the docstring), never code.
    for (rel, fn_name), nodes in found.items():
        if fn_name == "<module>":
            assert all(isinstance(n, ast.Constant) for n in nodes), (rel, [n.lineno for n in nodes])
    assert set(_ALLOWED_MIRROR_REFERENCES) <= set(found), set(_ALLOWED_MIRROR_REFERENCES) - set(found)
    for (rel, fn_name), (kind, _why) in _ALLOWED_MIRROR_REFERENCES.items():
        if kind != "defn/read" or fn_name == "<module>":
            continue
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        assert not (_called_names(fn) & _WRITE_CALLS), (rel, fn_name, _called_names(fn) & _WRITE_CALLS)


def test_decision_mirror_helpers_have_no_callers() -> None:
    """AC-TH-09 supporting evidence: _decision_mirror_path/_load_decision_file are uncalled
    (dead code, reported not deleted); a scanner that finds them proves it can see callers."""
    refs: dict[str, list[str]] = {"_decision_mirror_path": [], "_load_decision_file": []}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                )
                if name in refs:
                    refs[name].append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert refs == {"_decision_mirror_path": [], "_load_decision_file": []}


def test_planted_decision_mirror_in_snapshot_is_inert_after_restore(cfg, db_conn, tmp_path) -> None:
    """AC-TH-09 VERIFY 'all direct mirror-writing restore paths' (dynamic, generic archive copy):
    a forged admit mirror planted in live trust/decisions is archived and the generic restore
    copies it verbatim as an INERT file; it does not become authority (not in the authenticated
    decision list, revoke still latest, admission still invalid)."""
    engram_id, admit, revoke, key, backup_unused = _revoked_backup(cfg, db_conn, tmp_path)
    forged = {**admit.to_dict(), "decision_id": "td_planted0001", "timestamp": "2999-01-01T00:00:00+00:00"}
    mirror = trust_mod.trust_decisions_dir(cfg) / "td_planted0001.json"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(json.dumps(forged))
    snapshot = tmp_path / "backup-planted"
    backup_mod.create_snapshot(cfg, db_conn, snapshot)
    assert "trust/decisions/td_planted0001.json" in {
        e["path"] for e in json.loads((snapshot / "manifest.json").read_text())["files"]
    }  # positive control: the planted mirror really is in the archive
    mirror.unlink()
    ids_before = [d.decision_id for d in trust_mod.list_decisions(cfg)]
    result = backup_mod.restore_snapshot(cfg, db_conn, snapshot, custody_key=key)
    assert result["status"] == "ok", result
    assert mirror.read_text() == json.dumps(forged)  # copied verbatim, inert
    assert [d.decision_id for d in trust_mod.list_decisions(cfg)] == ids_before
    assert "td_planted0001" not in ids_before
    assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"
    digest = db_conn.execute("SELECT content_sha256 FROM engram WHERE id=?", (engram_id,)).fetchone()[0]
    assert not trust_mod.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)


def _decision_files(cfg: Config) -> dict[str, bytes]:
    root = trust_mod.trust_decisions_dir(cfg)
    return {p.name: p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def test_backup_restore_and_overlay_apply_write_no_decision_mirrors(cfg, db_conn, tmp_path) -> None:
    """AC-TH-09 VERIFY 'all direct mirror-writing restore paths' (dynamic): backup restore, with
    and without a caller RecoveryOverlay apply, leaves trust/decisions/ free of files after a
    custody-backed admit+revoke history (which itself writes none)."""
    engram_id, _a, _r, key, backup = _revoked_backup(cfg, db_conn, tmp_path)
    assert _decision_files(cfg) == {}
    plain = backup_mod.restore_snapshot(cfg, db_conn, backup, custody_key=key)
    assert plain["status"] == "ok", plain
    assert _decision_files(cfg) == {}
    with_overlay = backup_mod.restore_snapshot(
        cfg, db_conn, backup, overlay=_legit_overlay(cfg, key), custody_key=key
    )
    assert with_overlay["status"] == "ok", with_overlay
    assert with_overlay["overlay"]["revokes_applied"] >= 1  # overlay path genuinely ran
    assert _decision_files(cfg) == {}
    assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"


def test_bundle_import_writes_no_decision_mirrors(cfg, db_conn, embedder, tmp_path) -> None:
    """AC-TH-09 VERIFY 'all direct mirror-writing restore paths': signed bundle import writes
    no decision mirror (positive control: the import ingested a skill)."""
    from tests.unit.core.test_trust_r2_import_safety import _minimal_egr

    src = tmp_path / "bundle-src" / "skills"
    src.mkdir(parents=True)
    (src / "m.egr.md").write_bytes(_minimal_egr(name="mirrorless", eid="egr_ae200001").encode())
    key = Ed25519PrivateKey.generate()
    trust_mod.pin_trust_root(cfg, public_key_bytes=key.public_key().public_bytes_raw())
    archive = tmp_path / "bundle.zip"
    bundles_mod.write_signed_bundle(source_dir=tmp_path / "bundle-src", out_path=archive, private_key=key)
    outcome = registry_mod.import_bundle(cfg, db_conn, embedder, archive_path=archive)
    assert outcome.ingested == 1
    assert _decision_files(cfg) == {}


def test_migration_restore_writes_no_live_decision_mirrors(legacy_migration_case, tmp_path) -> None:
    """AC-TH-09 VERIFY 'all direct mirror-writing restore paths': documented `migration restore`
    materializes only inactive staging; live trust/decisions is byte-identical before/after.
    Positive control: the legacy backup carries a decision mirror that is staged, not restored."""
    from magicite.core import migration as migration_mod
    from magicite.core import trust_legacy

    root = tmp_path / "legacy"
    root.mkdir()
    policy = trust_mod.default_policy()
    legacy_decision = trust_mod.TrustDecision(
        decision_id="td_legacy0001",
        engram_id="legacy-subject",
        content_digest="a" * 64,
        decision="admit",
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="legacy",
        timestamp="2026-09-30T00:00:00Z",
    )
    mirror_dir = root / ".magicite" / "trust" / "decisions"
    mirror_dir.mkdir(parents=True)
    (mirror_dir / "td_legacy0001.json").write_text(json.dumps(legacy_decision.to_dict()))
    cfg, plan, backup, _provider = legacy_migration_case(root)
    live_before = _decision_files(cfg)
    assert "td_legacy0001.json" in live_before
    stage = tmp_path / "inactive-stage"
    result = migration_mod.restore(
        cfg,
        backup_path=backup,
        reviewed_sha256=trust_legacy.digest(plan),
        staging_path=stage,
    )
    assert result.state == "reconciliation_required"
    assert any(stage.rglob("td_legacy0001.json"))  # staged, inactive
    assert _decision_files(cfg) == live_before
