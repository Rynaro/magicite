"""AC-S09-02 supplementary probe at r2 source: a child exits after ACK; parent rebuilds.

Same scenario as docs/releases/v1/evidence/probe-checkpoint-process-death.py,
which no longer runs on current source because evidence writes now require an
enrolled custodian (it fails closed with CustodianError). The parent enrolls a
*simulated* fixture custodian (tests/support/custody_adapter.py); the child only
attaches that existing authority, checkpoints one event and exits via
os._exit(23). This is not a distinct-UID or installed-channel qualification.
State lives in temporary directories; the JSON report is printed to stdout.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from magicite.config import Config  # noqa: E402
from magicite.core import evidence  # noqa: E402
from magicite.storage import db  # noqa: E402

EVENT_ID = "s16_process_death_receipt"
ENV = {"MAGICITE_EMBEDDING_PROVIDER": "hashing"}


def child(root: str, custody: str, registry_id: str) -> None:
    from tests.support.custody_adapter import attach_fixture

    cfg = Config.load(Path(root), env=ENV)
    with attach_fixture(Path(root), Path(custody), registry_id):
        conn = db.connect(cfg.db_path)
        factory = runpy.run_path(str(REPO_ROOT / "tests/integration/test_evidence_durability.py"))
        event = factory["_decision_event"](event_id=EVENT_ID, extra={})
        ack = evidence.checkpoint(cfg, conn, event)
        print(json.dumps({"durable": ack.durable, "sequence": ack.sequence}), flush=True)
        os._exit(23)  # Intentionally bypass conn.close(), finally blocks and Python shutdown.


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3], sys.argv[4])
        return 0
    import pytest
    from tests.support.custody_adapter import attach_fixture, enroll_fixture

    with (
        tempfile.TemporaryDirectory(prefix="magicite-checkpoint-death-r2-") as directory,
        tempfile.TemporaryDirectory(prefix="magicite-custody-r2-") as custody_root,
    ):
        custody = Path(custody_root) / "private"
        cfg = Config.load(Path(directory), env=ENV)
        with pytest.MonkeyPatch.context() as monkeypatch:
            provider = enroll_fixture(cfg, monkeypatch, custody)
            registry_id = provider.registry_id
            cfg.ensure_dirs()
            provider.close()
        proc = subprocess.run(
            [sys.executable, __file__, "--child", directory, str(custody), registry_id],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 23, (proc.returncode, proc.stderr[-2000:])
        ack = json.loads(proc.stdout)
        assert ack["durable"] is True
        with attach_fixture(Path(directory), custody, registry_id):
            conn = db.connect(cfg.db_path)
            try:
                conn.execute("DELETE FROM evidence_event_projection")
                conn.execute("UPDATE evidence_meta SET last_sequence = 0 WHERE id = 1")
                conn.commit()
                wiped = conn.execute("SELECT COUNT(*) FROM evidence_event_projection").fetchone()[0] == 0
                rebuilt = evidence.rebuild_projections(cfg, conn)
                recovered = evidence.load_event(cfg, EVENT_ID)
                row = conn.execute(
                    "SELECT sequence FROM evidence_event_projection WHERE event_id = ?", (EVENT_ID,)
                ).fetchone()
            finally:
                conn.close()
        ok = wiped and rebuilt >= 1 and recovered is not None and row is not None
        matches = bool(row is not None and row["sequence"] == ack["sequence"])
        print(
            json.dumps(
                {
                    "schema": "magicite/checkpoint-process-death/2",
                    "status": "PASS" if ok and matches else "FAIL",
                    "child_exit_code": proc.returncode,
                    "ack_durable": ack["durable"],
                    "projection_wiped_before_rebuild": wiped,
                    "recovered_event": recovered is not None,
                    "sequence_matches_ack": matches,
                    "custody": "simulated fixture custodian in parent and child; not deployment proof",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if ok and matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
