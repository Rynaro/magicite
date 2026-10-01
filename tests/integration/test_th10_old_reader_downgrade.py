"""AC-TH-10 (as amended by TH-A01) — frozen 22ae4e0 reader and downgrade paths.

The pre-hardening tree is extracted with ``git archive`` and run in a separate
interpreter. That reader trusts JSON decision mirrors and, by default, admits
locally authored artifacts, so these nodes show the new authority format and the
supported runtime, not the old reader's policy, keep revoked content closed:

(a) new-format admit -> revoke: 22ae4e0 neither routes nor discloses (live and deleted DB);
(b) 22ae4e0 admit -> reviewed legacy migration -> review -> journal-only revoke: the stale admit
    mirror remains but the marked, re-digested target is refused (live and deleted DB);
(c1) documented ``migration restore`` stages inactive bytes and leaves the project closed;
(c2) hand-copied pre-migration backup: the supported runtime keeps the authenticated revoke and
     head and does not route the subject;
(c3) deleted local authority fails closed instead of resetting enrollment/history.

Disclosed residual (TH-A01, threat-model.md): running a pre-hardening binary against hand
rolled-back bytes can re-disclose revoked content. That binary never reads authenticated
custody; it is out of scope and deliberately not asserted here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import registry as registry_mod
from magicite.core import router as router_mod
from magicite.core import trust as trust_mod
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db as db_mod

pytestmark = pytest.mark.acceptance

OLD_COMMIT = "22ae4e01acde9e99e64c5a6c6fa0bcb6a625684c"
REPO = Path(__file__).resolve().parents[2]
TOY = REPO / "tests/fixtures/toy-registry/engrams"
SUBJECT = "proton-ge-proton-downgrade"
OTHER = "steam-prefix-access"
ENV = {"MAGICITE_EMBEDDING_PROVIDER": "hashing"}

OLD_SIDE = r'''
import hashlib, json, sys
from pathlib import Path
import magicite
from magicite.config import Config
from magicite.embeddings.hashing_provider import get_embedder
from magicite.storage import db
mode, root, names = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
cfg = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
out = {"magicite_file": magicite.__file__}
def err(exc):
    return type(exc).__name__ + ": " + str(exc)[:200]
if mode == "seed":
    from magicite.core import registry, trust
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    registry.register(cfg, conn, get_embedder(dim=256), path=".magicite/engrams")
    for name in names:
        row = conn.execute("SELECT id, content_sha256 FROM engram WHERE name=?", (name,)).fetchone()
        trust.approve(cfg, conn, engram_id=row["id"], expected_digest=row["content_sha256"], actor="old")
    conn.close()
    print(json.dumps(out)); sys.exit(0)
from magicite.core import registry
from magicite.mcp import bind_retrieval as br
from magicite.mcp.registry import ToolContext
from magicite.mcp.schemas import LoadSkillBodyInput, RouteInput
out["mirrors"] = sorted(p.name for p in (cfg.data_dir / "trust/decisions").glob("*.json"))
try:
    conn = db.connect(cfg.db_path)
except BaseException as exc:
    out["open_error"] = err(exc); print(json.dumps(out)); sys.exit(0)
emb = get_embedder(dim=256)
try:
    registry.sync(cfg, conn, emb); out["sync"] = "ok"
except BaseException as exc:
    out["sync"] = err(exc)
ctx = ToolContext(cfg=cfg, conn=conn, embedder=emb)
try:
    policy = br._active_policy_digest(cfg) or "none"
except BaseException:
    policy = "none"
out["subjects"] = {}
for name in names:
    item = out["subjects"][name] = {}
    try:
        raw = (cfg.registry_dir / (name + ".egr.md")).read_bytes()
        item["file_sha256"] = hashlib.sha256(raw).hexdigest()
        item["file_has_marker"] = b"magicite.trust_journal" in raw
        row = conn.execute("SELECT content_sha256 FROM engram WHERE name=?", (name,)).fetchone()
        digest = item["file_sha256"] if row is None else row["content_sha256"]
        body = br.load_skill_body(ctx, LoadSkillBodyInput(
            name=name, level="L2", expected_content_digest=digest, expected_policy_digest=policy))
        item["body_status"] = body.status
        item["body_reason_codes"] = list(body.reason_codes or [])
        item["body_disclosed"] = bool((body.procedure or "").strip())
    except BaseException as exc:
        item["error"] = err(exc); item["body_disclosed"] = False
try:
    data = br.route(ctx, RouteInput(query=names[0].replace("-", " "), k=10)).model_dump()
    out["routed_names"] = [c.get("name") for c in data.get("candidates") or []]
except BaseException as exc:
    out["route_error"] = err(exc); out["routed_names"] = []
conn.close()
print(json.dumps(out, default=str))
'''


@pytest.fixture(scope="module")
def old_src(tmp_path_factory) -> Path:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git executable unavailable: 22ae4e0 old-reader witness NOT run")
    probe = subprocess.run(
        [git, "-C", str(REPO), "cat-file", "-e", OLD_COMMIT + "^{commit}"], capture_output=True
    )
    if probe.returncode != 0:
        pytest.skip(f"commit {OLD_COMMIT[:7]} unavailable (shallow clone?): old-reader witness NOT run")
    archive = subprocess.run(
        [git, "-C", str(REPO), "archive", "--format=tar", OLD_COMMIT, "src"], check=True, capture_output=True
    ).stdout
    dest = tmp_path_factory.mktemp("old-22ae4e0")
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    (dest / "old_side.py").write_text(OLD_SIDE)
    return dest


def run_old(old_src: Path, mode: str, root: Path, *names: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "COV_", "COVERAGE"))}
    env.update(ENV, PYTHONPATH=str(old_src / "src"))
    done = subprocess.run(
        [sys.executable, "-B", str(old_src / "old_side.py"), mode, str(root), *names],
        env=env,
        cwd=old_src,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert Path(out["magicite_file"]).resolve().is_relative_to((old_src / "src").resolve()), out
    return out


def assert_old_closed(out: dict, name: str) -> None:
    if "open_error" in out:  # refusing to open the registry at all is closed
        return
    item = out["subjects"][name]
    assert item["file_has_marker"] is True, out
    assert item["body_disclosed"] is False, out
    # Closed by refusal (not indexed / not admitted), never by a broken probe.
    assert item.get("error", "NotFoundError").startswith("NotFoundError"), out
    assert "route_error" not in out, out
    assert name not in out["routed_names"], out


def latest(cfg: Config, engram_id: str) -> str:
    decision = trust_mod.latest_decision_for(cfg, engram_id)
    assert decision is not None
    return decision.decision


def revoke(cfg: Config, conn, name: str) -> str:
    row = conn.execute("SELECT id FROM engram WHERE name=?", (name,)).fetchone()
    registry_mod.review_revoke(cfg, conn, engram_id=row["id"], actor="th10-operator")
    return str(row["id"])


def drop_db(cfg: Config) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(cfg.db_path) + suffix).unlink(missing_ok=True)


def new_routes(cfg: Config, name: str) -> bool:
    conn = db_mod.connect(cfg.db_path)
    try:
        embedder = get_embedder(dim=256)
        registry_mod.sync(cfg, conn, embedder)
        outcome = router_mod.route(cfg, conn, embedder, query=name.replace("-", " "), k=10)
        return name in [candidate.name for candidate in outcome.candidates]
    finally:
        conn.close()


def test_old_reader_rejects_new_format_after_revoke(
    old_src, cfg, db_conn, embedder, review_fixture_artifacts
) -> None:
    """(a) New-format admit -> revoke; 22ae4e0 never routes or discloses it."""
    registry_mod.register(cfg, db_conn, embedder, path=".magicite/engrams")
    review_fixture_artifacts(SUBJECT, OTHER)
    engram_id = revoke(cfg, db_conn, SUBJECT)
    assert latest(cfg, engram_id) == "revoke"
    assert not list((cfg.data_dir / "trust/decisions").glob("*.json"))
    db_conn.commit()
    for phase in ("live-db", "db-deleted"):
        if phase == "db-deleted":
            db_conn.close()
            drop_db(cfg)
        out = run_old(old_src, "read", cfg.project_root, SUBJECT, OTHER)
        assert_old_closed(out, SUBJECT)
        # Unsupported reader rejects the new authority even for still-admitted content.
        assert_old_closed(out, OTHER)
    assert latest(cfg, engram_id) == "revoke"


def _legacy_project(old_src: Path, root: Path) -> Config:
    registry = root / ".magicite/engrams"
    registry.mkdir(parents=True)
    for source in TOY.glob("*.egr.md"):
        shutil.copy(source, registry / source.name)
    run_old(old_src, "seed", root, SUBJECT)
    control = run_old(old_src, "read", root, SUBJECT)
    # Positive control: on legacy bytes the frozen reader routes and discloses.
    assert control["subjects"][SUBJECT]["body_disclosed"] is True, control
    assert SUBJECT in control["routed_names"], control
    # 22ae4e0 implies the default policy without a file; the operator pins it before migration.
    (root / ".magicite/trust/policy.json").write_text(json.dumps(trust_mod.default_policy().to_dict()))
    return Config.load(root, env=ENV)


@pytest.fixture
def legacy_custody(tmp_path):
    from tests.support.custody_adapter import FixtureCustody

    provider = FixtureCustody(tmp_path / "custody", "th10-legacy")
    yield provider
    provider.close()


def _migrate_review_revoke(cfg: Config, provider, tmp_path: Path, monkeypatch) -> tuple[str, Path, str]:
    from magicite.core import trust_legacy, writer_guard
    from magicite.core.backup import _is_secret_rel

    original = writer_guard.resolve_custody
    root = cfg.project_root.resolve()
    monkeypatch.setattr(
        writer_guard,
        "resolve_custody",
        lambda c: (provider.registry_id, provider) if c.project_root.resolve() == root else original(c),
    )
    plan = trust_legacy.preview(cfg, registry_id=provider.registry_id, actor="th10-operator")
    reviewed = trust_legacy.digest(plan)
    # 22ae4e0 created runtime control keys; the operator supplies reviewed encrypted custody.
    encrypted = tmp_path / "operator-encrypted-custody"
    encrypted.write_bytes(b"opaque operator-managed encrypted artifact")
    custody = {
        "schema": "OperatorEncryptedCustody/1",
        "path": str(encrypted),
        "sha256": hashlib.sha256(encrypted.read_bytes()).hexdigest(),
        "source_sha256": {r: m["sha256"] for r, m in plan["files"].items() if _is_secret_rel(r)},
    }
    backup = tmp_path / "legacy-backup"
    trust_legacy.backup_reviewed(
        cfg, plan=plan, reviewed_sha256=reviewed, destination=backup, encrypted_custody=custody
    )
    applied = trust_legacy.apply_reviewed(cfg, backup_path=backup, reviewed_sha256=reviewed)
    assert applied["status"] == "complete_requires_target_review"
    conn = db_mod.connect(cfg.db_path)
    try:
        registry_mod.sync(cfg, conn, get_embedder(dim=256))
        row = conn.execute("SELECT id, content_sha256 FROM engram WHERE name=?", (SUBJECT,)).fetchone()
        registry_mod.review_approve(
            cfg, conn, engram_id=row["id"], expected_digest=row["content_sha256"], actor="th10-review"
        )
        assert latest(cfg, row["id"]) == "admit"
        engram_id = revoke(cfg, conn, SUBJECT)
    finally:
        conn.close()
    assert latest(cfg, engram_id) == "revoke"
    return engram_id, backup, reviewed


def test_old_reader_after_migration_and_journal_only_revoke(
    old_src, tmp_path, legacy_custody, monkeypatch
) -> None:
    """(b) Stale 22ae4e0 admit mirrors remain but stay inert against migrated bytes."""
    cfg = _legacy_project(old_src, tmp_path / "legacy")
    engram_id, _, _ = _migrate_review_revoke(cfg, legacy_custody, tmp_path, monkeypatch)
    for phase in ("live-db", "db-deleted"):
        if phase == "db-deleted":
            drop_db(cfg)
        out = run_old(old_src, "read", cfg.project_root, SUBJECT)
        assert out["mirrors"], out  # the pre-migration admit mirror is still in the old reader's path
        assert_old_closed(out, SUBJECT)
    assert latest(cfg, engram_id) == "revoke"


def test_downgrade_paths_preserve_revocation_and_enrollment(
    old_src, tmp_path, legacy_custody, monkeypatch
) -> None:
    """(c1) staged restore, (c2) hand rollback under the supported runtime, (c3) authority loss."""
    from magicite.core import migration as migration_mod
    from magicite.core import trust_legacy

    cfg = _legacy_project(old_src, tmp_path / "legacy")
    engram_id, backup, reviewed = _migrate_review_revoke(cfg, legacy_custody, tmp_path, monkeypatch)
    head = trust_mod.authenticated_snapshot(cfg).head

    def registry_bytes() -> dict[Path, bytes]:
        return {p: p.read_bytes() for p in cfg.registry_dir.rglob("*") if p.is_file()}

    before = registry_bytes()
    result = migration_mod.restore(
        cfg, backup_path=backup, reviewed_sha256=reviewed, staging_path=tmp_path / "inactive-stage"
    )
    assert result.state == "reconciliation_required"
    assert registry_bytes() == before
    assert_old_closed(run_old(old_src, "read", cfg.project_root, SUBJECT), SUBJECT)
    assert latest(cfg, engram_id) == "revoke"
    assert trust_mod.authenticated_snapshot(cfg).head == head

    # (c2) Out-of-band copy of the complete pre-migration backup over the active tree. The
    # derived DB is discarded (its copy carries a backup-time lease row) and rebuilt by the
    # supported runtime. Old-binary behaviour on these bytes is the TH-A01 residual.
    _, manifest = trust_legacy.read_verified_backup(backup, reviewed_sha256=reviewed)
    for rel in manifest["files"]:
        (cfg.data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup / "files" / rel, cfg.data_dir / rel)
    assert b"magicite.trust_journal" not in (cfg.registry_dir / f"{SUBJECT}.egr.md").read_bytes()
    drop_db(cfg)
    snapshot = trust_mod.authenticated_snapshot(cfg)
    assert snapshot.head == head
    assert snapshot.latest_by_engram[engram_id]["decision"] == "revoke"
    assert new_routes(cfg, SUBJECT) is False

    # (c3) Local authority deleted: custody refuses re-genesis instead of resetting history.
    shutil.rmtree(cfg.data_dir / "trust/authority")
    with pytest.raises(trust_mod.TrustLedgerCorruptError, match="reconciliation required"):
        trust_mod.authenticated_snapshot(cfg)
