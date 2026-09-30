"""Read-only-to-checkout diagnostic; all trust mutations use a disposable registry.

This records the held integrity finding, not a test requiring the bug to persist.
Run from the repository root with its installed dependency environment.
"""
from __future__ import annotations

import json
import runpy
import tempfile
from pathlib import Path

from magicite.config import Config
from magicite.core import registry, trust
from magicite.storage import db


def main() -> None:
    subject = runpy.run_path("tests/integration/test_trust_recovery.py")["_subject"]
    with tempfile.TemporaryDirectory(prefix="magicite-trust-diagnostic-") as directory:
        cfg = Config.load(Path(directory), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        cfg.ensure_dirs()
        engram_id, digest = subject(cfg)
        conn = db.connect(cfg.db_path)
        try:
            registry.review_approve(cfg, conn, engram_id=engram_id, expected_digest=digest,
                                    actor="diagnostic-reviewer", event_id="diagnostic-admit")
            admitted = trust.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
            revoked = trust.revoke(cfg, conn, engram_id=engram_id, expected_digest=digest,
                                   actor="diagnostic-reviewer", event_id="diagnostic-revoke")
            after_revoke = trust.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
            matches = [p for p in trust.trust_decisions_dir(cfg).glob("*.json")
                       if json.loads(p.read_text())["decision_id"] == revoked.decision_id]
            assert len(matches) == 1
            matches[0].unlink()
            after_loss = trust.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
            live_view = registry.trust_view_for(cfg, conn, engram_id=engram_id)
            print(json.dumps({"schema": "magicite/trust-mirror-diagnostic/1",
                              "admitted_before_revoke": admitted,
                              "admitted_after_revoke": after_revoke,
                              "admitted_after_revoke_mirror_deleted": after_loss,
                              "live_trust_view_admitted_after_loss": live_view.admitted,
                              "status": "FAIL" if after_loss else "NOT_REPRODUCED",
                              "scope": "Disposable registry; existing trust domain unchanged"}, indent=2))
        finally:
            conn.close()


if __name__ == "__main__":
    main()
