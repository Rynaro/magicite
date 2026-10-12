"""Re-run the original S16 mirror-loss scenario against custody-enforced trust.

The c0782fd diagnostic (docs/releases/v1/evidence/reproduce-trust-mirror-loss.py)
cannot run on current source because trust history now requires enrolled custody.
This script enrolls a *simulated* fixture custodian through
tests/support/custody_adapter.py (test-owned dependency injection; it does not
qualify an installed channel or a distinct-UID deployment), then replays the same
admit -> revoke -> delete revoke mirror -> re-check scenario on a disposable
registry. Every attempted loss variant uses its own fresh registry and custodian.

It writes nothing into the checkout: all state lives in temporary directories and
the JSON report is printed to stdout. Run from the repository root with the
project's locked dependency environment.
"""

from __future__ import annotations

import json
import runpy
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402  (MonkeyPatch only; no test collection)
from tests.support.custody_adapter import enroll_fixture  # noqa: E402

from magicite.config import Config  # noqa: E402
from magicite.core import registry, trust  # noqa: E402
from magicite.storage import db  # noqa: E402

SUBJECT = runpy.run_path(str(REPO_ROOT / "tests/integration/test_trust_recovery.py"))["_subject"]


def _decision_mirrors(cfg: Config, decision_id: str) -> list[Path]:
    directory = trust.trust_decisions_dir(cfg)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            if json.loads(path.read_text()).get("decision_id") == decision_id:
                found.append(path)
        except (OSError, ValueError):
            continue
    return found


def _delete_revoke_mirror(cfg: Config, conn: Any, admit: Any, revoke: Any) -> dict[str, Any]:
    """Original step: delete the revocation's writable mirror; replay the admit mirror."""
    mirrors = _decision_mirrors(cfg, revoke.decision_id)
    for path in mirrors:
        path.unlink()
    legacy = trust.trust_decisions_dir(cfg)
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / f"{admit.decision_id}.json").write_text(json.dumps(admit.to_dict()))
    return {
        "revoke_mirror_files_found": len(mirrors),
        "revoke_mirror_files_deleted": len(mirrors),
        "admit_mirror_replayed": True,
    }


def _delete_revoke_projection(cfg: Config, conn: Any, admit: Any, revoke: Any) -> dict[str, Any]:
    """Delete the revocation's SQLite projection row (rebuildable cache)."""
    cursor = conn.execute("DELETE FROM trust_decision WHERE decision_id = ?", (revoke.decision_id,))
    conn.commit()
    return {"projection_rows_deleted": cursor.rowcount}


def _delete_local_authority(cfg: Config, conn: Any, admit: Any, revoke: Any) -> dict[str, Any]:
    """Delete the whole local journal/head tree in addition to mirrors."""
    authority = cfg.data_dir / "trust" / "authority"
    existed = authority.is_dir()
    shutil.rmtree(authority, ignore_errors=False)
    return {"local_authority_tree_existed": existed, "local_authority_tree_deleted": existed}


VARIANTS: dict[str, Callable[..., dict[str, Any]]] = {
    "revoke_mirror_deleted": _delete_revoke_mirror,
    "revoke_projection_row_deleted": _delete_revoke_projection,
    "local_authority_tree_deleted": _delete_local_authority,
}


def _check(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"value": fn()}
    except Exception as exc:  # report the fail-closed class only; never raw content
        return {"closed_error": type(exc).__name__}


def _run_variant(name: str, mutate: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    with (
        tempfile.TemporaryDirectory(prefix="magicite-trust-r2-") as directory,
        tempfile.TemporaryDirectory(prefix="magicite-custody-r2-") as custody_root,
        pytest.MonkeyPatch.context() as monkeypatch,
    ):
        cfg = Config.load(Path(directory), env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        provider = enroll_fixture(cfg, monkeypatch, Path(custody_root) / "private")
        try:
            cfg.ensure_dirs()
            engram_id, digest = SUBJECT(cfg)
            conn = db.connect(cfg.db_path)
            try:
                admit = registry.review_approve(
                    cfg,
                    conn,
                    engram_id=engram_id,
                    expected_digest=digest,
                    actor="diagnostic-reviewer",
                    event_id="diagnostic-admit",
                )
                admitted = trust.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
                revoke = trust.revoke(
                    cfg,
                    conn,
                    engram_id=engram_id,
                    expected_digest=digest,
                    actor="diagnostic-reviewer",
                    event_id="diagnostic-revoke",
                )
                after_revoke = trust.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
                mutation = mutate(cfg, conn, admit, revoke)
                after_loss = _check(
                    lambda: trust.admission_still_valid(cfg, engram_id=engram_id, content_digest=digest)
                )
                view = _check(lambda: registry.trust_view_for(cfg, conn, engram_id=engram_id).admitted)
                latest = _check(lambda: getattr(trust.latest_decision_for(cfg, engram_id), "decision", None))
            finally:
                conn.close()
        finally:
            provider.close()
    reinstated = after_loss.get("value") is True or view.get("value") is True
    return {
        "variant": name,
        "admitted_before_revoke": admitted,
        "admitted_after_revoke": after_revoke,
        "mutation": mutation,
        "admission_after_loss": after_loss,
        "live_trust_view_admitted_after_loss": view,
        "latest_decision_after_loss": latest,
        "admission_reinstated": reinstated,
    }


def main() -> int:
    results = [_run_variant(name, mutate) for name, mutate in VARIANTS.items()]
    precondition = all(r["admitted_before_revoke"] and not r["admitted_after_revoke"] for r in results)
    reproduced = any(r["admission_reinstated"] for r in results)
    status = "INVALID_PRECONDITION" if not precondition else ("FAIL" if reproduced else "NOT_REPRODUCED")
    print(
        json.dumps(
            {
                "schema": "magicite/trust-mirror-diagnostic/2",
                "scenario": "admit -> revoke -> delete revoke mirror -> re-check (original S16 diagnostic)",
                "custody": "simulated fixture custodian via tests/support/custody_adapter.enroll_fixture; "
                "not a distinct-UID or installed-channel qualification",
                "variants": results,
                "precondition_admit_then_revoke_observed": precondition,
                "original_exploit_reproduced": reproduced,
                "status": status,
                "scope": "Disposable registries and custodians in temporary directories; checkout unchanged",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if status != "INVALID_PRECONDITION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
