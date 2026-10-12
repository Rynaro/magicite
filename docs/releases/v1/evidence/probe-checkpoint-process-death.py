"""AC-S09-02: a child exits without cleanup after ACK; the parent rebuilds."""
from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

from magicite.config import Config
from magicite.core import evidence
from magicite.storage import db

EVENT_ID = "s16_process_death_receipt"


def child(root: str) -> None:
    cfg = Config.load(Path(root), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    event_factory = runpy.run_path("tests/integration/test_evidence_durability.py")["_decision_event"]
    event = event_factory(event_id=EVENT_ID, extra={})
    ack = evidence.checkpoint(cfg, conn, event)
    print(json.dumps({"durable": ack.durable, "sequence": ack.sequence}), flush=True)
    os._exit(23)  # Intentionally bypass conn.close(), finally blocks and Python shutdown.


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
        return
    with tempfile.TemporaryDirectory(prefix="magicite-checkpoint-death-") as directory:
        proc = subprocess.run([sys.executable, __file__, "--child", directory],
                              capture_output=True, text=True, timeout=30, check=False)
        assert proc.returncode == 23, (proc.returncode, proc.stderr)
        ack = json.loads(proc.stdout)
        assert ack["durable"] is True
        cfg = Config.load(Path(directory), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        conn = db.connect(cfg.db_path)
        try:
            conn.execute("DELETE FROM evidence_event_projection")
            conn.execute("UPDATE evidence_meta SET last_sequence = 0 WHERE id = 1")
            assert conn.execute("SELECT COUNT(*) FROM evidence_event_projection").fetchone()[0] == 0
            rebuilt = evidence.rebuild_projections(cfg, conn)
            recovered = evidence.load_event(cfg, EVENT_ID)
            row = conn.execute("SELECT sequence FROM evidence_event_projection WHERE event_id = ?",
                               (EVENT_ID,)).fetchone()
            assert rebuilt >= 1 and recovered is not None and row is not None
            assert row["sequence"] == ack["sequence"]
            print(json.dumps({"schema": "magicite/checkpoint-process-death/1", "status": "PASS",
                              "child_exit_code": proc.returncode, "ack_durable": ack["durable"],
                              "projection_wiped_before_rebuild": True, "recovered_event": True,
                              "sequence_matches_ack": row["sequence"] == ack["sequence"]}, indent=2))
        finally:
            conn.close()


if __name__ == "__main__":
    main()
