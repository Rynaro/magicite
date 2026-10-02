"""AC-TH-09 closure: RecoveryOverlay / fingerprint-key MAC cannot substitute for custody
authority, and no restore/import path writes ``trust/decisions/`` decision mirrors."""

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


@pytest.mark.parametrize(
    "variant", ["absent_from_custody", "resequenced_later_timestamp", "wrong_id_content"]
)
def test_valid_mac_overlay_record_not_in_custody_history_is_refused(cfg, db_conn, tmp_path, variant) -> None:
    """AC-TH-09 VERIFY 'RecoveryOverlay mirrors or local fingerprint-key MACs SHALL NOT be
    resequenced or substituted for custodian authority' (ledger gap: valid RecoveryOverlay
    revocation records / fingerprint-key MACs). The overlay MAC is valid under the fingerprint
    key, yet the record is absent from, or differs from, custody history: restore refuses."""
    engram_id, admit, revoke, key, backup = _revoked_backup(cfg, db_conn, tmp_path)
    legit = _legit_overlay(cfg, key)
    real = dict(legit.revocation_records[0])
    if variant == "absent_from_custody":
        record = {**real, "decision_id": "td_forged000001", "engram_id": "ghost-engram"}
        records = [*legit.revocation_records, record]
    elif variant == "resequenced_later_timestamp":
        record = {**real, "timestamp": "2999-01-01T00:00:00+00:00"}
        records = [record]
    else:  # a custody-present decision id carrying different (revoke) content
        record = {**real, "decision_id": admit.decision_id}
        records = [*legit.revocation_records, record]
    forged = _resign(legit, records, key)
    before = [d.decision_id for d in trust_mod.list_decisions(cfg)]

    # Caller overlay only (no live-overlay merge), so the _apply_overlay custody check is the gate.
    with pytest.raises(InvalidInputError, match=REFUSAL):
        backup_mod.restore_snapshot(
            cfg, db_conn, backup, overlay=forged, custody_key=key, preserve_live_overlay=False
        )

    # The forged material never became authority.
    assert [d.decision_id for d in trust_mod.list_decisions(cfg)] == before
    assert trust_mod.latest_decision_for(cfg, engram_id).decision == "revoke"
    assert not backup_mod.recovery_activation_path(cfg).exists()

    # Default path (live overlay preserved + merged): never activates the forged overlay either.
    try:
        merged = backup_mod.restore_snapshot(cfg, db_conn, backup, overlay=forged, custody_key=key)
    except InvalidInputError as exc:
        assert REFUSAL in str(exc)
    else:
        assert merged["activated"] is False and merged["reconciliation_required"] is True, merged
    assert [d.decision_id for d in trust_mod.list_decisions(cfg)] == before


# --- AC-TH-09 'all direct mirror-writing restore paths' --------------------------------------

# Every src reference to the decision-mirror location, with the reason it is not a restore write.
_ALLOWED_MIRROR_REFERENCES = {
    ("core/trust.py", "ensure_trust_dirs"): "mkdir of the (empty) directory only; writes no file",
    ("core/trust.py", "_decision_mirror_path"): "dead helper (no callers; see test below)",
    ("core/trust_legacy.py", "_preview"): "READ-only enumeration of legacy bytes for reviewed migration",
}
_WRITE_CALLS = {
    "write_text",
    "write_bytes",
    "open",
    "replace",
    "rename",
    "copyfile",
    "copy2",
    "_write_json_durable",
}

_MIRROR_NAMES = {"trust_decisions_dir", "_decision_mirror_path"}


def _mirror_references() -> dict[tuple[str, str], list[ast.AST]]:
    found: dict[tuple[str, str], list[ast.AST]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                hit = (
                    (
                        isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and "trust/decisions" in node.value
                    )
                    or (isinstance(node, ast.Name) and node.id in _MIRROR_NAMES)
                    or (isinstance(node, ast.Attribute) and node.attr in _MIRROR_NAMES)
                )
                if hit:
                    found.setdefault((rel, fn.name), []).append(node)
    return found


def test_no_src_module_writes_decision_mirrors_outside_allowlist() -> None:
    """AC-TH-09 VERIFY 'all direct mirror-writing restore paths' (static enumeration): every
    function in src that references the trust/decisions mirror location is on a justified
    allowlist, and none of the allowlisted functions performs a file write to it."""
    found = _mirror_references()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "trust/decisions" in node.value
            ):
                # module docstring (backup.py) and legacy READ filter (trust_legacy.py) only
                assert rel in {"core/backup.py", "core/trust_legacy.py"}, (rel, node.lineno)
    unexpected = set(found) - set(_ALLOWED_MIRROR_REFERENCES)
    assert not unexpected, f"unreviewed reference to the decision-mirror location: {sorted(unexpected)}"
    # Positive control: the scanner does see the known references (not vacuous).
    assert set(_ALLOWED_MIRROR_REFERENCES) <= set(found), set(_ALLOWED_MIRROR_REFERENCES) - set(found)
    # Allowlisted functions must not call a write primitive.
    for path_rel, fn_name in _ALLOWED_MIRROR_REFERENCES:
        tree = ast.parse((SRC / path_rel).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        calls = {
            (c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", ""))
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
        }
        assert not (calls & _WRITE_CALLS), (path_rel, fn_name, calls & _WRITE_CALLS)


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
