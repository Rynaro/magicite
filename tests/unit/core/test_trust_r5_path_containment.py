"""Round-5 ATLAS regressions: quarantine path containment + reserved quarantine."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from magicite.core import registry as registry_mod
from magicite.engram.digests import sha256_hex
from magicite.errors import InvalidInputError

pytestmark = pytest.mark.acceptance


def _lint_valid_egr(*, name: str, eid: str) -> str:
    return (
        "---\n"
        "spec: engram/0.2\n"
        f"name: {name}\n"
        f"id: {eid}\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: Probe quarantine path containment\n"
        "  use_when: testing journal member path validation\n"
        "  not_when: zip-slip paths escape the registry\n"
        "triggers:\n"
        "  positive: [path containment, quarantine, reserved]\n"
        "  negative: [path escape via journal]\n"
        "---\n"
        "## Procedure\n"
        "1. Keep paths inside the registry root.\n"
        "## Pitfalls\n"
        "- Quarantine moves that follow '../../secret.txt'\n"
        "## Examples\n"
        "+ contained quarantine\n"
        "- escaped secret\n"
    )


def _auth_incomplete_journal(cfg, *, members: list[dict], aborted: list[str]) -> dict:
    return registry_mod._sign_publish_journal(
        cfg,
        {
            "schema": "BundlePublishJournal/1",
            "bundle_id": "r5-probe",
            "complete": False,
            "members": members,
            "pre_existing_paths": [],
            "aborted_engram_ids": aborted,
        },
    )


# ── MAJOR 1: path containment in quarantine rollback ──────────────────────


def test_quarantine_rejects_path_escape_members(cfg, db_conn, embedder, tmp_path: Path) -> None:
    secret = cfg.project_root / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    secret_bytes = secret.read_bytes()

    # Also plant a legit registry member for the positive case in a separate job.
    legit_rel = "skills/legit.egr.md"
    legit = cfg.registry_dir / legit_rel
    legit.parent.mkdir(parents=True, exist_ok=True)
    legit_payload = _lint_valid_egr(name="legit", eid="egr_1e91f001").encode()
    legit.write_bytes(legit_payload)

    for bad_path in ("../../secret.txt", "/tmp/abs.egr.md", "C:/windows/system32/x"):
        job_id = "escape-" + sha256_hex(bad_path.encode())[:8]
        job = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / job_id
        job.mkdir(parents=True, exist_ok=True)
        member = {
            "path": bad_path,
            "sha256": sha256_hex(secret_bytes),
            "size": len(secret_bytes),
            "pre_existing": False,
        }
        # Force digest match against secret when escape would hit it.
        if bad_path.startswith("../"):
            member["sha256"] = sha256_hex(secret_bytes)
            member["size"] = len(secret_bytes)
        journal = _auth_incomplete_journal(cfg, members=[member], aborted=["egr_bad00001"])
        (job / registry_mod._PUBLISH_JOURNAL_NAME).write_text(
            json.dumps(journal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    # Symlinked registry member pointing outside.
    link_rel = "skills/link.egr.md"
    link_path = cfg.registry_dir / link_rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    os.symlink(secret, link_path)
    job_link = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / "symlink-job"
    job_link.mkdir(parents=True, exist_ok=True)
    link_journal = _auth_incomplete_journal(
        cfg,
        members=[
            {
                "path": link_rel,
                "sha256": sha256_hex(secret_bytes),
                "size": len(secret_bytes),
                "pre_existing": False,
            }
        ],
        aborted=["egr_1e91f001"],
    )
    (job_link / registry_mod._PUBLISH_JOURNAL_NAME).write_text(
        json.dumps(link_journal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Genuine contained member in its own job.
    job_ok = cfg.registry_dir / registry_mod._IMPORT_STAGING_DIRNAME / "ok-job"
    job_ok.mkdir(parents=True, exist_ok=True)
    ok_journal = _auth_incomplete_journal(
        cfg,
        members=[
            {
                "path": legit_rel,
                "sha256": sha256_hex(legit_payload),
                "size": len(legit_payload),
                "pre_existing": False,
            }
        ],
        aborted=["egr_1e91f001"],
    )
    (job_ok / registry_mod._PUBLISH_JOURNAL_NAME).write_text(
        json.dumps(ok_journal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    registry_mod.sync(cfg, db_conn, embedder)

    assert secret.exists()
    assert secret.read_bytes() == secret_bytes
    assert not (cfg.data_dir / "quarantine" / "import-rollback").joinpath(
        "secret.txt"
    ).exists()
    # Symlink target (project secret) must remain; link itself may remain.
    assert secret.exists()
    # Legit member moved into quarantine.
    assert not legit.exists()
    q = cfg.data_dir / "quarantine" / "import-rollback" / "ok-job" / legit_rel
    assert q.is_file()
    assert q.read_bytes() == legit_payload


def test_safe_journal_member_path_rejects_escapes() -> None:
    for bad in ("../../x", "/abs", "a\\b", "C:/x", "//server/share", "a\x00b", "café/x"):
        with pytest.raises(InvalidInputError):
            registry_mod._assert_safe_journal_member_path(bad)
    assert str(registry_mod._assert_safe_journal_member_path("skills/ok.egr.md")) == (
        "skills/ok.egr.md"
    )


# ── MINOR 2: quarantine is reserved for register/discover ─────────────────


def test_register_refuses_quarantine_rollback_debris(cfg, db_conn, embedder) -> None:
    debris = (
        cfg.data_dir
        / "quarantine"
        / "import-rollback"
        / "old-job"
        / "skills"
        / "debris.egr.md"
    )
    debris.parent.mkdir(parents=True, exist_ok=True)
    debris.write_text(_lint_valid_egr(name="debris", eid="egr_debf1501"), encoding="utf-8")
    rel = str(debris.relative_to(cfg.project_root))
    with pytest.raises(InvalidInputError, match="reserved|quarantine|staging"):
        registry_mod.register(cfg, db_conn, embedder, path=rel, fmt="egr")
    assert db_conn.execute(
        "SELECT id FROM engram WHERE id = ?", ("egr_debf1501",)
    ).fetchone() is None
