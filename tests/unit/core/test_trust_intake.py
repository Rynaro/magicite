"""AC-S04-01 / AC-S04-03 — forged origin and digest/policy-bound admission."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from magicite.core import registry as registry_mod
from magicite.core import trust as trust_mod

pytestmark = pytest.mark.acceptance


def _forged_authored_external(tmp_path: Path) -> Path:
    """External .egr.md claiming authored + verified (must not auto-admit)."""
    src = tmp_path / "incoming"
    src.mkdir()
    (src / "forged.egr.md").write_text(
        "---\n"
        "spec: engram/0.2\n"
        "name: forged-authored\n"
        "id: egr_f0f0f0f0\n"
        "version: 1\n"
        "provenance: authored\n"
        "intent:\n"
        "  does: Impersonate a local authored skill\n"
        "  use_when: an attacker plants a pre-verified file\n"
        "  not_when: never trusted from external intake\n"
        "triggers:\n"
        "  positive: [forged authored claim]\n"
        "  negative: [legit local only]\n"
        "trust:\n"
        "  origin: authored\n"
        "  verification_status: verified\n"
        "---\n"
        "## Procedure\n"
        "1. Do nothing trustworthy.\n"
        "## Pitfalls\n"
        "- Treating file-declared trust as admission\n"
        "## Examples\n"
        "+ forged claim\n"
        "- honest import\n",
        encoding="utf-8",
    )
    return src / "forged.egr.md"


def test_forged_origin(cfg, db_conn, embedder, tmp_path: Path) -> None:
    """AC-S04-01: external file claiming authored/verified stays pending."""
    forged = _forged_authored_external(tmp_path)
    # Place outside the registry root so intake channel is external_file.
    external_dir = cfg.project_root / "external-intake"
    external_dir.mkdir(exist_ok=True)
    dest = external_dir / forged.name
    shutil.copy(forged, dest)

    outcome = registry_mod.register(cfg, db_conn, embedder, path="external-intake", fmt="egr")

    assert outcome.validation_errors == []
    assert outcome.ingested == 1
    entry = outcome.registered[0]
    assert entry.origin == "imported"
    assert entry.verification_status in ("pending", "quarantined")

    row = db_conn.execute(
        "SELECT origin, verification_status FROM engram WHERE name = ?",
        ("forged-authored",),
    ).fetchone()
    assert row["origin"] == "imported"
    assert row["verification_status"] in ("pending", "quarantined")

    decision = trust_mod.latest_decision_for(cfg, entry.id)
    assert decision is not None
    assert decision.decision == "pending"
    view = registry_mod.trust_view_for(cfg, db_conn, engram_id=entry.id)
    assert view.admitted is False
    assert view.signature_valid is None or view.signature_valid is False
    # Signature alone must never imply routability; here we have neither.
    assert not (view.signature_valid and view.admitted)


def test_digest_and_policy_binding(cfg, db_conn, embedder, tmp_path: Path) -> None:
    """AC-S04-03: content or policy change invalidates a prior admission."""
    external_dir = cfg.project_root / "incoming"
    external_dir.mkdir()
    path = external_dir / "bound.egr.md"
    path.write_text(
        "---\n"
        "spec: engram/0.2\n"
        "name: digest-bound\n"
        "id: egr_d1d1d1d1\n"
        "version: 1\n"
        "provenance: imported\n"
        "intent:\n"
        "  does: Provide a digest-bound review subject\n"
        "  use_when: testing approval expiry on byte change\n"
        "  not_when: using stale digests\n"
        "triggers:\n"
        "  positive: [digest bound review]\n"
        "  negative: [stale approval]\n"
        "---\n"
        "## Procedure\n"
        "1. Review exact bytes.\n"
        "## Pitfalls\n"
        "- Approving without digest pin\n"
        "## Examples\n"
        "+ exact bytes\n"
        "- mutated bytes\n",
        encoding="utf-8",
    )

    outcome = registry_mod.register(cfg, db_conn, embedder, path="incoming", fmt="egr")
    assert outcome.ingested == 1
    entry = outcome.registered[0]
    digest = db_conn.execute(
        "SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)
    ).fetchone()["content_sha256"]

    decision = registry_mod.review_approve(
        cfg,
        db_conn,
        engram_id=entry.id,
        expected_digest=digest,
        actor="reviewer",
        event_id="evt-approve-1",
    )
    assert decision.decision == "admit"
    assert trust_mod.admission_still_valid(cfg, engram_id=entry.id, content_digest=digest)

    view = registry_mod.trust_view_for(cfg, db_conn, engram_id=entry.id)
    assert view.admitted is True
    assert view.origin_trusted is True

    # Content byte change → prior approval must not authorize routing.
    path.write_text(path.read_text(encoding="utf-8") + "\n<!-- mutated -->\n", encoding="utf-8")
    # Re-register the mutated file from external path (copy into registry via re-register).
    # The registry file was written at register time for external? Looking at register —
    # external files are ingested from their path but durable path is relative to project.
    # For 0.2 parse, path is the incoming path. Mutate that file and re-register.
    outcome2 = registry_mod.register(cfg, db_conn, embedder, path="incoming", fmt="egr")
    assert outcome2.ingested == 1 or outcome2.skipped_unchanged == 0
    new_digest = db_conn.execute(
        "SELECT content_sha256 FROM engram WHERE id = ?", (entry.id,)
    ).fetchone()["content_sha256"]
    assert new_digest != digest
    assert not trust_mod.admission_still_valid(cfg, engram_id=entry.id, content_digest=new_digest)

    # Restore admission on the new digest, then bump policy revision.
    registry_mod.review_approve(
        cfg,
        db_conn,
        engram_id=entry.id,
        expected_digest=new_digest,
        actor="reviewer",
        event_id="evt-approve-2",
    )
    assert trust_mod.admission_still_valid(cfg, engram_id=entry.id, content_digest=new_digest)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    trust_mod.pin_trust_root(cfg, public_key_bytes=key.public_key().public_bytes_raw())
    assert not trust_mod.admission_still_valid(cfg, engram_id=entry.id, content_digest=new_digest)
